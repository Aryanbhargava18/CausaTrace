from __future__ import annotations

from pathlib import Path

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
