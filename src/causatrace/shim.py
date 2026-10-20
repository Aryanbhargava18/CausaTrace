"""Top-level execution shim used by :mod:`causatrace.runner`.

The shim runs one target with narrowly scoped wrappers around supported P0
operations.  It intentionally does not install hooks in descendants spawned
by the target; the runner reports that boundary as a limitation.
"""

from __future__ import annotations

import argparse
from collections.abc import MutableMapping
import inspect
import json
import locale
import math
import os
from pathlib import Path
import random
import runpy
import sys
import sysconfig
import threading
import time
from collections.abc import Iterator
from functools import wraps
from typing import Any, Callable, TypeVar

from .profiles import HARNESS_ENV_NAMES


CHILD_PROCESS_LIMITATION = (
    "Child-process tracing is not supported; runtime events cover the top-level process only."
)
_T = TypeVar("_T", bound=Callable[..., Any])
_MAX_STACK_FRAMES = 8
_MAX_EVENTS = 4096
_MAX_SAFE_DEPTH = 4
_MAX_SAFE_ITEMS = 64
_MAX_SAFE_STRING = 4096


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Convert an arbitrary event detail into bounded JSON data."""

    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and len(value) > _MAX_SAFE_STRING:
            return value[:_MAX_SAFE_STRING] + "..."
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if depth >= _MAX_SAFE_DEPTH:
        try:
            return str(value)[:_MAX_SAFE_STRING]
        except Exception:
            return f"<{type(value).__name__}>"
    if isinstance(value, bytes):
        encoded = value.hex()
        return encoded[:_MAX_SAFE_STRING] + ("..." if len(encoded) > _MAX_SAFE_STRING else "")
    if isinstance(value, dict):
        return {
            str(key): _json_safe(child, depth=depth + 1)
            for index, (key, child) in enumerate(value.items())
            if index < _MAX_SAFE_ITEMS
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        values = list(value)
        result = [_json_safe(child, depth=depth + 1) for child in values[:_MAX_SAFE_ITEMS]]
        if len(values) > _MAX_SAFE_ITEMS:
            result.append(f"<{len(values) - _MAX_SAFE_ITEMS} items omitted>")
        return result
    try:
        return str(value)[:_MAX_SAFE_STRING]
    except Exception:
        return f"<{type(value).__name__}>"


class EventRecorder:
    """Bounded append-only JSONL event sink for one top-level process.

    Appending one bounded JSON object per event avoids rewriting all prior
    events.  Each record is flushed immediately so a timeout or an unhandled
    target exception still leaves the prefix of the trace readable.
    """

    def __init__(self, path: str | Path | None, *, max_events: int = _MAX_EVENTS) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        self.max_events = max(1, int(max_events))
        self._dropped_events = 0
        self._truncation_written = False
        self._lock = threading.Lock()
        self._stream = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = self.path.open("w", encoding="utf-8")

    def close(self) -> None:
        with self._lock:
            if self._stream is None:
                return
            if self._dropped_events and not self._truncation_written:
                self._append_unlocked(
                    {
                        "operation": "runtime.events_truncated",
                        "kind": "unknown",
                        "path": None,
                        "line": None,
                        "stack": [],
                        "dropped": self._dropped_events,
                    }
                )
                self._truncation_written = True
            self._flush()
            self._stream.close()
            self._stream = None

    def __enter__(self) -> "EventRecorder":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    @property
    def dropped_events(self) -> int:
        return self._dropped_events

    def _is_stdlib(self, path: str) -> bool:
        stdlib_root = Path(sysconfig.get_paths().get("stdlib", "")).resolve()
        try:
            return Path(path).resolve().is_relative_to(stdlib_root)
        except (OSError, ValueError):
            return False

    def _stack(self) -> list[dict[str, Any]]:
        """Return a small, serializable stack of user Python frames."""

        stack: list[dict[str, Any]] = []
        frame = inspect.currentframe()
        shim_path = Path(__file__).resolve()
        try:
            frame = frame.f_back if frame else None
            while frame is not None and len(stack) < _MAX_STACK_FRAMES:
                path = str(frame.f_code.co_filename)
                try:
                    internal = Path(path).resolve() == shim_path
                except (OSError, ValueError):
                    internal = False
                if not path.startswith("<") and not internal and not self._is_stdlib(path):
                    stack.append(
                        {
                            "path": path,
                            "line": frame.f_lineno,
                            "function": frame.f_code.co_name,
                        }
                    )
                frame = frame.f_back
        finally:
            del frame
        return stack

    def _location(self) -> tuple[str | None, int | None, str]:
        stack = self._stack()
        if stack:
            return stack[0]["path"], stack[0]["line"], "user"
        return None, None, "unknown"

    def record(self, operation: str, **details: Any) -> None:
        path, line, kind = self._location()
        event: dict[str, Any] = {
            "operation": operation,
            "kind": kind,
            "path": path,
            "line": line,
        }
        event.update({str(key): _json_safe(value) for key, value in details.items()})
        # ``stack`` is reserved evidence owned by the recorder, even if a
        # future hook happens to pass a conflicting detail name.
        event["stack"] = self._stack()
        event = _json_safe(event)
        with self._lock:
            # Reserve one slot for a truncation marker so consumers can tell
            # a complete trace from a capped one.
            if len(self.events) >= max(0, self.max_events - 1):
                self._dropped_events += 1
                return
            self._append_unlocked(event)

    def _append_unlocked(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        if self._stream is not None:
            self._stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
            self._flush()

    def _flush(self) -> None:
        if self._stream is None:
            return
        self._stream.flush()


class _ScrubbedEnvironment(MutableMapping[str, str]):
    """Environment proxy that hides harness controls and reports access."""

    def __init__(self, backing: MutableMapping[str, str], recorder: EventRecorder) -> None:
        self._backing = backing
        self._recorder = recorder

    @staticmethod
    def _name(key: object) -> str | None:
        if isinstance(key, bytes):
            try:
                return os.fsdecode(key)
            except (UnicodeDecodeError, ValueError):
                return None
        return key if isinstance(key, str) else None

    def _marker(self, key: object, access: str) -> bool:
        name = self._name(key)
        if name not in HARNESS_ENV_NAMES:
            return False
        self._recorder.record("harness.marker_read", name=name, access=access)
        return True

    def __getitem__(self, key: str) -> str:
        if self._marker(key, "getitem"):
            raise KeyError(key)
        return self._backing[key]

    def __setitem__(self, key: str, value: str) -> None:
        if self._marker(key, "setitem"):
            return
        self._backing[key] = value

    def __delitem__(self, key: str) -> None:
        if self._marker(key, "delitem"):
            return
        del self._backing[key]

    def __iter__(self) -> Iterator[str]:
        return (key for key in self._backing if key not in HARNESS_ENV_NAMES)

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __repr__(self) -> str:
        return repr(dict(self))

    def copy(self) -> dict[str, str]:
        return dict(self)

    def __contains__(self, key: object) -> bool:
        if self._marker(key, "contains"):
            return False
        return key in self._backing

    def get(self, key: str, default: Any = None) -> Any:
        if self._marker(key, "get"):
            return default
        return self._backing.get(key, default)


def _scrub_harness_environment(recorder: EventRecorder) -> None:
    """Remove controls from the process environment before target code runs."""

    backing = os.environ
    for name in HARNESS_ENV_NAMES:
        try:
            del backing[name]
        except KeyError:
            pass
    os.environ = _ScrubbedEnvironment(backing, recorder)  # type: ignore[assignment]

    # ``os.getenvb`` consults this separate mapping on POSIX.  Keep the same
    # private-channel guarantee for byte-key callers.
    environb = getattr(os, "environb", None)
    if environb is not None:
        for name in HARNESS_ENV_NAMES:
            try:
                del environb[os.fsencode(name)]
            except KeyError:
                pass
        os.environb = _ScrubbedEnvironment(environb, recorder)  # type: ignore[assignment]


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

    original_default_rng = getattr(np.random, "default_rng", None)
    if original_default_rng is not None:
        @wraps(original_default_rng)
        def default_rng(*args: Any, **kwargs: Any) -> Any:
            explicit_seed = args[0] if args else kwargs.get("seed")
            has_explicit_seed = bool(args) or "seed" in kwargs
            controlled = not has_explicit_seed and seed is not None
            recorder.record(
                "numpy.random.default_rng",
                seed=explicit_seed if has_explicit_seed else seed,
                controlled=controlled,
                symbol="numpy.random.default_rng",
            )
            if controlled:
                return original_default_rng(seed)
            # Preserve the original call exactly, including keyword-only
            # forms such as ``default_rng(seed=17)`` and future arguments.
            return original_default_rng(*args, **kwargs)

        setattr(default_rng, "__causatrace_wrapped__", True)
        setattr(default_rng, "__causatrace_original__", original_default_rng)
        np.random.default_rng = default_rng


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
            result.sort(key=lambda value: value)
            if seed != 0:
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
            result.sort(key=lambda entry: entry.name)
            if seed != 0:
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
    try:
        _install_hooks(recorder)
        # The shim consumes all harness controls before target code starts.
        # Remaining reads are reported by the proxy rather than exposing the
        # control values as ordinary application inputs.
        _scrub_harness_environment(recorder)
        recorder.record(
            "process.start",
            target=str(target_path),
            child_process_tracing=False,
            limitation=CHILD_PROCESS_LIMITATION,
        )

        # Match ``python script.py args`` semantics: the script directory is
        # the first import location and argv[0] is the target path.
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
    finally:
        recorder.close()


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
