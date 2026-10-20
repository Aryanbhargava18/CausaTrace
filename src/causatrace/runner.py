"""Subprocess runner for deterministic, temporary-copy script execution."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Mapping

from .capture import capture_output
from .models import ArtifactSpec, RunRequest, RunResult
from .profiles import CHILD_PROCESS_LIMITATION, build_environment


HARNESS_MARKER_LIMITATION = (
    "The target attempted to read a private CAUSATRACE harness control; marker reads "
    "are not attribution evidence."
)


class SubprocessRunner:
    """Run a Python target in a mandatory temporary copy.

    Only the process hosting the target is instrumented.  Descendants may
    still be started by the target, but are outside the P0 tracing boundary.
    """

    def __init__(
        self,
        *,
        python_executable: str | os.PathLike[str] | None = None,
        base_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.python_executable = str(python_executable or sys.executable)
        self.base_environment = dict(base_environment) if base_environment is not None else None

    @staticmethod
    def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
        # Caches and VCS metadata cannot affect a script's normal result and
        # can make a repository-sized temporary copy unnecessarily expensive.
        ignored = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache"}
        return {name for name in names if name in ignored}

    @staticmethod
    def _resolve_target(request: RunRequest, project_root: Path) -> tuple[Path, Path]:
        target = Path(request.target)
        if not target.is_absolute():
            target = project_root / target
        target = target.resolve()
        if not target.is_file():
            raise FileNotFoundError(f"target script does not exist: {target}")
        try:
            relative = target.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("target must be inside project_root for temporary-copy execution") from exc
        return target, relative

    @staticmethod
    def _event_data(path: Path) -> tuple[dict, ...]:
        if not path.is_file():
            return ()
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return ()
        # New shims append JSONL records.  Keep accepting the former JSON-list
        # shape so traces written by an older shim remain readable.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, list):
            return tuple(item for item in data if isinstance(item, dict))
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return tuple(item for item in data["events"] if isinstance(item, dict))
        records: list[dict] = []
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return tuple(records)

    @staticmethod
    def _normalise_stream(
        value: bytes | str,
        *,
        copy_root: Path,
        project_root: Path,
    ) -> bytes | str:
        """Replace the per-run copy root with the stable logical root.

        Python exposes both ``__file__`` and ``cwd`` to a target.  The runner
        still executes from an isolated copy, but those paths are harness
        implementation details and must not make otherwise stable output vary
        across baseline trials.
        """

        source = str(copy_root)
        destination = str(project_root)
        if isinstance(value, bytes):
            return value.replace(os.fsencode(source), os.fsencode(destination))
        return value.replace(source, destination)

    @staticmethod
    def _remap_event_paths(
        events: tuple[dict, ...],
        copy_root: Path,
        project_root: Path,
    ) -> tuple[dict, ...]:
        """Map trace paths from the temporary copy back to source paths."""

        copy_root = copy_root.resolve()
        project_root = project_root.resolve()

        def remap(value: Any) -> Any:
            if isinstance(value, dict):
                result = dict(value)
                for key in ("path", "file", "filename", "source_path", "code_filename"):
                    raw = result.get(key)
                    if isinstance(raw, str):
                        try:
                            relative = Path(raw).resolve().relative_to(copy_root)
                        except (ValueError, OSError):
                            continue
                        result[key] = str(project_root / relative)
                for key, child in tuple(result.items()):
                    if key in {"path", "file", "filename", "source_path", "code_filename"}:
                        continue
                    result[key] = remap(child)
                return result
            if isinstance(value, list):
                return [remap(item) for item in value]
            if isinstance(value, tuple):
                return tuple(remap(item) for item in value)
            return value

        return tuple(remap(event) for event in events)

    @staticmethod
    def _artifact_root(path: Path, project_root: Path) -> Path:
        if path.is_absolute():
            try:
                return path.resolve().relative_to(project_root)
            except ValueError as exc:
                raise ValueError("artifact must be inside project_root for temporary-copy execution") from exc
        try:
            return (project_root / path).resolve().relative_to(project_root)
        except ValueError as exc:
            raise ValueError("artifact must be inside project_root for temporary-copy execution") from exc

    @classmethod
    def _copy_artifact_specs(
        cls,
        specs: tuple[ArtifactSpec, ...],
        project_root: Path,
    ) -> tuple[tuple[ArtifactSpec, ...], dict[str, str]]:
        """Map source-tree artifact paths to paths inside the copy."""

        copied: list[ArtifactSpec] = []
        display_names: dict[str, str] = {}
        for spec in specs:
            relative = cls._artifact_root(Path(spec.path), project_root)
            copied_path = str(relative)
            copied.append(replace(spec, path=copied_path))
            display_names[copied_path] = spec.path
        return tuple(copied), display_names

    def run(self, request: RunRequest) -> RunResult:
        """Execute ``request`` and return captured output and evidence.

        ``RunRequest.temporary_copy`` is retained for contract compatibility,
        but P0 always copies the project.  No option can accidentally execute
        the user's source tree in place.
        """

        project_root = Path(request.project_root).resolve()
        if not project_root.is_dir():
            raise NotADirectoryError(f"project root does not exist: {project_root}")
        _source_target, target_relative = self._resolve_target(request, project_root)

        with tempfile.TemporaryDirectory(prefix="causatrace-run-") as temporary:
            copy_root = Path(temporary) / "project"
            shutil.copytree(
                project_root,
                copy_root,
                ignore=self._copy_ignore,
            )
            target_copy = copy_root / target_relative
            event_path = Path(temporary) / "events.json"

            environment = build_environment(request.profile, base=self.base_environment)
            # The shim is imported from this checkout/environment rather than
            # copied into the target project.  Keep an existing PYTHONPATH so
            # project-local imports remain available as well.
            package_root = str(Path(__file__).resolve().parent.parent)
            existing_pythonpath = environment.get("PYTHONPATH")
            environment["PYTHONPATH"] = (
                package_root
                if not existing_pythonpath
                else os.pathsep.join((package_root, existing_pythonpath))
            )
            environment.setdefault("PYTHONUNBUFFERED", "1")
            copied_specs, artifact_names = self._copy_artifact_specs(
                request.artifacts,
                project_root,
            )

            command = (
                self.python_executable,
                "-m",
                "causatrace.shim",
                "--target",
                str(target_copy),
                "--events",
                str(event_path),
                "--",
                *request.arguments,
            )
            start = time.monotonic()
            stdout: bytes | str = b""
            stderr: bytes | str = b""
            exit_code: int | None = None
            timed_out = False
            process = subprocess.Popen(
                command,
                cwd=str(copy_root),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            try:
                stdout, stderr = process.communicate(timeout=request.timeout_seconds)
                exit_code = process.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                # Capture any already-buffered output, then stop only the
                # top-level process.  Descendants are intentionally outside
                # the P0 propagation contract.
                process.kill()
                trailing_stdout, trailing_stderr = process.communicate()
                stdout = (exc.stdout or b"") + (trailing_stdout or b"")
                stderr = (exc.stderr or b"") + (trailing_stderr or b"")
            duration = time.monotonic() - start

            captured = capture_output(
                self._normalise_stream(stdout, copy_root=copy_root, project_root=project_root),
                self._normalise_stream(stderr, copy_root=copy_root, project_root=project_root),
                artifact_specs=copied_specs,
                artifact_root=copy_root,
                metric_patterns=request.metric_patterns,
            )
            artifacts = {
                artifact_names.get(path, path): digest
                for path, digest in captured.artifacts.items()
            }
            metrics: dict[str, float] = {}
            for key, value in captured.metrics.items():
                display_key = key
                for copied_path, original_path in artifact_names.items():
                    prefix = f"{copied_path}."
                    if key.startswith(prefix):
                        display_key = f"{original_path}.{key[len(prefix):]}"
                        break
                if display_key in metrics:
                    suffix = 2
                    while f"{display_key}_{suffix}" in metrics:
                        suffix += 1
                    display_key = f"{display_key}_{suffix}"
                metrics[display_key] = value
            recorded_events = self._event_data(event_path)
            events = recorded_events if request.trace else ()
            events = self._remap_event_paths(events, copy_root, project_root)
            limitations = (CHILD_PROCESS_LIMITATION,)
            if any(
                event.get("operation") == "harness.marker_read" for event in recorded_events
            ):
                limitations = (*limitations, HARNESS_MARKER_LIMITATION)
            if not request.trace:
                limitations = (*limitations, "Runtime event tracing was disabled for this request.")
            return RunResult(
                command=command,
                exit_code=None if timed_out else exit_code,
                timed_out=timed_out,
                stdout=captured.stdout,
                stderr=captured.stderr,
                output_hash=captured.output_hash,
                stderr_hash=captured.stderr_hash,
                artifacts=artifacts,
                metrics=metrics,
                trace_events=events,
                duration_seconds=duration,
                limitations=limitations,
            )


# The aliases avoid making callers couple to one implementation spelling.
ProcessRunner = SubprocessRunner
DefaultRunner = SubprocessRunner
TemporaryCopyRunner = SubprocessRunner
Runner = SubprocessRunner


def run(request: RunRequest) -> RunResult:
    """Convenience function for the default runner."""

    return SubprocessRunner().run(request)


run_request = run
run_script = run


__all__ = [
    "CHILD_PROCESS_LIMITATION",
    "HARNESS_MARKER_LIMITATION",
    "DefaultRunner",
    "ProcessRunner",
    "Runner",
    "SubprocessRunner",
    "TemporaryCopyRunner",
    "build_environment",
    "run",
    "run_request",
    "run_script",
]
