from __future__ import annotations

import hashlib
from pathlib import Path

from causatrace.models import ArtifactSpec, ExecutionProfile, RunRequest
from causatrace.runner import CHILD_PROCESS_LIMITATION, SubprocessRunner


def test_runner_uses_copy_captures_hashes_metrics_and_events(tmp_path: Path) -> None:
    target = tmp_path / "program.py"
    target.write_text(
        "import os, random\n"
        "from pathlib import Path\n"
        "print(random.random())\n"
        "print(sorted(os.listdir(Path('.'))))\n"
        "Path('result.txt').write_text('score=4.5')\n",
        encoding="utf-8",
    )

    result = SubprocessRunner().run(
        RunRequest(
            target="program.py",
            project_root=str(tmp_path),
            profile=ExecutionProfile(
                name="test",
                python_random_seed=3,
                listing_seed=2,
                timezone="UTC",
            ),
            artifacts=(ArtifactSpec("result.txt", kind="text", metric_pattern=r"score=(?P<score>[-+]?\d+(?:\.\d+)?)"),),
        )
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.output_hash == hashlib.sha256(result.stdout.encode()).hexdigest()
    assert result.artifacts["result.txt"] == hashlib.sha256(b"score=4.5").hexdigest()
    assert result.metrics == {"result.txt.score": 4.5}
    assert any(event["operation"] == "random.random" for event in result.trace_events)
    assert any(event["operation"] == "os.listdir" for event in result.trace_events)
    assert CHILD_PROCESS_LIMITATION in result.limitations
    assert not (tmp_path / "result.txt").exists()


def test_hash_seed_and_timezone_are_process_controls(tmp_path: Path) -> None:
    target = tmp_path / "controls.py"
    target.write_text(
        "import os, time\n"
        "print(hash('causatrace'))\n"
        "print(time.tzname)\n"
        "open('marker.txt', 'w').write(os.environ.get('TZ', ''))\n",
        encoding="utf-8",
    )
    runner = SubprocessRunner()
    utc = runner.run(
        RunRequest(
            target="controls.py",
            project_root=str(tmp_path),
            profile=ExecutionProfile(
                name="utc",
                environment={"PYTHONHASHSEED": "1"},
                timezone="UTC",
            ),
        )
    )
    new_york = runner.run(
        RunRequest(
            target="controls.py",
            project_root=str(tmp_path),
            profile=ExecutionProfile(
                name="new-york",
                environment={"PYTHONHASHSEED": "2"},
                timezone="America/New_York",
            ),
        )
    )

    assert utc.exit_code == new_york.exit_code == 0
    assert utc.stdout != new_york.stdout
    assert "UTC" in utc.stdout
    assert "EST" in new_york.stdout or "EDT" in new_york.stdout
    assert not (tmp_path / "marker.txt").exists()


def test_numpy_profile_is_optional_and_seeds_module_random(tmp_path: Path) -> None:
    target = tmp_path / "numpy_program.py"
    target.write_text(
        "try:\n"
        " import numpy as np\n"
        "except ImportError:\n"
        " print('numpy-unavailable')\n"
        "else:\n"
        " print(float(np.random.random()))\n",
        encoding="utf-8",
    )
    result = SubprocessRunner().run(
        RunRequest(
            target="numpy_program.py",
            project_root=str(tmp_path),
            profile=ExecutionProfile(name="numpy", numpy_seed=11),
        )
    )
    assert result.exit_code == 0
    assert "numpy.random.seed" in {event["operation"] for event in result.trace_events} or result.stdout == "numpy-unavailable\n"


def test_absolute_artifact_paths_are_remapped_into_the_copy(tmp_path: Path) -> None:
    target = tmp_path / "artifact_program.py"
    artifact = tmp_path / "artifact.txt"
    target.write_text(
        "from pathlib import Path\n"
        "Path('artifact.txt').write_text('value=7')\n",
        encoding="utf-8",
    )
    result = SubprocessRunner().run(
        RunRequest(
            target=str(target),
            project_root=str(tmp_path),
            artifacts=(ArtifactSpec(str(artifact), metric_pattern=r"value=(\d+)"),),
        )
    )

    assert result.exit_code == 0
    assert str(artifact) in result.artifacts
    assert result.metrics == {f"{artifact}.value": 7.0}
    assert not artifact.exists()
