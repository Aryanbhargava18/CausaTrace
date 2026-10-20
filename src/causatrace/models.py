"""Shared, JSON-serializable contracts used by the CausaTrace pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Location:
    path: str
    line: int
    column: int | None = None
    kind: str = "unknown"
    operation: str | None = None
    symbol: str | None = None
    confidence: str = "unknown"
    evidence: str | None = None


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    environment: Mapping[str, str] = field(default_factory=dict)
    inherit_environment: bool = True
    python_random_seed: int | None = None
    numpy_seed: int | None = None
    listing_seed: int | None = None
    timezone: str | None = None
    locale: str | None = "C.UTF-8"


@dataclass(frozen=True)
class ArtifactSpec:
    path: str
    kind: str = "binary"
    metric_pattern: str | None = None


@dataclass(frozen=True)
class RunRequest:
    target: str
    arguments: tuple[str, ...] = ()
    project_root: str = "."
    profile: ExecutionProfile = field(
        default_factory=lambda: ExecutionProfile(name="default")
    )
    timeout_seconds: float = 30.0
    artifacts: tuple[ArtifactSpec, ...] = ()
    metric_patterns: tuple[str, ...] = ()
    trace: bool = True
    temporary_copy: bool = True


@dataclass(frozen=True)
class RunResult:
    command: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    output_hash: str
    stderr_hash: str
    artifacts: Mapping[str, str] = field(default_factory=dict)
    missing_artifacts: tuple[str, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    trace_events: tuple[Mapping[str, Any], ...] = ()
    duration_seconds: float = 0.0
    limitations: tuple[str, ...] = ()
    profile: str = "unknown"


@dataclass(frozen=True)
class RunEvidence:
    """Compact, auditable evidence for one execution.

    Reports intentionally carry hashes and parsed metrics rather than captured
    stdout/stderr.  This keeps the machine-readable evidence useful for
    auditing a classification without copying secrets or arbitrarily large
    program output into JSON or HTML.
    """

    profile: str
    output_hash: str
    stderr_hash: str
    artifacts: Mapping[str, str] = field(default_factory=dict)
    missing_artifacts: tuple[str, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    exit_code: int | None = None
    timed_out: bool = False

    @classmethod
    def from_run(cls, result: RunResult, *, profile: str | None = None) -> RunEvidence:
        return cls(
            profile=profile or result.profile,
            output_hash=result.output_hash,
            stderr_hash=result.stderr_hash,
            artifacts=dict(result.artifacts),
            missing_artifacts=tuple(result.missing_artifacts),
            metrics=dict(result.metrics),
            exit_code=result.exit_code,
            timed_out=result.timed_out,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the compact serialized form used by report payloads."""

        return asdict(self)

    @property
    def profile_name(self) -> str:
        """Compatibility spelling for callers that use the full field name."""

        return self.profile

    @property
    def artifact_hashes(self) -> Mapping[str, str]:
        """Compatibility spelling that makes the digest nature explicit."""

        return self.artifacts

    @property
    def exit_status(self) -> int | None:
        return self.exit_code

    @property
    def timeout(self) -> bool:
        return self.timed_out


@dataclass(frozen=True)
class OutputComparison:
    changed: bool
    stdout_changed: bool
    stderr_changed: bool
    changed_artifacts: tuple[str, ...] = ()
    changed_metrics: tuple[str, ...] = ()
    max_absolute_difference: float | None = None
    mean_absolute_difference: float | None = None
    explanation: str = ""


@dataclass(frozen=True)
class ProbeResult:
    factor: str
    treatment: str
    trials: int
    changed_trials: int
    consistency: float
    comparison: OutputComparison | None = None
    locations: tuple[Location, ...] = ()
    classification: str = "inconclusive"
    limitations: tuple[str, ...] = ()
    baseline_trials: tuple[RunEvidence, ...] = ()
    treatment_trials: tuple[RunEvidence, ...] = ()


@dataclass(frozen=True)
class StaticFinding:
    rule_id: str
    message: str
    location: Location
    suggestion: str | None = None


@dataclass(frozen=True)
class AnalysisReport:
    schema_version: str
    target: str
    status: str
    baseline: RunResult | None = None
    baseline_repeat: RunResult | None = None
    baseline_trials: tuple[RunResult, ...] = ()
    probes: tuple[ProbeResult, ...] = ()
    static_findings: tuple[StaticFinding, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe report without raw process output.

        ``RunResult`` remains available to in-process callers for comparisons
        and diagnostics, but serialized reports use :class:`RunEvidence` for
        every baseline and probe trial.
        """

        return {
            "schema_version": self.schema_version,
            "target": self.target,
            "status": self.status,
            "baseline": _compact_run(self.baseline, "baseline"),
            "baseline_repeat": _compact_run(self.baseline_repeat, "baseline"),
            "baseline_trials": [
                _compact_run(run, "baseline") for run in self.baseline_trials
            ],
            "probes": [_probe_to_dict(probe) for probe in self.probes],
            "static_findings": [asdict(finding) for finding in self.static_findings],
            "limitations": list(self.limitations),
        }


def _compact_run(
    value: RunResult | RunEvidence | None,
    fallback_profile: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, RunEvidence):
        return value.to_dict()
    profile = value.profile if value.profile and value.profile != "unknown" else fallback_profile
    return RunEvidence.from_run(value, profile=profile).to_dict()


def _probe_to_dict(probe: ProbeResult) -> dict[str, Any]:
    value = asdict(probe)
    value["baseline_trials"] = [
        _compact_run(run, "baseline") for run in probe.baseline_trials
    ]
    value["treatment_trials"] = [
        _compact_run(run, probe.treatment or "treatment") for run in probe.treatment_trials
    ]
    return value
