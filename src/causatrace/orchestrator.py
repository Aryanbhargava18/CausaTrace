"""P0 analysis workflow: baseline, controlled probes, attribution, reports."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable, Sequence

from .attribution import (
    Attributor,
    FACTOR_SUPPORTED,
    INTERACTION_SUSPECTED,
)
from .compare import ExactComparator
from .locator import SourceLocator
from .models import (
    AnalysisReport,
    ArtifactSpec,
    ExecutionProfile,
    ProbeResult,
    RunRequest,
    RunResult,
)
from .profiles import (
    all_released_profile,
    baseline_profile,
    default_factor_probes,
)
from .runner import SubprocessRunner


class AnalysisError(ValueError):
    """Raised when an analysis request cannot be made safely."""


class AnalysisOrchestrator:
    """Run the evidence-first P0 workflow for one Python script."""

    def __init__(
        self,
        *,
        runner: SubprocessRunner | None = None,
        comparator: ExactComparator | None = None,
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.comparator = comparator or ExactComparator()
        self.attributor = Attributor(self.comparator)

    @staticmethod
    def _root_and_target(
        target: str | Path,
        project_root: str | Path | None,
    ) -> tuple[Path, str]:
        target_path = Path(target).expanduser().resolve()
        if not target_path.is_file():
            raise AnalysisError(f"target script does not exist: {target_path}")
        root = Path(project_root).expanduser().resolve() if project_root else target_path.parent
        if not root.is_dir():
            raise AnalysisError(f"project root does not exist: {root}")
        try:
            relative = target_path.relative_to(root)
        except ValueError as exc:
            raise AnalysisError("target must be inside project_root") from exc
        return root, str(relative)

    def _run(
        self,
        *,
        root: Path,
        target: str,
        profile: ExecutionProfile,
        arguments: Sequence[str],
        artifacts: tuple[ArtifactSpec, ...],
        metric_patterns: tuple[str, ...],
        timeout_seconds: float,
    ) -> RunResult:
        return self.runner.run(
            RunRequest(
                target=target,
                arguments=tuple(arguments),
                project_root=str(root),
                profile=profile,
                timeout_seconds=timeout_seconds,
                artifacts=artifacts,
                metric_patterns=metric_patterns,
                trace=True,
                temporary_copy=True,
            )
        )

    def analyze(
        self,
        target: str | Path,
        *,
        project_root: str | Path | None = None,
        arguments: Sequence[str] = (),
        artifacts: Iterable[ArtifactSpec] = (),
        metric_patterns: Iterable[str] = (),
        trials: int = 3,
        timeout_seconds: float = 30.0,
        include_numpy: bool | None = None,
    ) -> AnalysisReport:
        if trials != 3:
            raise AnalysisError("P0 requires exactly 3 paired discovery trials")
        if timeout_seconds <= 0:
            raise AnalysisError("timeout_seconds must be positive")

        root, relative_target = self._root_and_target(target, project_root)
        artifact_specs = tuple(artifacts)
        patterns = tuple(metric_patterns)
        control = baseline_profile()

        baseline_runs = tuple(
            self._run(
                root=root,
                target=relative_target,
                profile=control,
                arguments=arguments,
                artifacts=artifact_specs,
                metric_patterns=patterns,
                timeout_seconds=timeout_seconds,
            )
            for _ in range(trials)
        )
        baseline = baseline_runs[0]
        baseline_repeat = baseline_runs[1]
        static_findings = SourceLocator(root).find_static(root / relative_target)

        baseline_report = self.attributor.analyze(
            target=relative_target,
            baseline=baseline,
            baseline_repeat=baseline_repeat,
            schema_version="1.0",
        )
        if baseline_report.status != "stable_baseline":
            return self.attributor.analyze(
                target=relative_target,
                baseline=baseline,
                baseline_repeat=baseline_repeat,
                static_findings=static_findings,
                limitations=tuple(sorted({item for run in baseline_runs for item in run.limitations})),
                schema_version="1.0",
            )

        probes: list[ProbeResult] = []
        for factor_probe in default_factor_probes(include_numpy=include_numpy):
            for treatment_profile in factor_probe.profiles(control):
                treatment_runs = tuple(
                    self._run(
                        root=root,
                        target=relative_target,
                        profile=replace(
                            treatment_profile,
                            name=f"{treatment_profile.name}:trial-{index + 1}",
                        ),
                        arguments=arguments,
                        artifacts=artifact_specs,
                        metric_patterns=patterns,
                        timeout_seconds=timeout_seconds,
                    )
                    for index in range(trials)
                )
                locations = SourceLocator(root).find_runtime(
                    tuple(event for run in treatment_runs for event in run.trace_events),
                    root,
                )
                probe_result = self.attributor.attribute(
                    factor_probe.name,
                    baseline_runs,
                    treatment_runs,
                    treatment=treatment_profile.name,
                    locations=locations,
                    limitations=tuple(
                        sorted({item for run in treatment_runs for item in run.limitations})
                    ),
                )
                if probe_result.classification != FACTOR_SUPPORTED:
                    probe_result = replace(probe_result, locations=())
                probes.append(probe_result)

        released_profile = all_released_profile()
        released_runs = tuple(
            self._run(
                root=root,
                target=relative_target,
                profile=replace(released_profile, name=f"{released_profile.name}:trial-{index + 1}"),
                arguments=arguments,
                artifacts=artifact_specs,
                metric_patterns=patterns,
                timeout_seconds=timeout_seconds,
            )
            for index in range(trials)
        )
        released_comparisons = tuple(
            self.comparator.compare(control_run, released_run)
            for control_run, released_run in zip(baseline_runs, released_runs)
        )
        released_changed = sum(comparison.changed for comparison in released_comparisons)
        released_locations = SourceLocator(root).find_runtime(
            tuple(event for run in released_runs for event in run.trace_events),
            root,
        )
        released_probe = self.attributor.attribute(
            "all_released",
            baseline_runs,
            released_runs,
            treatment=released_profile.name,
            locations=released_locations,
            limitations=(
                "The all-released profile can flag an interaction but cannot identify one factor by itself.",
            ),
        )
        if released_changed:
            released_probe = replace(released_probe, classification=INTERACTION_SUSPECTED)
        released_probe = replace(released_probe, locations=())
        probes.append(released_probe)

        limitations = tuple(
            sorted(
                {
                    item
                    for run in (*baseline_runs, *released_runs)
                    for item in run.limitations
                }
            )
        )
        report = self.attributor.analyze(
            target=relative_target,
            baseline=baseline,
            baseline_repeat=baseline_repeat,
            probes=probes,
            static_findings=static_findings,
            limitations=limitations,
            schema_version="1.0",
        )
        return report


def analyze_script(*args: object, **kwargs: object) -> AnalysisReport:
    """Convenience wrapper for the default P0 orchestrator."""

    return AnalysisOrchestrator().analyze(*args, **kwargs)  # type: ignore[arg-type]


__all__ = ["AnalysisError", "AnalysisOrchestrator", "analyze_script"]
