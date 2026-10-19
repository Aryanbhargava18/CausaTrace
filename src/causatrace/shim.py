"""Top-level execution shim used by :mod:`causatrace.runner`.

The shim runs one target with narrowly scoped wrappers around supported P0
operations.  It intentionally does not install hooks in descendants spawned
by the target; the runner reports that boundary as a limitation.
"""

from __future__ import annotations

import argparse
import inspect
import json
import locale
import os
from pathlib import Path
import random
import runpy
import sys
import threading
import time
from collections.abc import Iterator
from typing import Any, Callable, TypeVar


CHILD_PROCESS_LIMITATION = (
    "Child-process tracing is not supported; runtime events cover the top-level process only."
)
_T = TypeVar("_T", bound=Callable[..., Any])


class EventRecorder:
    """Small JSON-lines-independent event sink for a single top-level process.

    The file contains a JSON list so the runner can consume it atomically at
    the end of a run.  Rewriting the short list per event makes events useful
    even if the target exits through an unhandled exception.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._flush()

    def _location(self) -> tuple[str | None, int | None, str]:
        frame = inspect.currentframe()
        try:
            frame = frame.f_back if frame else None
            while frame is not None:
                path = str(frame.f_code.co_filename)
                if Path(path).resolve() != Path(__file__).resolve():
                    line = frame.f_lineno
                    kind = "stdlib" if path.startswith(sys.base_prefix) else "user"
                    return path, line, kind
                frame = frame.f_back
        finally:
            # Break reference cycles created by frame walking.
            del frame
        return None, None, "unknown"

    def record(self, operation: str, **details: Any) -> None:
        path, line, kind = self._location()
        event: dict[str, Any] = {
            "operation": operation,
            "kind": kind,
            "path": path,
            "line": line,
        }
        event.update(details)
        with self._lock:
            self.events.append(event)
            self._flush()

    def _flush(self) -> None:
        if not self.path:
            return
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self.events, sort_keys=True), encoding="utf-8")
        temporary.replace(self.path)


def _int_environment(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _frame_wrapper(
    recorder: EventRecorder,
    operation: str,
    original: _T,
) -> _T:
    """Wrap a supported callable and preserve its ordinary return contract."""

    if getattr(original, "__causatrace_wrapped__", False):
        return original

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        recorder.record(operation, symbol=operation)
        return original(*args, **kwargs)

    setattr(wrapped, "__causatrace_wrapped__", True)
    setattr(wrapped, "__causatrace_original__", original)
    # The cast is safe: the wrapper forwards the callable's arguments exactly.
    return wrapped  # type: ignore[return-value]


def _install_random_hooks(recorder: EventRecorder, seed: int | None) -> None:
    if seed is not None:
        random.seed(seed)
    names = (
        "random",
        "randint",
        "randrange",
        "choice",
        "choices",
        "sample",
        "shuffle",
        "getrandbits",
        "uniform",
        "triangular",
        "gauss",
        "normalvariate",
        "lognormvariate",
        "expovariate",
        "vonmisesvariate",
        "gammavariate",
        "betavariate",
        "paretovariate",
        "weibullvariate",
        "randbytes",
        "seed",
    )
    for name in names:
        original = getattr(random, name, None)
        if original is not None:
            setattr(random, name, _frame_wrapper(recorder, f"random.{name}", original))

    # Direct instances (random.Random(...).random()) are also supported.  Do
    # not replace methods on the class: module-level bound methods are created
    # at import time and replacing the class would not affect that singleton.
    recorder.record("random.profile", seed=seed)


def _install_numpy_hooks(recorder: EventRecorder, seed: int | None) -> None:
    if seed is None:
        return
    try:
        import numpy as np  # type: ignore[import-not-found]
    except ImportError:
        recorder.record("numpy.random.unavailable", seed=seed)
        return

    np.random.seed(seed)
    recorder.record("numpy.random.seed", seed=seed, symbol="numpy.random.seed")
    for name in (
        "random",
        "rand",
        "randn",
        "randint",
        "choice",
        "bytes",
        "random_sample",
        "ranf",
        "sample",
        "uniform",
        "normal",
        "standard_normal",
        "shuffle",
        "permutation",
    ):
        original = getattr(np.random, name, None)
        if original is not None:
            setattr(
                np.random,
                name,
                _frame_wrapper(recorder, f"numpy.random.{name}", original),
            )


class _ScandirResult:
    """A context-manager-compatible shuffled ``os.scandir`` result."""

    def __init__(self, entries: list[os.DirEntry[str] | os.DirEntry[bytes]]) -> None:
        self._iterator: Iterator[os.DirEntry[str] | os.DirEntry[bytes]] = iter(entries)

    def __iter__(self) -> "_ScandirResult":
        return self

    def __next__(self) -> os.DirEntry[str] | os.DirEntry[bytes]:
        return next(self._iterator)

    def close(self) -> None:
        return None

    def __enter__(self) -> "_ScandirResult":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()


def _install_listing_hooks(recorder: EventRecorder, seed: int | None) -> None:
    if seed is None:
        # Listing calls remain visible even in the baseline; only treatment
        # profiles alter their order.
        recorder.record("filesystem.listing.profile", seed=None)

    original_listdir = os.listdir

    def listdir(path: str | bytes | os.PathLike[str] | os.PathLike[bytes] = ".") -> list[str] | list[bytes]:
        result = original_listdir(path)
        recorder.record("os.listdir", symbol="os.listdir")
        if seed is not None:
            randomizer = random.Random(seed)
            randomizer.shuffle(result)
        return result

    setattr(listdir, "__causatrace_wrapped__", True)
    os.listdir = listdir  # type: ignore[assignment]

    original_scandir = os.scandir

    def scandir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes] = ".",
    ) -> _ScandirResult | os.ScandirIterator[str] | os.ScandirIterator[bytes]:
        result = list(original_scandir(path))
        recorder.record("os.scandir", symbol="os.scandir")
        if seed is not None:
            randomizer = random.Random(seed)
            randomizer.shuffle(result)
        return _ScandirResult(result)

    setattr(scandir, "__causatrace_wrapped__", True)
    os.scandir = scandir  # type: ignore[assignment]


def _install_timezone_hooks(recorder: EventRecorder, timezone: str | None) -> None:
    if timezone:
        os.environ["TZ"] = timezone
        if hasattr(time, "tzset"):
            time.tzset()
        recorder.record("timezone.profile", timezone=timezone)

    for module, name in (
        (time, "localtime"),
        (time, "gmtime"),
        (time, "mktime"),
        (time, "strftime"),
    ):
        original = getattr(module, name, None)
        if original is not None:
            setattr(module, name, _frame_wrapper(recorder, f"time.{name}", original))


def _install_hooks(recorder: EventRecorder) -> None:
    timezone = os.environ.get("CAUSATRACE_TIMEZONE")
    if timezone:
        _install_timezone_hooks(recorder, timezone)
    else:
        # The event marks the control state without claiming a timezone call.
        recorder.record("timezone.profile", timezone=None)

    locale_name = os.environ.get("LC_ALL") or os.environ.get("LANG")
    if locale_name:
        try:
            locale.setlocale(locale.LC_ALL, locale_name)
        except locale.Error:
            # A missing locale must not prevent the target from running.  The
            # effective environment remains available in the run metadata.
            recorder.record("locale.unavailable", locale=locale_name)

    _install_random_hooks(recorder, _int_environment("CAUSATRACE_PYTHON_RANDOM_SEED"))
    _install_numpy_hooks(recorder, _int_environment("CAUSATRACE_NUMPY_SEED"))
    _install_listing_hooks(recorder, _int_environment("CAUSATRACE_LISTING_SEED"))

    if "PYTHONHASHSEED" in os.environ:
        recorder.record("python.hash_seed", seed=os.environ["PYTHONHASHSEED"])


def run_target(
    target: str | Path,
    arguments: list[str] | tuple[str, ...] = (),
    *,
    events_path: str | Path | None = None,
) -> int:
    """Run one script in the current top-level interpreter."""

    target_path = Path(target).resolve()
    recorder = EventRecorder(events_path)
    _install_hooks(recorder)
    recorder.record(
        "process.start",
        target=str(target_path),
        child_process_tracing=False,
        limitation=CHILD_PROCESS_LIMITATION,
    )

    # Match ``python script.py args`` semantics: the script directory is the
    # first import location and argv[0] is the target path.
    target_directory = str(target_path.parent)
    if not sys.path or sys.path[0] != target_directory:
        sys.path.insert(0, target_directory)
    sys.argv = [str(target_path), *[str(argument) for argument in arguments]]
    try:
        runpy.run_path(str(target_path), run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        print(code, file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CausaTrace top-level execution shim")
    parser.add_argument("--target", required=True)
    parser.add_argument("--events", required=False)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parsed = build_parser().parse_args(argv)
    arguments = list(parsed.arguments)
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    return run_target(parsed.target, arguments, events_path=parsed.events)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CHILD_PROCESS_LIMITATION", "EventRecorder", "build_parser", "main", "run_target"]
