"""Diff-first, source-preserving P1 repair previews.

The P0 locator deliberately reports plain-text suggestions.  This module is
the opt-in P1 layer for a small set of repairs that have an unambiguous source
rewrite.  It accepts source text and returns a preview; it never opens or
writes a source file.

The implementation uses :mod:`ast` only for recognition and source spans.
Replacements are applied to the original text, which keeps comments,
formatting, and code outside the selected expression intact.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import difflib
from typing import Iterable

import libcst as cst


_SKLEARN_ESTIMATORS = {
    "AdaBoostClassifier",
    "AdaBoostRegressor",
    "BaggingClassifier",
    "BaggingRegressor",
    "BayesianGaussianMixture",
    "Birch",
    "DBSCAN",
    "DecisionTreeClassifier",
    "DecisionTreeRegressor",
    "ExtraTreeClassifier",
    "ExtraTreeRegressor",
    "ExtraTreesClassifier",
    "ExtraTreesRegressor",
    "FeatureAgglomeration",
    "GaussianMixture",
    "GradientBoostingClassifier",
    "GradientBoostingRegressor",
    "HalvingRandomSearchCV",
    "HistGradientBoostingClassifier",
    "HistGradientBoostingRegressor",
    "IsolationForest",
    "KMeans",
    "KNeighborsClassifier",
    "KNeighborsRegressor",
    "MLPClassifier",
    "MLPRegressor",
    "MeanShift",
    "MiniBatchKMeans",
    "OPTICS",
    "Perceptron",
    "RandomForestClassifier",
    "RandomForestRegressor",
    "RandomizedSearchCV",
    "RandomTreesEmbedding",
    "RadiusNeighborsClassifier",
    "RadiusNeighborsRegressor",
    "SGDClassifier",
    "SGDRegressor",
    "SpectralClustering",
    "StackingClassifier",
    "StackingRegressor",
    "VotingClassifier",
    "VotingRegressor",
}

_SKLEARN_NON_ESTIMATORS = {
    "BaseEstimator",
    "ColumnTransformer",
    "FeatureUnion",
    "GridSearchCV",
    "LabelEncoder",
    "MaxAbsScaler",
    "MinMaxScaler",
    "Normalizer",
    "OneHotEncoder",
    "OrdinalEncoder",
    "PCA",
    "ParameterGrid",
    "ParameterSampler",
    "Pipeline",
    "RobustScaler",
    "StandardScaler",
    "TruncatedSVD",
}

_PATH_LISTING_METHODS = {"glob", "iterdir", "rglob"}
_SKLEARN_RANDOM_STATE = "random_state=0"


@dataclass(frozen=True, slots=True)
class FixResult:
    """A non-writing repair preview.

    ``unified_diff`` is empty when ``changed`` is false.  ``warnings`` only
    contains parser or safety messages; ordinary P0 suggestions remain plain
    text in the existing locator/reporting flow.
    """

    changed: bool
    unified_diff: str
    warnings: tuple[str, ...]
    transformed_source: str


# The descriptive alias is useful to callers that think of this as a preview.
FixPreview = FixResult


@dataclass(frozen=True, slots=True)
class _Edit:
    start: int
    end: int
    replacement: str
    rule: str


def _dotted_parts(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        prefix = _dotted_parts(node.value)
        return (*prefix, node.attr) if prefix else ()
    return ()


def _line_starts(source: str) -> list[int]:
    starts = [0]
    for index, character in enumerate(source):
        if character == "\n":
            starts.append(index + 1)
    return starts


def _byte_column_as_char_count(line: str, column: int) -> int:
    """Convert AST's UTF-8 byte column into a Python string offset."""

    try:
        return len(line.encode("utf-8")[:column].decode("utf-8"))
    except UnicodeDecodeError:
        # CPython normally reports a code-point boundary.  Keeping this
        # defensive fallback makes malformed third-party ASTs harmless.
        return len(line.encode("utf-8")[:column].decode("utf-8", errors="ignore"))


