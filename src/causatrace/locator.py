"""Static and runtime source location support for CausaTrace.

The locator deliberately has no dependency on the runner.  Static locations
come from a conservative AST pass and are *heuristics*: they describe code
that deserves inspection, not code that was shown to affect an output.  Runtime
locations are made from the event records emitted by the execution shim and
are marked separately so callers cannot accidentally present a static match as
runtime proof.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import sysconfig
from typing import Any, Iterable, Mapping, Sequence

from .models import Location, StaticFinding


# Rule ids are public so reports and future integrations do not need to copy
# string literals.  Keep these short and stable; the fact that a finding is
# static is represented by Location.kind == "static_rule".
RULE_UNORDERED_SET = "unordered_set"
RULE_PYTHON_RANDOM = "python_random"
RULE_NUMPY_DEFAULT_RNG = "numpy_default_rng"
RULE_FILESYSTEM_LISTING = "filesystem_listing"
RULE_SKLEARN_RANDOM_STATE = "sklearn_random_state"
RULE_ENVIRONMENT_READ = "environment_read"
RULE_IMPLICIT_LOCAL_TIME = "implicit_local_time"

STATIC_RULE_IDS = (
    RULE_UNORDERED_SET,
    RULE_PYTHON_RANDOM,
    RULE_NUMPY_DEFAULT_RNG,
    RULE_FILESYSTEM_LISTING,
    RULE_SKLEARN_RANDOM_STATE,
    RULE_ENVIRONMENT_READ,
    RULE_IMPLICIT_LOCAL_TIME,
)


_SKIP_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
}

_RANDOM_METHODS = {
    "random",
    "randint",
    "randrange",
    "choice",
    "choices",
    "sample",
    "shuffle",
    "getrandbits",
    "uniform",
    "triangular",
    "gauss",
    "normalvariate",
    "lognormvariate",
    "expovariate",
    "vonmisesvariate",
    "gammavariate",
    "betavariate",
    "paretovariate",
    "weibullvariate",
    "randbytes",
    "seed",
    "getstate",
    "setstate",
    "getrandbits",
    "Random",
    "SystemRandom",
}

_FILESYSTEM_METHODS = {
    "iterdir",
    "rglob",
    "glob",
    "walk",
}

_SKLEARN_ESTIMATORS = {
    # Ensemble and tree estimators.
    "AdaBoostClassifier",
    "AdaBoostRegressor",
    "BaggingClassifier",
    "BaggingRegressor",
    "ExtraTreesClassifier",
    "ExtraTreesRegressor",
    "GradientBoostingClassifier",
    "GradientBoostingRegressor",
    "HistGradientBoostingClassifier",
    "HistGradientBoostingRegressor",
    "IsolationForest",
    "RandomForestClassifier",
    "RandomForestRegressor",
    "RandomTreesEmbedding",
    "StackingClassifier",
    "StackingRegressor",
    "VotingClassifier",
    "VotingRegressor",
    "DecisionTreeClassifier",
    "DecisionTreeRegressor",
    "ExtraTreeClassifier",
    "ExtraTreeRegressor",
    # Neighbors, neural networks, and linear models.
    "KNeighborsClassifier",
    "KNeighborsRegressor",
    "RadiusNeighborsClassifier",
    "RadiusNeighborsRegressor",
    "MLPClassifier",
    "MLPRegressor",
    "Perceptron",
    "SGDClassifier",
    "SGDRegressor",
    # Clustering and mixture estimators.
    "Birch",
    "DBSCAN",
    "FeatureAgglomeration",
    "KMeans",
    "MiniBatchKMeans",
    "MeanShift",
    "OPTICS",
    "SpectralClustering",
    "GaussianMixture",
    "BayesianGaussianMixture",
    # Model selection / wrappers whose random state is a meaningful control.
    "RandomizedSearchCV",
    "RandomizedSearchCV",
    "HalvingRandomSearchCV",
}

_SKLEARN_NON_ESTIMATOR_NAMES = {
    "BaseEstimator",
    "Pipeline",
    "FeatureUnion",
    "ColumnTransformer",
    "GridSearchCV",
    "ParameterGrid",
    "ParameterSampler",
    "StandardScaler",
    "MinMaxScaler",
    "MaxAbsScaler",
    "RobustScaler",
    "Normalizer",
    "OneHotEncoder",
    "OrdinalEncoder",
    "LabelEncoder",
    "PCA",
    "TruncatedSVD",
}


def _dotted_parts(node: ast.AST) -> tuple[str, ...]:
    """Return a dotted expression as names, or ``()`` for other AST nodes."""

    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        prefix = _dotted_parts(node.value)
        return (*prefix, node.attr) if prefix else ()
    return ()


def _source_symbol(node: ast.AST) -> str | None:
    try:
        return ast.unparse(node)
    except (AttributeError, TypeError):
        return None


def _constant_none(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _has_keyword(node: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in node.keywords)


def _is_explicit_timezone(node: ast.Call) -> bool:
    """Whether a datetime call supplies a real timezone argument."""

    if _has_keyword(node, "tz"):
        keyword = next(keyword for keyword in node.keywords if keyword.arg == "tz")
        return not _constant_none(keyword.value)
    if len(node.args) >= 2:
        return not _constant_none(node.args[1])
    if node.args:
        return not _constant_none(node.args[0])
    return False


@dataclass(frozen=True)
class _Frame:
    path: str
    line: int | None = None
    column: int | None = None
    kind: str | None = None
    symbol: str | None = None
    operation: str | None = None
    evidence: str | None = None


class _StaticVisitor(ast.NodeVisitor):
    """AST visitor implementing the intentionally heuristic P0 rules."""

    def __init__(self, path: Path, source: str, tree: ast.AST) -> None:
        self.path = path
        self.source = source
        self.lines = source.splitlines()
        self.tree = tree
        self.findings: list[StaticFinding] = []
        self._seen: set[tuple[str, int, int]] = set()
        self._parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self._parents[child] = parent

        self.random_modules: set[str] = set()
        self.random_functions: set[str] = set()
        self.numpy_modules: set[str] = set()
        self.numpy_random_modules: set[str] = set()
        self.default_rng_names: set[str] = set()
        self.os_modules: set[str] = set()
        self.environ_names: set[str] = set()
        self.getenv_names: set[str] = set()
        self.listdir_names: set[str] = set()
        self.scandir_names: set[str] = set()
        self.pathlib_modules: set[str] = set()
        self.path_names: set[str] = set()
        self.glob_modules: set[str] = set()
        self.glob_functions: set[str] = set()
        self.datetime_modules: set[str] = set()
        self.datetime_names: set[str] = set()
        self.date_names: set[str] = set()
        self.time_modules: set[str] = set()
        # Alias -> original full sklearn import path, or module aliases.
        self.sklearn_names: dict[str, str] = {}
        self.sklearn_modules: dict[str, str] = {}
        self.unordered_names: set[str] = set()

        self._collect_imports()
        self._collect_unordered_assignments()

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if alias.name == "random" or alias.name.startswith("random."):
                        self.random_modules.add(bound)
                    if alias.name == "numpy":
                        self.numpy_modules.add(bound)
                    elif alias.name == "numpy.random":
                        self.numpy_random_modules.add(bound)
                    if alias.name == "os":
                        self.os_modules.add(bound)
                    if alias.name == "pathlib":
                        self.pathlib_modules.add(bound)
                    if alias.name == "glob":
                        self.glob_modules.add(bound)
                    if alias.name == "datetime":
                        self.datetime_modules.add(bound)
                    if alias.name == "time":
                        self.time_modules.add(bound)
                    if alias.name == "sklearn" or alias.name.startswith("sklearn."):
                        self.sklearn_modules[bound] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    bound = alias.asname or alias.name
                    original = alias.name
                    if module == "random":
                        self.random_functions.add(bound)
                    if module == "numpy.random" and original == "default_rng":
                        self.default_rng_names.add(bound)
                    if module == "os":
                        if original == "environ":
                            self.environ_names.add(bound)
                        elif original == "getenv":
                            self.getenv_names.add(bound)
                        elif original == "listdir":
                            self.listdir_names.add(bound)
                        elif original == "scandir":
                            self.scandir_names.add(bound)
                    if module == "pathlib" and original == "Path":
                        self.path_names.add(bound)
                    if module == "glob" and original in {"glob", "iglob"}:
                        self.glob_functions.add(bound)
                    if module == "datetime":
                        if original == "datetime":
                            self.datetime_names.add(bound)
                        elif original == "date":
                            self.date_names.add(bound)
                    if module == "time":
                        self.time_modules.add(bound)
                    if module.startswith("sklearn"):
                        self.sklearn_names[bound] = f"{module}.{original}"

    def _collect_unordered_assignments(self) -> None:
        for node in ast.walk(self.tree):
            value: ast.AST | None = None
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            else:
                continue
            if value is None or not self._is_unordered_expression(value):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    self.unordered_names.add(target.id)

    def _is_unordered_expression(self, node: ast.AST) -> bool:
        if isinstance(node, (ast.Set, ast.SetComp)):
            return True
        if isinstance(node, ast.Name):
            return node.id in self.unordered_names
        if isinstance(node, ast.Call):
            parts = _dotted_parts(node.func)
            if parts and parts[-1] in {"set", "frozenset"} and len(parts) == 1:
                return True
            if parts and parts[-1] in {"set", "frozenset"}:
                # ``builtins.set`` and an imported alias are still useful
                # hints, while arbitrary ``thing.set()`` is not.
                return parts[-1] == "set" and parts[0] == "builtins"
        return False

    def _evidence(self, node: ast.AST) -> str | None:
        segment = ast.get_source_segment(self.source, node)
        if segment:
            return segment.strip()
        line = getattr(node, "lineno", 0)
        if line and line <= len(self.lines):
            return self.lines[line - 1].strip()
        return None

    def _add(
        self,
        rule_id: str,
        node: ast.AST,
        message: str,
        suggestion: str,
        *,
        operation: str | None = None,
        symbol: str | None = None,
    ) -> None:
        line = int(getattr(node, "lineno", 1))
        column = int(getattr(node, "col_offset", 0))
        key = (rule_id, line, column)
        if key in self._seen:
            return
        self._seen.add(key)
        location = Location(
            path=str(self.path),
            line=line,
            column=column,
            kind="static_rule",
            operation=operation or rule_id,
            symbol=symbol or _source_symbol(node),
            confidence="heuristic",
            evidence=self._evidence(node),
        )
        self.findings.append(
            StaticFinding(
                rule_id=rule_id,
                message=f"Heuristic: {message}",
                location=location,
                suggestion=suggestion,
            )
        )

    def _is_random_call(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        if len(parts) == 1:
            return parts[0] in self.random_functions
        return parts[-1] in _RANDOM_METHODS and (
            parts[-2] in self.random_modules
            or parts[-2] == "random" and parts[-3:-2] == ("numpy",)
        )

    def _is_default_rng_call_without_seed(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        if len(parts) == 1:
            imported = parts[0] in self.default_rng_names
        else:
            imported = (
                parts[-1] == "default_rng"
                and (
                    parts[-2] in self.numpy_random_modules
                    or len(parts) >= 3
                    and parts[-3] in self.numpy_modules
                    and parts[-2] == "random"
                )
            )
        if not imported:
            return False
        if not node.args and not node.keywords:
            return True
        if node.args and _constant_none(node.args[0]):
            return True
        keyword = next((item for item in node.keywords if item.arg == "seed"), None)
        return keyword is not None and _constant_none(keyword.value)

    def _is_filesystem_call(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if isinstance(node.func, ast.Attribute) and node.func.attr in _FILESYSTEM_METHODS:
            # This includes ``Path('.').iterdir()`` where the receiver is a
            # call and therefore cannot be represented as dotted parts.
            return True
        if not parts:
            return False
        if len(parts) == 1:
            return parts[0] in self.listdir_names or parts[0] in self.scandir_names or parts[0] in self.glob_functions
        if parts[-1] in {"listdir", "scandir", "walk", "fwalk"} and parts[-2] in self.os_modules:
            return True
        if parts[-1] in {"glob", "iglob"} and parts[-2] in self.glob_modules:
            return True
        # Path.iterdir/glob/rglob are intentionally heuristic.  A method with
        # these names on a project type has the same ordering concern.
        return parts[-1] in _FILESYSTEM_METHODS

    def _is_environment_access(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Subscript):
            parts = _dotted_parts(node.value)
            return len(parts) >= 2 and parts[-1] == "environ" and parts[-2] in self.os_modules or (
                isinstance(node.value, ast.Name) and node.value.id in self.environ_names
            )
        if isinstance(node, ast.Name):
            return node.id in self.environ_names
        if not isinstance(node, ast.Call):
            return False
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        if len(parts) == 1:
            return parts[0] in self.getenv_names
        if len(parts) >= 2 and parts[-1] == "getenv" and parts[-2] in self.os_modules:
            return True
        if parts[-1] in {"get", "keys", "items", "values"}:
            if len(parts) >= 2 and parts[-2] in self.environ_names:
                return True
            if len(parts) >= 3 and parts[-2] == "environ" and parts[-3] in self.os_modules:
                return True
        return False

    def _is_implicit_local_time(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        method = parts[-1]
        if len(parts) == 1:
            if parts[0] in self.datetime_names and method in {"now", "today", "utcnow"}:
                return not _is_explicit_timezone(node)
            if parts[0] in self.date_names and method == "today":
                return True
            if parts[0] in self.time_modules and parts[0] in {"localtime", "mktime", "ctime", "strftime"}:
                return True
            return False
        if parts[-2] in self.time_modules and method in {"localtime", "mktime", "ctime", "strftime"}:
            return True
        if (
            len(parts) >= 3
            and parts[-3] in self.datetime_modules
            and parts[-2] in {"datetime", "date"}
            and method in {"now", "today", "utcnow", "fromtimestamp"}
        ):
            return method in {"today", "utcnow"} or not _is_explicit_timezone(node)
        if parts[-2] in self.datetime_modules and method in {"now", "today", "utcnow", "fromtimestamp"}:
            return method in {"today", "utcnow"} or not _is_explicit_timezone(node)
        if parts[-2] in self.datetime_names and method in {"now", "today", "utcnow", "fromtimestamp"}:
            return method in {"today", "utcnow"} or not _is_explicit_timezone(node)
        if parts[-2] in self.date_names and method == "today":
            return True
        return False

    def _is_sklearn_estimator(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        final = parts[-1]
        original: str | None = None
        if len(parts) == 1:
            original = self.sklearn_names.get(final)
        elif parts[-2] in self.sklearn_modules:
            original = f"{self.sklearn_modules[parts[-2]]}.{final}"
        elif parts[0] in self.sklearn_modules and self.sklearn_modules[parts[0]] == "sklearn":
            original = ".".join(parts)
        elif parts[0] == "sklearn":
            original = ".".join(parts)
        if original is None:
            return False
        class_name = original.rsplit(".", 1)[-1]
        if class_name in _SKLEARN_NON_ESTIMATOR_NAMES:
            return False
        # Explicit names cover the common APIs.  The suffix fallback supports
        # newly added sklearn estimators without importing sklearn at analysis
        # time (the locator is intended to work offline).
        looks_like_estimator = class_name in _SKLEARN_ESTIMATORS or class_name.endswith(
            ("Classifier", "Regressor", "Clustering", "Mixture", "Forest")
        )
        if not looks_like_estimator:
            return False
        return not _has_keyword(node, "random_state")

    def visit_Call(self, node: ast.Call) -> Any:  # noqa: N802 - AST API
        if self._is_unordered_expression(node) and not self._inside_order_preserving_call(node):
            self._add(
                RULE_UNORDERED_SET,
                node,
                "a set-derived value may expose process-dependent iteration order",
                "Sort the values before consuming or serializing them when order is part of the result.",
                operation="set-iteration",
            )
        if self._is_random_call(node):
            self._add(
                RULE_PYTHON_RANDOM,
                node,
                "Python random state is read or changed without a locator-level seed guarantee",
                "Pass and record an explicit seed (or inject a seeded random.Random instance).",
                operation="python-random",
            )
        if self._is_default_rng_call_without_seed(node):
            self._add(
                RULE_NUMPY_DEFAULT_RNG,
                node,
                "numpy.random.default_rng is created without a seed",
                "Provide a stable seed to default_rng and record it with the run profile.",
                operation="numpy.random.default_rng",
            )
        if self._is_filesystem_call(node):
            self._add(
                RULE_FILESYSTEM_LISTING,
                node,
                "filesystem entries are listed without an explicit ordering step",
                "Sort directory entries before using them to construct output or model input.",
                operation="filesystem-listing",
            )
        if self._is_sklearn_estimator(node):
            self._add(
                RULE_SKLEARN_RANDOM_STATE,
                node,
                "a scikit-learn estimator appears to omit random_state",
                "Set random_state explicitly where the estimator supports it and record the value.",
                operation="sklearn-estimator",
            )
        if self._is_environment_access(node):
            self._add(
                RULE_ENVIRONMENT_READ,
                node,
                "the process environment is read and can vary between runs",
                "Declare the environment input in the execution profile or provide a stable default.",
                operation="environment-read",
            )
        if self._is_implicit_local_time(node):
            self._add(
                RULE_IMPLICIT_LOCAL_TIME,
                node,
                "local or naive wall-clock time is used without an explicit timezone",
                "Use an explicit timezone-aware clock and record the timezone in the run profile.",
                operation="local-time",
            )
        self.generic_visit(node)

    def _inside_order_preserving_call(self, node: ast.AST) -> bool:
        parent = self._parents.get(node)
        if not isinstance(parent, ast.Call):
            return False
        parts = _dotted_parts(parent.func)
        return bool(parts and parts[-1] in {"sorted", "min", "max", "len"})

    def visit_Set(self, node: ast.Set) -> Any:  # noqa: N802 - AST API
        if not self._inside_order_preserving_call(node):
            self._add(
                RULE_UNORDERED_SET,
                node,
                "a set literal is unordered when consumed as a value",
                "Sort the values before consuming or serializing them when order is part of the result.",
                operation="set-iteration",
            )
        self.generic_visit(node)

    def visit_SetComp(self, node: ast.SetComp) -> Any:  # noqa: N802 - AST API
        if not self._inside_order_preserving_call(node):
            self._add(
                RULE_UNORDERED_SET,
                node,
                "a set comprehension creates an unordered value",
                "Sort the values before consuming or serializing them when order is part of the result.",
                operation="set-iteration",
            )
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> Any:  # noqa: N802 - AST API
        if self._is_unordered_expression(node.iter):
            self._add(
                RULE_UNORDERED_SET,
                node.iter,
                "a loop consumes a set-derived value whose order is not defined",
                "Sort the iterable before iterating when order affects the result.",
                operation="set-iteration",
            )
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_Subscript(self, node: ast.Subscript) -> Any:  # noqa: N802 - AST API
        if self._is_environment_access(node):
            self._add(
                RULE_ENVIRONMENT_READ,
                node,
                "the process environment is read and can vary between runs",
                "Declare the environment input in the execution profile or provide a stable default.",
                operation="environment-read",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> Any:  # noqa: N802 - AST API
        # Catch a direct ``print(os.environ)`` read while avoiding a duplicate
        # finding for the more specific ``os.environ.get(...)`` and
        # ``os.environ[...]`` visitors.
        parts = _dotted_parts(node)
        parent = self._parents.get(node)
        if (
            len(parts) >= 2
            and parts[-1] == "environ"
            and parts[-2] in self.os_modules
            and not isinstance(parent, (ast.Attribute, ast.Subscript))
        ):
            self._add(
                RULE_ENVIRONMENT_READ,
                node,
                "the process environment is read and can vary between runs",
                "Declare the environment input in the execution profile or provide a stable default.",
                operation="environment-read",
            )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> Any:  # noqa: N802 - AST API
        if node.id in self.environ_names:
            parent = self._parents.get(node)
            if not isinstance(parent, (ast.Attribute, ast.Subscript)):
                self._add(
                    RULE_ENVIRONMENT_READ,
                    node,
                    "the process environment is read and can vary between runs",
                    "Declare the environment input in the execution profile or provide a stable default.",
                    operation="environment-read",
                )
        self.generic_visit(node)


class SourceLocator:
    """Locate P0 static heuristics and classify runtime trace locations.

    ``project_root`` is optional for protocol compatibility.  Supplying it is
    recommended for :meth:`find_runtime`, because it lets the locator prefer a
    project frame over a dependency or shim frame in an event stack.
    """

    def __init__(self, project_root: str | os.PathLike[str] | None = None) -> None:
        self.project_root = Path(project_root).resolve() if project_root is not None else None

    def find_static(self, root: Path | str) -> tuple[StaticFinding, ...]:
        """Return heuristic findings for Python files below ``root``.

        Syntax errors and unreadable files are skipped: a locator should not
        prevent the runtime experiment from producing evidence for the files
        that can be analyzed.
        """

        root_path = Path(root)
        if root_path.is_file():
            paths = (root_path,)
        elif root_path.is_dir():
            paths = tuple(
                sorted(
                    path
                    for path in root_path.rglob("*.py")
                    if not any(part in _SKIP_DIRS for part in path.parts)
                )
            )
        else:
            return ()
        findings: list[StaticFinding] = []
        for path in paths:
            try:
                source = path.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(path))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            visitor = _StaticVisitor(path, source, tree)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        findings.sort(
            key=lambda finding: (
                finding.location.path,
                finding.location.line,
                finding.location.column or 0,
                finding.rule_id,
            )
        )
        return tuple(findings)

    def find_runtime(
        self,
        events: tuple[dict, ...] | Sequence[Mapping[str, Any]],
        project_root: str | os.PathLike[str] | None = None,
    ) -> tuple[Location, ...]:
        """Map shim event records to classified runtime locations.

        Events may carry ``frames``, ``stack`` or ``call_stack`` in addition to
        the top-level ``path``/``line`` fields.  A frame under the project root
        is preferred regardless of its position in the supplied stack.  This
        turns a wrapper/dependency event into the user's call site when that
        information is available while retaining dependency and stdlib events
        when no project frame exists.
        """

        root = (
            Path(project_root).resolve()
            if project_root is not None
            else self.project_root
        )
        locations: list[Location] = []
        seen: set[tuple[str, int | None, str, str | None]] = set()
        for event in events:
            if not isinstance(event, Mapping):
                continue
            frames = self._event_frames(event)
            if not frames:
                continue
            selected = self._select_frame(frames, event, root)
            if selected is None:
                continue
            kind = self._runtime_kind(selected, event, root)
            operation = str(event.get("operation") or selected.operation or "runtime-event")
            symbol = event.get("symbol") or selected.symbol
            evidence = event.get("evidence") or selected.evidence
            key = (selected.path, selected.line, kind, operation)
            if key in seen:
                continue
            seen.add(key)
            confidence = "heuristic" if kind == "static_rule" else "runtime"
            locations.append(
                Location(
                    path=selected.path,
                    line=selected.line or 0,
                    column=selected.column,
                    kind=kind,
                    operation=operation,
                    symbol=str(symbol) if symbol is not None else None,
                    confidence=confidence,
                    evidence=str(evidence) if evidence is not None else None,
                )
            )
        return tuple(locations)

    @staticmethod
    def _mapping_frame(value: Mapping[str, Any]) -> _Frame | None:
        nested = value.get("location")
        if isinstance(nested, Mapping):
            merged = dict(nested)
            merged.update({key: item for key, item in value.items() if key not in {"location"}})
            value = merged
        path_value: Any = None
        for key in ("path", "file", "filename", "source_path", "code_filename"):
            if value.get(key) is not None:
                path_value = value[key]
                break
        if path_value is None:
            return None
        path = str(path_value)
        line_value = next(
            (value.get(key) for key in ("line", "lineno", "line_number") if value.get(key) is not None),
            None,
        )
        try:
            line = int(line_value) if line_value is not None else None
        except (TypeError, ValueError):
            line = None
        column_value = next(
            (value.get(key) for key in ("column", "col", "col_offset") if value.get(key) is not None),
            None,
        )
        try:
            column = int(column_value) if column_value is not None else None
        except (TypeError, ValueError):
            column = None
        return _Frame(
            path=path,
            line=line,
            column=column,
            kind=str(value.get("kind")) if value.get("kind") is not None else None,
            symbol=str(value.get("symbol")) if value.get("symbol") is not None else None,
            operation=str(value.get("operation")) if value.get("operation") is not None else None,
            evidence=str(value.get("evidence")) if value.get("evidence") is not None else None,
        )

    @classmethod
    def _event_frames(cls, event: Mapping[str, Any]) -> list[_Frame]:
        frames: list[_Frame] = []
        # The event's direct path is the best fallback for older shim records.
        direct = cls._mapping_frame(event)
        if direct is not None:
            frames.append(direct)
        for key in ("frames", "stack", "call_stack", "traceback"):
            value = event.get(key)
            if isinstance(value, Mapping):
                value = [value]
            if not isinstance(value, (list, tuple)):
                continue
            for item in value:
                if isinstance(item, Mapping):
                    frame = cls._mapping_frame(item)
                    if frame is not None:
                        frames.append(frame)
                elif isinstance(item, str):
                    frames.append(_Frame(path=item))
        # A nested singular frame is also accepted by integrations that emit
        # one frame rather than a list.
        value = event.get("frame")
        if isinstance(value, Mapping):
            frame = cls._mapping_frame(value)
            if frame is not None:
                frames.append(frame)
        return frames

    @staticmethod
    def _path_is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    @classmethod
    def _normalise_path(cls, path: str, root: Path | None) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute() and root is not None:
            candidate = root / candidate
        try:
            return candidate.resolve()
        except OSError:
            return candidate.absolute()

    @classmethod
    def _is_dependency_path(cls, path: Path) -> bool:
        return any(part in {"site-packages", "dist-packages", ".venv", "venv"} for part in path.parts)

    @classmethod
    def _is_stdlib_path(cls, path: Path) -> bool:
        if cls._is_dependency_path(path):
            return False
        roots: list[Path] = []
        try:
            roots.append(Path(sysconfig.get_paths().get("stdlib", "")))
        except (AttributeError, TypeError):
            pass
        if sys.base_prefix:
            roots.append(Path(sys.base_prefix) / "lib")
        for root in roots:
            if str(root) and cls._path_is_under(path, root):
                return True
        return False

    @classmethod
    def _select_frame(
        cls,
        frames: list[_Frame],
        event: Mapping[str, Any],
        root: Path | None,
    ) -> _Frame | None:
        if root is not None:
            for frame in frames:
                path = cls._normalise_path(frame.path, root)
                if cls._path_is_under(path, root) and not cls._is_dependency_path(path):
                    return frame
        # Direct event fields are intentionally first in _event_frames; if no
        # project frame exists, retain the operation's own dependency/stdlib
        # location rather than choosing an arbitrary stack frame.
        return frames[0] if frames else None

    @classmethod
    def _runtime_kind(
        cls,
        frame: _Frame,
        event: Mapping[str, Any],
        root: Path | None,
    ) -> str:
        explicit = str(frame.kind or event.get("kind") or "").lower()
        if explicit in {"static", "static_rule", "heuristic", "static-rule"}:
            return "static_rule"
        path = cls._normalise_path(frame.path, root)
        if root is not None and cls._path_is_under(path, root) and not cls._is_dependency_path(path):
            return "runtime_user"
        if explicit in {"runtime_user", "user", "project"} and root is None:
            return "runtime_user"
        if explicit in {"runtime_stdlib", "stdlib", "standard_library", "standard-library"}:
            return "runtime_stdlib"
        if explicit in {"runtime_dependency", "dependency", "third_party", "third-party"}:
            return "runtime_dependency"
        if cls._is_stdlib_path(path):
            return "runtime_stdlib"
        return "runtime_dependency"


# Names used by callers in different milestones.  They all implement the
# same small Locator protocol and intentionally point to one implementation.
ASTLocator = SourceLocator
DefaultLocator = SourceLocator
CausaTraceLocator = SourceLocator
Locator = SourceLocator


def find_static(root: Path | str) -> tuple[StaticFinding, ...]:
    """Convenience wrapper around :class:`SourceLocator`."""

    return SourceLocator(root if Path(root).is_dir() else None).find_static(root)


def find_runtime(
    events: tuple[dict, ...] | Sequence[Mapping[str, Any]],
    project_root: Path | str | None = None,
) -> tuple[Location, ...]:
    """Convenience wrapper around :class:`SourceLocator.find_runtime`."""

    return SourceLocator(project_root).find_runtime(events)


__all__ = [
    "ASTLocator",
    "CausaTraceLocator",
    "DefaultLocator",
    "Locator",
    "RULE_ENVIRONMENT_READ",
    "RULE_FILESYSTEM_LISTING",
    "RULE_IMPLICIT_LOCAL_TIME",
    "RULE_NUMPY_DEFAULT_RNG",
    "RULE_PYTHON_RANDOM",
    "RULE_SKLEARN_RANDOM_STATE",
    "RULE_UNORDERED_SET",
    "STATIC_RULE_IDS",
    "SourceLocator",
    "find_runtime",
    "find_static",
]
