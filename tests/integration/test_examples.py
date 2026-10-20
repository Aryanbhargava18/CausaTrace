from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from causatrace.models import RunRequest
from causatrace.profiles import baseline_profile, profiles_for_factor
from causatrace.runner import SubprocessRunner


ROOT = Path(__file__).resolve().parents[2]


def _accuracy(result_stdout: str) -> str:
    return next(line.split("=", 1)[1] for line in result_stdout.splitlines() if line.startswith("accuracy="))


def test_model_accuracy_example_exposes_order_and_seed_causes() -> None:
    runner = SubprocessRunner()
    baseline = baseline_profile()
    request = lambda profile: RunRequest(
        target="examples/model_accuracy.py",
        project_root=str(ROOT),
        profile=profile,
    )

    baseline_result = runner.run(request(baseline))
    hash_result = runner.run(request(profiles_for_factor("hash_seed", baseline)[0]))
    listing_result = runner.run(request(profiles_for_factor("filesystem_listing", baseline)[0]))

    assert _accuracy(baseline_result.stdout) == "0.75"
    assert _accuracy(hash_result.stdout) == "0.25"
    assert _accuracy(listing_result.stdout) == "1.00"


def test_flaky_pytest_example_reproduces_seed_specific_failure_line() -> None:
    command = (sys.executable, "-m", "pytest", "examples/flaky_pytest/test_order.py", "-q")
    passing_environment = {**os.environ, "PYTHONHASHSEED": "0"}
    failing_environment = {**os.environ, "PYTHONHASHSEED": "1"}

    passing = subprocess.run(command, cwd=ROOT, env=passing_environment, capture_output=True, text=True)
    failing = subprocess.run(command, cwd=ROOT, env=failing_environment, capture_output=True, text=True)

    assert passing.returncode == 0
    assert failing.returncode == 1
    assert "examples/flaky_pytest/test_order.py:6" in failing.stdout + failing.stderr
