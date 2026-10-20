from __future__ import annotations

from pathlib import Path

from causatrace.models import ArtifactSpec, RunRequest
from causatrace.verification import (
    clean_machine_profile,
    verify_clean_machine,
    verify_five_runs,
)


def test_verify_five_runs_requires_exact_hashes_and_keeps_runner_boundary(tmp_path: Path) -> None:
    target = tmp_path / "stable.py"
    target.write_text("print(sorted({'b', 'a'}))\n", encoding="utf-8")
    request = RunRequest(target="stable.py", project_root=str(tmp_path))

    result = verify_five_runs(request)

    assert result.verified is True
    assert result.passed is True
    assert len(result.runs) == 5
    assert len(set(result.output_hashes)) == 1
    assert result.profile.name == "clean-machine"
    assert result.warnings == ()
    assert any("top-level process" in limitation for limitation in result.limitations)
    assert request.profile.name == "default"


def test_clean_machine_profile_can_expose_unrepaired_process_randomness(tmp_path: Path) -> None:
    target = tmp_path / "unstable.py"
    target.write_text("import time\nprint(time.time_ns())\n", encoding="utf-8")
    request = RunRequest(target="unstable.py", project_root=str(tmp_path))

    result = verify_clean_machine(request)

    assert result.profile == clean_machine_profile()
    assert result.profile.inherit_environment is False
    assert result.profile.listing_seed == 37
    assert result.profile.environment["PYTHONHASHSEED"] == "123"
    assert result.verified is False
    assert len(result.runs) == 5
    assert "output SHA-256 hashes differ" in result.warnings[0]


def test_missing_requested_artifact_cannot_verify(tmp_path: Path) -> None:
    target = tmp_path / "stable.py"
    target.write_text("print('ok')\n", encoding="utf-8")
    request = RunRequest(
        target="stable.py",
        project_root=str(tmp_path),
        artifacts=(ArtifactSpec("required.json"),),
    )

    result = verify_five_runs(request)

    assert result.verified is False
    assert any("required.json" in warning for warning in result.warnings)
