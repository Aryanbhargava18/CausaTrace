"""Exact, evidence-preserving comparison of runner results.

P0 deliberately does not use a tolerance for numeric metrics.  A metric is
changed when its value is missing on one side or when the two parsed numeric
values are not exactly equal.  Output and artifact comparisons use the
SHA-256 digests supplied by the runner, rather than normalising or diffing the
captured text.  This keeps a comparison reproducible and prevents a fuzzy
comparison from being presented as proof.
"""

from __future__ import annotations

import math
from numbers import Real
from typing import Iterable

from .models import OutputComparison, RunResult


def _numeric_difference(left: object, right: object) -> float | None:
    """Return an absolute numeric difference for two supported metric values.

    Metrics emitted by the P0 capture layer are floats.  Keeping this helper
    small also makes manually-created ``RunResult`` values useful in tests;
    non-numeric values are reported as changed by the caller but do not
    contribute a misleading numeric delta.
    """

    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if not isinstance(left, Real) or not isinstance(right, Real):
        return None
    try:
        difference = abs(left - right)
        return float(difference)
    except (TypeError, ValueError, OverflowError):
        return None


class ExactComparator:
    """Compare two :class:`~causatrace.models.RunResult` instances exactly."""

    def compare(self, baseline: RunResult, treatment: RunResult) -> OutputComparison:
        """Compare output hashes, artifacts, execution state, and metrics.

        ``changed_metrics`` contains the sorted union of metric names whose
        presence or exact value differs.  For common numeric metrics,
        ``max_absolute_difference`` and ``mean_absolute_difference`` are
        calculated over all common metrics (including equal metrics, which
        makes a comparable metric set report ``0.0`` when unchanged).  No
        epsilon or rounding is applied.
        """

        stdout_changed = baseline.output_hash != treatment.output_hash
        stderr_changed = baseline.stderr_hash != treatment.stderr_hash

        artifact_names = sorted(set(baseline.artifacts) | set(treatment.artifacts))
        changed_artifacts = tuple(
            name
            for name in artifact_names
            if baseline.artifacts.get(name) != treatment.artifacts.get(name)
        )

        metric_names = sorted(set(baseline.metrics) | set(treatment.metrics))
        changed_metrics = tuple(
            name
            for name in metric_names
            if (
                name not in baseline.metrics
                or name not in treatment.metrics
                or baseline.metrics[name] != treatment.metrics[name]
            )
        )

        metric_differences: list[float] = []
        for name in sorted(set(baseline.metrics) & set(treatment.metrics)):
            difference = _numeric_difference(baseline.metrics[name], treatment.metrics[name])
            if difference is not None and math.isfinite(difference):
                metric_differences.append(difference)

        max_difference = max(metric_differences) if metric_differences else None
        mean_difference = (
            sum(metric_differences) / len(metric_differences)
            if metric_differences
            else None
        )

        execution_changed = (
            baseline.exit_code != treatment.exit_code
            or baseline.timed_out != treatment.timed_out
        )
        changed = bool(
            stdout_changed
            or stderr_changed
            or changed_artifacts
            or changed_metrics
            or execution_changed
        )

        differences: list[str] = []
        if stdout_changed:
            differences.append("stdout SHA-256 changed")
        if stderr_changed:
            differences.append("stderr SHA-256 changed")
        if changed_artifacts:
            differences.append("artifact SHA-256 changed: " + ", ".join(changed_artifacts))
        if changed_metrics:
            differences.append("metrics changed exactly (no tolerance): " + ", ".join(changed_metrics))
        if execution_changed:
            differences.append("execution status changed")
        explanation = "; ".join(differences) if differences else "No exact output, artifact, metric, or execution-state changes."

        return OutputComparison(
            changed=changed,
            stdout_changed=stdout_changed,
            stderr_changed=stderr_changed,
            changed_artifacts=changed_artifacts,
            changed_metrics=changed_metrics,
            max_absolute_difference=max_difference,
            mean_absolute_difference=mean_difference,
            explanation=explanation,
        )


# Public aliases keep the protocol name and common implementation spellings
# convenient for callers without forcing them to depend on one class name.
Comparator = ExactComparator
OutputComparator = ExactComparator
SHA256Comparator = ExactComparator


def compare_runs(baseline: RunResult, treatment: RunResult) -> OutputComparison:
    """Compare two runner results using the P0 exact comparator."""

    return ExactComparator().compare(baseline, treatment)


compare = compare_runs
compare_output = compare_runs
compare_results = compare_runs


def aggregate_comparisons(comparisons: Iterable[OutputComparison]) -> OutputComparison | None:
    """Summarise paired comparisons without discarding changed evidence.

    Attribution uses this for a probe's multiple paired trials.  The result
    is intentionally conservative: any changed pair keeps the aggregate
    changed, and changed artifact/metric names are the sorted union.
    """

    values = tuple(comparisons)
    if not values:
        return None
    artifact_names = tuple(sorted({name for value in values for name in value.changed_artifacts}))
    metric_names = tuple(sorted({name for value in values for name in value.changed_metrics}))
    max_values = [value.max_absolute_difference for value in values if value.max_absolute_difference is not None]
    mean_values = [value.mean_absolute_difference for value in values if value.mean_absolute_difference is not None]
    max_difference = max(max_values) if max_values else None
    mean_difference = sum(mean_values) / len(mean_values) if mean_values else None
    changed = any(value.changed for value in values)
    explanation = (
        f"{len(values)} paired comparisons; {sum(value.changed for value in values)} changed. "
        "Each pair used exact SHA-256 and exact numeric metric equality (no tolerance)."
    )
    return OutputComparison(
        changed=changed,
        stdout_changed=any(value.stdout_changed for value in values),
        stderr_changed=any(value.stderr_changed for value in values),
        changed_artifacts=artifact_names,
        changed_metrics=metric_names,
        max_absolute_difference=max_difference,
        mean_absolute_difference=mean_difference,
        explanation=explanation,
    )


__all__ = [
    "Comparator",
    "ExactComparator",
    "OutputComparator",
    "SHA256Comparator",
    "aggregate_comparisons",
    "compare",
    "compare_output",
    "compare_results",
    "compare_runs",
]
