from __future__ import annotations

from pathlib import Path

import pytest

from causatrace.models import ExecutionProfile, RunRequest
from causatrace.runner import SubprocessRunner


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures"
RUNNER = SubprocessRunner()


def run_fixture(name: str, profile: ExecutionProfile | None = None) -> str:
    """Run one planted fixture through the offline temporary-copy runner."""

    result = RUNNER.run(
        RunRequest(
            target=str(Path("tests") / "fixtures" / name),
            project_root=str(PROJECT_ROOT),
            profile=profile or ExecutionProfile(name="baseline"),
        )
    )
    assert result.exit_code == 0, result.stderr
    return result.stdout.strip()


def test_clean_control_is_stable_under_control_profiles() -> None:
    profiles = (
        ExecutionProfile(name="baseline"),
        ExecutionProfile(
            name="all-controls",
            environment={"PYTHONHASHSEED": "23", "CAUSATRACE_FIXTURE_VALUE": "ignored"},
            python_random_seed=17,
            numpy_seed=19,
            listing_seed=29,
            timezone="America/New_York",
        ),
    )

    outputs = [run_fixture("clean_control.py", profile) for profile in profiles]

    assert outputs == [
        '{"items": ["alpha", "beta", "gamma"], "score": 42, "version": 1}',
        '{"items": ["alpha", "beta", "gamma"], "score": 42, "version": 1}',
    ]


def test_unseeded_python_random_changes_between_processes() -> None:
    outputs = {run_fixture("unseeded_python_random.py") for _ in range(3)}

    assert len(outputs) >= 2
    assert all(output for output in outputs)


def test_numpy_default_rng_is_unseeded_and_changes_between_processes() -> None:
    pytest.importorskip("numpy")

    outputs = {run_fixture("numpy_default_rng.py") for _ in range(3)}

    assert len(outputs) >= 2
    assert all(output for output in outputs)


def test_hash_dependent_set_changes_with_python_hash_seed() -> None:
    values = {"alpha", "bravo", "charlie", "delta", "echo", "foxtrot"}
    seed_one = run_fixture(
        "hash_dependent_set.py",
        ExecutionProfile(name="hash-one", environment={"PYTHONHASHSEED": "1"}),
    )
    seed_two = run_fixture(
        "hash_dependent_set.py",
        ExecutionProfile(name="hash-two", environment={"PYTHONHASHSEED": "2"}),
    )

    assert set(seed_one.split("|")) == values
    assert set(seed_two.split("|")) == values
    assert seed_one != seed_two


def test_filesystem_listing_order_changes_with_listing_seed() -> None:
    names = {"alpha.txt", "bravo.txt", "charlie.txt", "delta.txt"}
    seed_one = run_fixture(
        "filesystem_listing.py",
        ExecutionProfile(name="listing-one", listing_seed=1),
    )
    seed_two = run_fixture(
        "filesystem_listing.py",
        ExecutionProfile(name="listing-two", listing_seed=2),
    )

    assert set(seed_one.split("|")) == names
    assert set(seed_two.split("|")) == names
    assert seed_one != seed_two


def test_timezone_date_output_is_controlled_by_profile_timezone() -> None:
    utc = run_fixture(
        "timezone_date.py",
        ExecutionProfile(name="utc", timezone="UTC"),
    )
    kolkata = run_fixture(
        "timezone_date.py",
        ExecutionProfile(name="kolkata", timezone="Asia/Kolkata"),
    )
    los_angeles = run_fixture(
        "timezone_date.py",
        ExecutionProfile(name="los-angeles", timezone="America/Los_Angeles"),
    )
    kiritimati = run_fixture(
        "timezone_date.py",
        ExecutionProfile(name="kiritimati", timezone="Pacific/Kiritimati"),
    )

    assert utc == "2024-01-01"
    assert kolkata == "2024-01-02"
    assert los_angeles == "2024-01-01"
    assert kiritimati == "2024-01-02"


def test_environment_variable_changes_are_visible_in_fixture_output() -> None:
    first = run_fixture(
        "environment_variable.py",
        ExecutionProfile(
            name="first", environment={"CAUSATRACE_FIXTURE_VALUE": "first-value"}
        ),
    )
    second = run_fixture(
        "environment_variable.py",
        ExecutionProfile(
            name="second", environment={"CAUSATRACE_FIXTURE_VALUE": "second-value"}
        ),
    )

    assert first == "first-value"
    assert second == "second-value"
    assert first != second


def test_baseline_flaky_fixture_changes_across_repeated_baselines() -> None:
    outputs = {run_fixture("baseline_flaky.py") for _ in range(3)}

    assert len(outputs) >= 2
    assert all(output.isdigit() for output in outputs)