def _span(source: str, starts: list[int], node: ast.AST) -> tuple[int, int]:
    line = getattr(node, "lineno", 1)
    end_line = getattr(node, "end_lineno", line)
    column = getattr(node, "col_offset", 0)
    end_column = getattr(node, "end_col_offset", column)
    lines = source.splitlines(keepends=True)
    start_line = lines[line - 1] if line - 1 < len(lines) else ""
    finish_line = lines[end_line - 1] if end_line - 1 < len(lines) else ""
    start = starts[line - 1] + _byte_column_as_char_count(start_line, column)
    end = starts[end_line - 1] + _byte_column_as_char_count(finish_line, end_column)
    return start, end


def _source(source: str, starts: list[int], node: ast.AST) -> str:
    start, end = _span(source, starts, node)
    return source[start:end]


def _has_starred_arguments(call: ast.Call) -> bool:
    return any(isinstance(argument, ast.Starred) for argument in call.args) or any(
        keyword.arg is None for keyword in call.keywords
    )


def _is_named_call(node: ast.Call, names: set[str]) -> bool:
    parts = _dotted_parts(node.func)
    return len(parts) == 1 and parts[0] in names


def _immediate_sorted_parent(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    parent = parents.get(node)
    if not isinstance(parent, ast.Call) or not parent.args:
        return False
    parts = _dotted_parts(parent.func)
    return parts == ("sorted",) and parent.args[0] is node and len(parent.args) == 1


def _append_argument(call_text: str, argument: str, *, has_arguments: bool) -> str:
    """Append one argument while preserving the call's one-line/multiline form."""

    close = call_text.rfind(")")
    if close < 0:
        return call_text
    before = call_text[:close]
    after = call_text[close:]
    if "\n" not in call_text:
        separator = ", " if has_arguments else ""
        return f"{before}{separator}{argument}{after}"

    # The closing parenthesis's indentation is the indentation immediately
    # before it.  Add four spaces for a new argument line, matching common
    # Python formatting without reformatting any existing text.
    closing_indent = before.rsplit("\n", 1)[-1]
    base = before.rstrip()
    argument_indent = f"{closing_indent}    "
    if has_arguments:
        comma = "" if base.endswith(",") else ","
        return f"{base}{comma}\n{argument_indent}{argument},\n{closing_indent}{after}"
    return f"{base}\n{argument_indent}{argument},\n{closing_indent}{after}"


class _RepairScanner:
    def __init__(self, source: str, tree: ast.AST) -> None:
        self.source = source
        self.tree = tree
        self.starts = _line_starts(source)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent

        self.os_modules: set[str] = set()
        self.listdir_names: set[str] = set()
        self.glob_modules: set[str] = set()
        self.glob_names: set[str] = set()
        self.pathlib_modules: set[str] = set()
        self.path_names: set[str] = set()
        self.numpy_modules: set[str] = set()
        self.numpy_random_modules: set[str] = set()
        self.default_rng_names: set[str] = set()
        self.sklearn_names: dict[str, str] = {}
        self.sklearn_modules: dict[str, str] = {}
        self.set_names: set[str] = set()
        self._collect_imports()
        self._collect_set_assignments()

    def _collect_imports(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if alias.name == "os":
                        self.os_modules.add(bound)
                    elif alias.name == "glob":
                        self.glob_modules.add(bound)
                    elif alias.name == "pathlib":
                        self.pathlib_modules.add(bound)
                    elif alias.name == "numpy":
                        self.numpy_modules.add(bound)
                    elif alias.name == "numpy.random":
                        self.numpy_random_modules.add(bound)
                    if alias.name == "sklearn" or alias.name.startswith("sklearn."):
                        self.sklearn_modules[bound] = alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    bound = alias.asname or alias.name
                    if module == "os" and alias.name == "listdir":
                        self.listdir_names.add(bound)
                    elif module == "glob" and alias.name in {"glob", "iglob"}:
                        self.glob_names.add(bound)
                    elif module == "pathlib" and alias.name == "Path":
                        self.path_names.add(bound)
                    elif module == "numpy.random" and alias.name == "default_rng":
                        self.default_rng_names.add(bound)
                    if module == "numpy" and alias.name == "random":
                        self.numpy_random_modules.add(bound)
                    if module.startswith("sklearn"):
                        self.sklearn_names[bound] = f"{module}.{alias.name}"
                    if module == "sklearn" and alias.name in {"ensemble", "cluster", "linear_model", "neighbors", "mixture", "model_selection"}:
                        self.sklearn_modules[bound] = f"sklearn.{alias.name}"

    def _collect_set_assignments(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            else:
                continue
            if not self._is_set_expression(value):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    self.set_names.add(target.id)

    def _is_set_expression(self, node: ast.AST) -> bool:
        if isinstance(node, (ast.Set, ast.SetComp)):
            return True
        if isinstance(node, ast.Name):
            return node.id in self.set_names
        if isinstance(node, ast.Call):
            parts = _dotted_parts(node.func)
            return len(parts) == 1 and parts[0] in {"set", "frozenset"} and not _has_starred_arguments(node)
        return False

    def _is_default_rng(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if len(parts) == 1:
            return parts[0] in self.default_rng_names
        if not parts or parts[-1] != "default_rng":
            return False
        if len(parts) >= 3 and parts[-3] in self.numpy_modules and parts[-2] == "random":
            return True
        return len(parts) >= 2 and parts[-2] in self.numpy_random_modules

    def _is_listing(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if len(parts) == 1:
            return parts[0] in self.listdir_names or parts[0] in self.glob_names
        if len(parts) == 2:
            if parts[-2] in self.os_modules and parts[-1] == "listdir":
                return True
            if parts[-2] in self.glob_modules and parts[-1] in {"glob", "iglob"}:
                return True
        if isinstance(node.func, ast.Attribute) and node.func.attr in _PATH_LISTING_METHODS:
            return self._is_path_constructor(node.func.value)
        return False

    def _is_path_constructor(self, node: ast.AST) -> bool:
        parts = _dotted_parts(node.func) if isinstance(node, ast.Call) else ()
        if len(parts) == 1:
            return parts[0] in self.path_names
        return len(parts) == 2 and parts[-1] == "Path" and parts[-2] in self.pathlib_modules

    def _is_sklearn_estimator(self, node: ast.Call) -> bool:
        parts = _dotted_parts(node.func)
        if not parts:
            return False
        class_name = parts[-1]
        original: str | None = None
        if len(parts) == 1:
            original = self.sklearn_names.get(class_name)
        else:
            module = ".".join(parts[:-1])
            if module == "sklearn" or module.startswith("sklearn."):
                original = module + "." + class_name
            elif module in self.sklearn_modules.values():
                original = module + "." + class_name
            elif parts[0] in self.sklearn_modules and ".".join(parts[:-1]) == parts[0]:
                original = self.sklearn_modules[parts[0]] + "." + class_name
        if original is None:
            return False
        recognized_name = original.rsplit(".", 1)[-1]
        if recognized_name in _SKLEARN_NON_ESTIMATORS:
            return False
        return recognized_name in _SKLEARN_ESTIMATORS or recognized_name.endswith(
            ("Classifier", "Regressor", "Clustering", "Mixture", "Forest")
        )

    def _add_edit(self, edits: list[_Edit], node: ast.AST, replacement: str, rule: str) -> None:
        start, end = _span(self.source, self.starts, node)
        edits.append(_Edit(start, end, replacement, rule))

    def _listing_edits(self, edits: list[_Edit]) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not self._is_listing(node):
                continue
            if _immediate_sorted_parent(node, self.parents):
                continue
            self._add_edit(edits, node, f"sorted({_source(self.source, self.starts, node)})", "filesystem-listing")

    def _rng_edits(self, edits: list[_Edit], warnings: list[tuple[int, str]]) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not self._is_default_rng(node):
                continue
            if not node.args and not node.keywords:
                call = _source(self.source, self.starts, node)
                self._add_edit(edits, node, _append_argument(call, "0", has_arguments=False), "numpy-default-rng")
                continue
            if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value is None:
                self._add_edit(edits, node.args[0], "0", "numpy-default-rng")
                continue
            seed = next((keyword for keyword in node.keywords if keyword.arg == "seed"), None)
            if seed is not None and isinstance(seed.value, ast.Constant) and seed.value.value is None:
                self._add_edit(edits, seed.value, "0", "numpy-default-rng")
            elif any(keyword.arg is None for keyword in node.keywords):
                warnings.append((getattr(node, "lineno", 1), "Skipped default_rng(**kwargs): the seed is ambiguous."))

    def _sklearn_edits(self, edits: list[_Edit], warnings: list[tuple[int, str]]) -> None:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not self._is_sklearn_estimator(node):
                continue
            if any(keyword.arg == "random_state" for keyword in node.keywords):
                continue
            if _has_starred_arguments(node):
                warnings.append(
                    (getattr(node, "lineno", 1), "Skipped sklearn estimator with *args/**kwargs: random_state is ambiguous.")
                )
                continue
            call = _source(self.source, self.starts, node)
            has_arguments = bool(node.args or node.keywords)
            self._add_edit(edits, node, _append_argument(call, _SKLEARN_RANDOM_STATE, has_arguments=has_arguments), "sklearn-random-state")

    def _set_edits(self, edits: list[_Edit]) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and _is_named_call(node, {"list"}) and len(node.args) == 1 and not node.keywords:
                if self._is_set_expression(node.args[0]):
                    argument = _source(self.source, self.starts, node.args[0])
                    self._add_edit(edits, node, f"sorted({argument})", "set-list")
                    continue
            if isinstance(node, (ast.For, ast.comprehension)):
                iterator = node.iter
                if self._is_set_expression(iterator) and not _immediate_sorted_parent(iterator, self.parents):
                    expression = _source(self.source, self.starts, iterator)
                    self._add_edit(edits, iterator, f"sorted({expression})", "set-iteration")

    def edits(self) -> tuple[list[_Edit], list[tuple[int, str]]]:
        edits: list[_Edit] = []
        warnings: list[tuple[int, str]] = []
        self._sklearn_edits(edits, warnings)
        self._rng_edits(edits, warnings)
        self._listing_edits(edits)
        self._set_edits(edits)
        return edits, warnings


def _apply_edits(source: str, edits: Iterable[_Edit]) -> str:
    selected: list[_Edit] = []
    # Outer edits win if an expression is both a container and a candidate.
    for edit in sorted(edits, key=lambda item: (item.start, -(item.end - item.start))):
        if any(edit.start < previous.end and previous.start < edit.end for previous in selected):
            continue
        selected.append(edit)
    for edit in sorted(selected, key=lambda item: item.start, reverse=True):
        source = source[: edit.start] + edit.replacement + source[edit.end :]
    return source


def _cst_parts(node: cst.CSTNode) -> tuple[str, ...]:
    if isinstance(node, cst.Name):
        return (node.value,)
    if isinstance(node, cst.Attribute):
        prefix = _cst_parts(node.value)
        return (*prefix, node.attr.value) if prefix else ()
    return ()


class _CSTRepairTransformer(cst.CSTTransformer):
    """Small, syntax-aware codemod set used by the P1 preview."""

    def __init__(self) -> None:
        self.os_modules: set[str] = set()
        self.listdir_names: set[str] = set()
        self.glob_modules: set[str] = set()
        self.glob_names: set[str] = set()
        self.pathlib_modules: set[str] = set()
        self.path_names: set[str] = set()
        self.numpy_modules: set[str] = set()
        self.numpy_random_modules: set[str] = set()
        self.default_rng_names: set[str] = set()
        self.sklearn_names: set[str] = set()
        self.sklearn_modules: set[str] = set()
        self.set_names: set[str] = set()
        self._sorted_depth = 0

    def visit_Import(self, node: cst.Import) -> bool:
        for alias in node.names:
            name = ".".join(_cst_parts(alias.name))
            bound = alias.asname.name.value if alias.asname else name.split(".")[0]
            if name == "os":
                self.os_modules.add(bound)
            elif name == "glob":
                self.glob_modules.add(bound)
            elif name == "pathlib":
                self.pathlib_modules.add(bound)
            elif name == "numpy":
                self.numpy_modules.add(bound)
            elif name == "numpy.random":
                self.numpy_random_modules.add(bound)
            if name.startswith("sklearn.") or name == "sklearn":
                self.sklearn_modules.add(bound)
        return True

    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        module = ".".join(_cst_parts(node.module)) if node.module else ""
        for alias in node.names:
            if isinstance(alias, cst.ImportStar):
                continue
            name = ".".join(_cst_parts(alias.name))
            bound = alias.asname.name.value if alias.asname else name
            if module == "os" and name == "listdir":
                self.listdir_names.add(bound)
            elif module == "glob" and name in {"glob", "iglob"}:
                self.glob_names.add(bound)
            elif module == "pathlib" and name == "Path":
                self.path_names.add(bound)
            elif module == "numpy.random" and name == "default_rng":
                self.default_rng_names.add(bound)
            elif module == "numpy" and name == "random":
                self.numpy_random_modules.add(bound)
            if module.startswith("sklearn"):
                self.sklearn_names.add(bound)
        return True

    def visit_Assign(self, node: cst.Assign) -> bool:
        if self._is_set_expr(node.value):
            for target in node.targets:
                if isinstance(target.target, cst.Name):
                    self.set_names.add(target.target.value)
        return True

    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        if node.value is not None and self._is_set_expr(node.value) and isinstance(node.target, cst.Name):
            self.set_names.add(node.target.value)
        return True

    def visit_Call(self, node: cst.Call) -> bool:
        if _cst_parts(node.func) == ("sorted",):
            self._sorted_depth += 1
        return True

    def _is_set_expr(self, node: cst.CSTNode) -> bool:
        if isinstance(node, (cst.Set, cst.SetComp)):
            return True
        if isinstance(node, cst.Call):
            call_parts = _cst_parts(node.func)
            return len(call_parts) == 1 and call_parts[0] in {"set", "frozenset"}
        parts = _cst_parts(node)
        if len(parts) == 1 and parts[0] in self.set_names:
            return True
        return len(parts) == 1 and parts[0] in {"set", "frozenset"}

    def _is_default_rng(self, node: cst.Call) -> bool:
        parts = _cst_parts(node.func)
        if len(parts) == 1:
            return parts[0] in self.default_rng_names
        return (
            parts[-1:] == ("default_rng",)
            and (
                len(parts) >= 3 and parts[-3] in self.numpy_modules and parts[-2] == "random"
                or len(parts) >= 2 and parts[-2] in self.numpy_random_modules
            )
        )

    def _is_listing(self, node: cst.Call) -> bool:
        parts = _cst_parts(node.func)
        if len(parts) == 1 and parts[0] in self.listdir_names:
            return True
        if parts[-2:] in {(module, "listdir") for module in self.os_modules}:
            return True
        if len(parts) == 1 and parts[0] == "__direct_os__":
            return True
        if len(parts) >= 2 and parts[-2] in self.glob_modules and parts[-1] in {"glob", "iglob"}:
            return True
        if len(parts) == 1 and parts[0] in self.glob_names:
            return True
        if isinstance(node.func, cst.Attribute) and node.func.attr.value in _PATH_LISTING_METHODS:
            receiver = node.func.value
            receiver_parts = _cst_parts(receiver.func) if isinstance(receiver, cst.Call) else ()
            return (
                isinstance(receiver, cst.Call)
                and receiver_parts[-1:] == ("Path",)
                and (len(receiver_parts) == 1 or receiver_parts[-2] in self.pathlib_modules)
            )
        return False

    def _is_estimator(self, node: cst.Call) -> bool:
        parts = _cst_parts(node.func)
        if not parts:
            return False
        if len(parts) == 1:
            return parts[0] in self.sklearn_names
        return (
            parts[0] == "sklearn"
            or parts[0] in self.sklearn_modules
            or ".".join(parts[:-1]) in self.sklearn_modules
        ) and parts[-1] in _SKLEARN_ESTIMATORS

    @staticmethod
    def _has_keyword(node: cst.Call, name: str) -> bool:
        return any(argument.keyword and argument.keyword.value == name for argument in node.args)

    @staticmethod
    def _has_star(node: cst.Call) -> bool:
        return any(argument.star for argument in node.args)

    @staticmethod
    def _sorted(node: cst.CSTNode) -> cst.Call:
        return cst.Call(func=cst.Name("sorted"), args=[cst.Arg(value=node)])

    def leave_Call(self, original_node: cst.Call, updated_node: cst.Call) -> cst.CSTNode:
        is_sorted = _cst_parts(original_node.func) == ("sorted",)
        try:
            if not is_sorted and self._sorted_depth == 0 and self._is_listing(original_node):
                return self._sorted(updated_node)
            if self._is_default_rng(original_node) and not self._has_star(original_node):
                if not original_node.args:
                    return updated_node.with_changes(args=[cst.Arg(value=cst.Integer("0"))])
                first = original_node.args[0]
                if first.keyword is None and isinstance(first.value, cst.Name) and first.value.value == "None":
                    args = [first.with_changes(value=cst.Integer("0")), *updated_node.args[1:]]
                    return updated_node.with_changes(args=args)
            if self._is_estimator(original_node) and not self._has_keyword(original_node, "random_state") and not self._has_star(original_node):
                equal = cst.AssignEqual(
                    whitespace_before=cst.SimpleWhitespace(""),
                    whitespace_after=cst.SimpleWhitespace(""),
                )
                return updated_node.with_changes(
                    args=[
                        *updated_node.args,
                        cst.Arg(
                            keyword=cst.Name("random_state"),
                            equal=equal,
                            value=cst.Integer("0"),
                        ),
                    ]
                )
            if len(_cst_parts(original_node.func)) == 1 and _cst_parts(original_node.func) == ("list",) and len(original_node.args) == 1 and self._is_set_expr(original_node.args[0].value):
                return self._sorted(updated_node.args[0].value)
            return updated_node
        finally:
            if is_sorted:
                self._sorted_depth -= 1

    def leave_For(self, original_node: cst.For, updated_node: cst.For) -> cst.CSTNode:
        if self._is_set_expr(original_node.iter):
            return updated_node.with_changes(iter=self._sorted(updated_node.iter))
        return updated_node

    def leave_CompFor(self, original_node: cst.CompFor, updated_node: cst.CompFor) -> cst.CSTNode:
        if self._is_set_expr(original_node.iter):
            return updated_node.with_changes(iter=self._sorted(updated_node.iter))
        return updated_node


def _diff(original: str, transformed: str, filename: str) -> str:
    if original == transformed:
        return ""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            transformed.splitlines(keepends=True),
            fromfile=filename,
            tofile=f"{filename} (fixed)",
        )
    )


def preview_fixes(source: str, *, filename: str = "source.py") -> FixResult:
    """Return safe, diff-first repairs for one Python source string."""

    if not isinstance(source, str):
        raise TypeError("source must be a string")
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as error:
        message = f"Could not preview fixes for {filename}: {error.msg} at line {error.lineno}."
        return FixResult(False, "", (message,), source)

    scanner = _RepairScanner(source, tree)
    _edits, warning_records = scanner.edits()
    try:
        transformed = cst.parse_module(source).visit(_CSTRepairTransformer()).code
    except Exception as error:  # pragma: no cover - defensive fallback for unsupported CST syntax
        transformed = _apply_edits(source, _edits)
        warning_records.append((1, f"LibCST codemod fallback used: {error}"))
    warnings = tuple(
        message for _line, message in sorted(set(warning_records), key=lambda item: (item[0], item[1]))
    )
    return FixResult(
        changed=transformed != source,
        unified_diff=_diff(source, transformed, filename),
        warnings=warnings,
        transformed_source=transformed,
    )


# Common spellings keep the module easy to discover without adding a CLI or
# changing the P0 orchestrator.
preview_repairs = preview_fixes
repair_preview = preview_fixes
fix_source = preview_fixes


__all__ = ["FixPreview", "FixResult", "fix_source", "preview_fixes", "preview_repairs", "repair_preview"]
