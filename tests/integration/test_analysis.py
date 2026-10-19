from __future__ import annotations

from pathlib import Path

import pytest

from causatrace.attribution import FACTOR_SUPPORTED
from causatrace.orchestrator import AnalysisOrchestrator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"


def analyze(name: str):
    return AnalysisOrchestrator().analyze(
        FIXTURE_ROOT / name,
        project_root=PROJECT_ROOT,
        include_numpy=True,
    )


@pytest.mark.parametrize(
    ("fixture", "factor"),
    (
        ("unseeded_python_random.py", "python_random"),
        ("numpy_default_rng.py", "numpy_random"),
        ("hash_dependent_set.py", "hash_seed"),
        ("filesystem_listing.py", "filesystem_listing"),
    ),
)
def test_analysis_identifies_planted_factor(fixture: str, factor: str) -> None:
    if factor == "numpy_random":
        pytest.importorskip("numpy")
    report = analyze(fixture)
    matching = [probe for probe in report.probes if probe.factor == factor]
    assert matching
    assert any(probe.classification == FACTOR_SUPPORTED for probe in matching)


def test_analysis_identifies_timezone_and_user_line() -> None:
    report = analyze("timezone_date.py")
    timezone_probes = [probe for probe in report.probes if probe.factor == "timezone"]
    assert any(probe.classification == FACTOR_SUPPORTED for probe in timezone_probes)


def test_analysis_withholds_factor_claims_for_unstable_baseline() -> None:
    report = analyze("baseline_flaky.py")
    assert report.status == "baseline_unstable"
    assert report.probes == ()
