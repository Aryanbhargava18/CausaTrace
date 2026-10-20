"""Honest fixture benchmark for the planted P0 cases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .attribution import FACTOR_SUPPORTED
from .orchestrator import AnalysisOrchestrator
from .profiles import numpy_available


@dataclass(frozen=True, slots=True)
class FixtureCase:
    fixture: str
    expected_factor: str | None


@dataclass(frozen=True, slots=True)
class FixtureBenchmarkRow:
    fixture: str
    expected_factor: str | None
    status: str
    detected_factor: str | None
    correct: bool
    note: str


DEFAULT_FIXTURE_CASES: tuple[FixtureCase, ...] = (
    FixtureCase("clean_control.py", None),
    FixtureCase("unseeded_python_random.py", "python_random"),
    FixtureCase("numpy_default_rng.py", "numpy_random"),
    FixtureCase("hash_dependent_set.py", "hash_seed"),
    FixtureCase("filesystem_listing.py", "filesystem_listing"),
    FixtureCase("timezone_date.py", "timezone"),
    FixtureCase("environment_variable.py", None),
    FixtureCase("baseline_flaky.py", None),
)


def run_fixture_benchmark(
    project_root: str | Path,
    *,
    cases: Iterable[FixtureCase] = DEFAULT_FIXTURE_CASES,
    include_numpy: bool | None = None,
) -> tuple[FixtureBenchmarkRow, ...]:
    """Run planted fixtures and return fixture-only evidence, not accuracy claims."""

    root = Path(project_root).resolve()
    orchestrator = AnalysisOrchestrator()
    rows: list[FixtureBenchmarkRow] = []
    for case in cases:
        if case.expected_factor == "numpy_random" and not (include_numpy if include_numpy is not None else numpy_available()):
            rows.append(
                FixtureBenchmarkRow(
                    fixture=case.fixture,
                    expected_factor=case.expected_factor,
                    status="skipped_optional_dependency",
                    detected_factor=None,
                    correct=True,
                    note="NumPy is unavailable; optional fixture skipped",
                )
            )
            continue
        target = root / "tests" / "fixtures" / case.fixture
        report = orchestrator.analyze(
            target,
            project_root=root,
            include_numpy=include_numpy,
        )
        supported = tuple(
            probe.factor
            for probe in report.probes
            if probe.classification == FACTOR_SUPPORTED
        )
        detected = next(
            (factor for factor in supported if factor == case.expected_factor),
            supported[0] if supported else None,
        )
        if report.status == "baseline_unstable":
            correct = case.fixture == "baseline_flaky.py"
            note = "baseline instability was correctly surfaced"
        elif case.expected_factor is None:
            correct = not supported
            note = "no planted factor expected"
        else:
            correct = case.expected_factor in supported
            note = "planted factor detected" if correct else "planted factor not detected"
        rows.append(
            FixtureBenchmarkRow(
                fixture=case.fixture,
                expected_factor=case.expected_factor,
                status=report.status,
                detected_factor=detected,
                correct=correct,
                note=note,
            )
        )
    return tuple(rows)


def render_benchmark_terminal(rows: Iterable[FixtureBenchmarkRow]) -> str:
    rows = tuple(rows)
    lines = [
        "CausaTrace fixture benchmark",
        "Fixture benchmark only; this is not a real-world accuracy estimate.",
        "",
    ]
    for row in rows:
        expected = row.expected_factor or "none"
        detected = row.detected_factor or "none"
        marker = "PASS" if row.correct else "FAIL"
        lines.append(
            f"[{marker}] {row.fixture}: expected={expected}, detected={detected}, "
            f"status={row.status} ({row.note})"
        )
    passed = sum(row.correct for row in rows)
    lines.extend(("", f"Fixture cases passed: {passed}/{len(rows)}"))
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_FIXTURE_CASES",
    "FixtureBenchmarkRow",
    "FixtureCase",
    "render_benchmark_terminal",
    "run_fixture_benchmark",
]
