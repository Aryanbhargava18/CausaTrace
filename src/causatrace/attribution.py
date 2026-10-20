"""Paired-trial attribution for P0 factor experiments.

Attribution is intentionally separate from static analysis.  A static rule or
source location can explain where a factor *might* matter, but only paired
runtime results are used to classify a factor here.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .compare import ExactComparator, aggregate_comparisons
from .models import (
    AnalysisReport,
    ExecutionProfile,
    Location,
    ProbeResult,
    RunResult,
    StaticFinding,
)


FACTOR_SUPPORTED = "factor_supported"
FACTOR_NOT_OBSERVED = "factor_not_observed"
INTERACTION_SUSPECTED = "interaction_suspected"
INCONCLUSIVE = "inconclusive"
BASELINE_UNSTABLE = "baseline_unstable"

_BASELINE_LIMITATION = (
    "Baseline repeats were not identical; factor attribution is protected and marked inconclusive."
)
_PAIRING_LIMITATION = "Paired baseline and treatment trial counts do not match."
_FAILED_RUN_LIMITATION = "At least one paired run failed or timed out; attribution is inconclusive."


def _as_runs(value: RunResult | Iterable[RunResult] | None) -> tuple[RunResult, ...]:
    if value is None:
        return ()
    if isinstance(value, RunResult):
        return (value,)
    return tuple(value)


def _run_succeeded(result: RunResult) -> bool:
    return result.exit_code == 0 and not result.timed_out


def baseline_is_stable(
    baseline_trials: RunResult | Iterable[RunResult],
    *,
    comparator: ExactComparator | None = None,
) -> bool:
    """Return whether all successful baseline repeats are exactly identical.

    A single baseline is accepted as the caller's already-pinned baseline.
    When repeats are supplied, every repeat must be successful and have no
    exact comparison difference from the first repeat.
    """

    runs = _as_runs(baseline_trials)
    if not runs or not all(_run_succeeded(run) for run in runs):
        return False
    if len(runs) == 1:
        return True
    comparator = comparator or ExactComparator()
    reference = runs[0]
    return all(not comparator.compare(reference, run).changed for run in runs[1:])


def _baseline_repeat_is_stable(
    baseline: RunResult | None,
    baseline_repeat: RunResult | None,
    *,
    comparator: ExactComparator,
) -> bool:
    if baseline is None or not _run_succeeded(baseline):
        return False
    if baseline_repeat is None:
        return True
    return _run_succeeded(baseline_repeat) and not comparator.compare(baseline, baseline_repeat).changed


def _aggregate_limitations(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted(set(value for value in values if value)))


def attribute_factor(
    factor: str,
    baseline_trials: RunResult | Iterable[RunResult],
    treatment_trials: RunResult | Iterable[RunResult],
    *,
    treatment: str | None = None,
    locations: Sequence[Location] = (),
    limitations: Sequence[str] = (),
    comparator: ExactComparator | None = None,
) -> ProbeResult:
    """Classify a factor from paired baseline/treatment runs.

    P0 semantics are deliberately exact and simple:

    * every paired treatment change => ``factor_supported``;
    * no paired treatment changes => ``factor_not_observed``;
    * a mix of changed and unchanged pairs => ``interaction_suspected``;
    * missing/mismatched pairs or failed runs => ``inconclusive``.

    ``consistency`` is the exact fraction ``changed_trials / trials``.  A
    baseline repeat that changes is a hard protection: the result is
    ``inconclusive`` even if treatment pairs differ.
    """

    comparator = comparator or ExactComparator()
    baselines = _as_runs(baseline_trials)
    treatments = _as_runs(treatment_trials)
    trial_count = len(treatments)
    treatment_name = treatment or factor
    base_limitations = list(limitations)

    if not baselines or not treatments:
        base_limitations.append("At least one baseline and one treatment trial are required.")
        return ProbeResult(
            factor=factor,
            treatment=treatment_name,
            trials=trial_count,
            changed_trials=0,
            consistency=0.0,
            comparison=None,
            locations=tuple(locations),
            classification=INCONCLUSIVE,
            limitations=_aggregate_limitations(base_limitations),
        )

    if len(baselines) == 1:
        paired_baselines = baselines * len(treatments)
    elif len(baselines) == len(treatments):
        paired_baselines = baselines
    else:
        base_limitations.append(_PAIRING_LIMITATION)
        return ProbeResult(
            factor=factor,
            treatment=treatment_name,
            trials=trial_count,
            changed_trials=0,
            consistency=0.0,
            comparison=None,
            locations=tuple(locations),
            classification=INCONCLUSIVE,
            limitations=_aggregate_limitations(base_limitations),
        )

    if not baseline_is_stable(baselines, comparator=comparator):
        base_limitations.append(_BASELINE_LIMITATION)
        return ProbeResult(
            factor=factor,
            treatment=treatment_name,
            trials=trial_count,
            changed_trials=0,
            consistency=0.0,
            comparison=None,
            locations=tuple(locations),
            classification=INCONCLUSIVE,
            limitations=_aggregate_limitations(base_limitations),
        )

    comparisons = tuple(
        comparator.compare(baseline, treatment_run)
        for baseline, treatment_run in zip(paired_baselines, treatments)
    )
    changed_trials = sum(comparison.changed for comparison in comparisons)
    consistency = changed_trials / trial_count if trial_count else 0.0
    failed = any(not _run_succeeded(run) for run in (*paired_baselines, *treatments))
    if failed:
        base_limitations.append(_FAILED_RUN_LIMITATION)
        classification = INCONCLUSIVE
    elif changed_trials == trial_count:
        classification = FACTOR_SUPPORTED
    elif changed_trials == 0:
        classification = FACTOR_NOT_OBSERVED
    else:
        # A repeatable baseline plus a mixed treatment response is evidence of
        # a context-dependent effect, not proof of a universal factor effect.
        classification = INTERACTION_SUSPECTED

    return ProbeResult(
        factor=factor,
        treatment=treatment_name,
        trials=trial_count,
        changed_trials=changed_trials,
        consistency=consistency,
        comparison=aggregate_comparisons(comparisons),
        locations=tuple(locations),
        classification=classification,
        limitations=_aggregate_limitations(base_limitations),
    )


def attribute_probe(*args: Any, **kwargs: Any) -> ProbeResult:
    """Compatibility spelling for :func:`attribute_factor`."""

    return attribute_factor(*args, **kwargs)


def classify_factor(
    factor: str,
    baseline_trials: RunResult | Iterable[RunResult],
    treatment_trials: RunResult | Iterable[RunResult],
    **kwargs: Any,
) -> str:
    """Return only the P0 classification for a paired factor experiment."""

    return attribute_factor(factor, baseline_trials, treatment_trials, **kwargs).classification


def paired_trial_consistency(
    baseline_trials: RunResult | Iterable[RunResult],
    treatment_trials: RunResult | Iterable[RunResult],
    *,
    comparator: ExactComparator | None = None,
) -> tuple[int, int, float]:
    """Return ``(changed_trials, trials, consistency)`` for paired runs.

    This helper intentionally reports no classification; callers must apply
    the baseline and failed-run protections in :func:`attribute_factor` when
    turning the count into a causal finding.
    """

    comparator = comparator or ExactComparator()
    baselines = _as_runs(baseline_trials)
    treatments = _as_runs(treatment_trials)
    if len(baselines) == 1:
        baselines = baselines * len(treatments)
    if len(baselines) != len(treatments):
        return (0, len(treatments), 0.0)
    changed = sum(
        comparator.compare(baseline, treatment).changed
        for baseline, treatment in zip(baselines, treatments)
    )
    trials = len(treatments)
    return changed, trials, changed / trials if trials else 0.0


@dataclass(frozen=True)
class FactorExperiment:
    """Input record accepted by :meth:`Attributor.attribute_experiment`."""

    factor: str
    treatment: str
    baseline_trials: tuple[RunResult, ...]
    treatment_trials: tuple[RunResult, ...]
    locations: tuple[Location, ...] = ()
    limitations: tuple[str, ...] = ()


class Attributor:
    """Small service object for exact paired-trial attribution."""

    def __init__(self, comparator: ExactComparator | None = None) -> None:
        self.comparator = comparator or ExactComparator()

    def attribute(
        self,
        factor: str,
        baseline_trials: RunResult | Iterable[RunResult],
        treatment_trials: RunResult | Iterable[RunResult],
        **kwargs: Any,
    ) -> ProbeResult:
        return attribute_factor(
            factor,
            baseline_trials,
            treatment_trials,
            comparator=self.comparator,
            **kwargs,
        )

    def attribute_experiment(self, experiment: FactorExperiment) -> ProbeResult:
        return self.attribute(
            experiment.factor,
            experiment.baseline_trials,
            experiment.treatment_trials,
            treatment=experiment.treatment,
            locations=experiment.locations,
            limitations=experiment.limitations,
        )

    def analyze(
        self,
        *,
        target: str,
        baseline: RunResult | None,
        baseline_repeat: RunResult | None = None,
        baseline_trials: Iterable[RunResult] | None = None,
        probes: Iterable[ProbeResult] = (),
        static_findings: Iterable[StaticFinding] = (),
        limitations: Iterable[str] = (),
        schema_version: str = "1.0",
    ) -> AnalysisReport:
        """Build a report while protecting all probes from an unstable baseline."""

        probe_values = tuple(probes)
        report_limitations = list(limitations)
        all_baselines = tuple(baseline_trials or ())
        baseline_stable = (
            baseline_is_stable(all_baselines, comparator=self.comparator)
            if all_baselines
            else _baseline_repeat_is_stable(
                baseline,
                baseline_repeat,
                comparator=self.comparator,
            )
        )
        if baseline is None or not _run_succeeded(baseline):
            status = INCONCLUSIVE
            report_limitations.append("No successful baseline run is available.")
        elif not baseline_stable:
            status = BASELINE_UNSTABLE
            report_limitations.append(_BASELINE_LIMITATION)
            # Keep the observed comparison evidence, but do not publish a
            # causal factor classification when the pinned baseline itself is
            # unstable.
            probe_values = tuple(
                replace(
                    probe,
                    classification=INCONCLUSIVE,
                    limitations=_aggregate_limitations(
                        (*probe.limitations, _BASELINE_LIMITATION)
                    ),
                )
                for probe in probe_values
            )
        else:
            status = "stable_baseline"
        return AnalysisReport(
            schema_version=schema_version,
            target=target,
            status=status,
            baseline=baseline,
            baseline_repeat=baseline_repeat,
            baseline_trials=all_baselines,
            probes=probe_values,
            static_findings=tuple(static_findings),
            limitations=_aggregate_limitations(report_limitations),
        )


AttributionEngine = Attributor
Attribution = Attributor


def build_analysis_report(**kwargs: Any) -> AnalysisReport:
    """Convenience wrapper around :class:`Attributor`."""

    return Attributor().analyze(**kwargs)


__all__ = [
    "Attributor",
    "AttributionEngine",
    "Attribution",
    "BASELINE_UNSTABLE",
    "FACTOR_NOT_OBSERVED",
    "FACTOR_SUPPORTED",
    "FactorExperiment",
    "INCONCLUSIVE",
    "INTERACTION_SUSPECTED",
    "attribute_factor",
    "attribute_probe",
    "baseline_is_stable",
    "build_analysis_report",
    "classify_factor",
    "paired_trial_consistency",
]
