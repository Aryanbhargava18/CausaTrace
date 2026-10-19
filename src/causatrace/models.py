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
    metrics: Mapping[str, float] = field(default_factory=dict)
    trace_events: tuple[Mapping[str, Any], ...] = ()
    duration_seconds: float = 0.0
    limitations: tuple[str, ...] = ()


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
    probes: tuple[ProbeResult, ...] = ()
    static_findings: tuple[StaticFinding, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
