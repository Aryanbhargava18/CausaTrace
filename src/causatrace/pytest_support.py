"""Offline-friendly pytest execution and JUnit XML normalization."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Sequence

from .p1_compare import JUnitReport, parse_junit_xml
from .profiles import baseline_profile, build_environment


@dataclass(frozen=True, slots=True)
class PytestRunResult:
    command: tuple[str, ...]
    exit_code: int
    junit: JUnitReport
    stdout: str
    stderr: str
    limitations: tuple[str, ...]


def run_pytest(
    project_root: str | Path,
    *,
    arguments: Sequence[str] = (),
    timeout_seconds: float = 120.0,
) -> PytestRunResult:
    """Run pytest with a temporary JUnit XML file and parse the result."""

    root = Path(project_root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    with tempfile.TemporaryDirectory(prefix="causatrace-pytest-") as temporary:
        junit_path = Path(temporary) / "results.xml"
        command = (
            sys.executable,
            "-m",
            "pytest",
            *arguments,
            f"--junitxml={junit_path}",
        )
        environment = build_environment(baseline_profile(), base=os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            completed = subprocess.run(
                command,
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            return PytestRunResult(
                command=command,
                exit_code=124,
                junit=JUnitReport(),
                stdout=stdout if isinstance(stdout, str) else stdout.decode(errors="replace"),
                stderr=stderr if isinstance(stderr, str) else stderr.decode(errors="replace"),
                limitations=("pytest execution timed out", "network isolation is not enforced locally"),
            )
        junit = parse_junit_xml(junit_path) if junit_path.is_file() else JUnitReport()
        return PytestRunResult(
            command=command,
            exit_code=completed.returncode,
            junit=junit,
            stdout=completed.stdout,
            stderr=completed.stderr,
            limitations=("network isolation is not enforced locally",),
        )


__all__ = ["PytestRunResult", "run_pytest"]
