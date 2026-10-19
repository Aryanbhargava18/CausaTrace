"""Output and artifact capture helpers for CausaTrace.

The runner deliberately keeps captured output as bytes until hashes have been
calculated.  This makes hashes stable for non-UTF-8 output while still
providing useful replacement-decoded strings to the rest of the application.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .models import ArtifactSpec


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest of *value*."""

    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str, *, encoding: str = "utf-8") -> str:
    """Return the SHA-256 digest of text encoded without normalisation."""

    return sha256_bytes(value.encode(encoding))


def sha256_file(path: str | Path) -> str:
    """Hash a file without loading the complete file into memory."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decode_output(value: bytes | str | None, *, encoding: str = "utf-8") -> str:
    """Decode captured process output while preserving all readable bytes.

    ``subprocess`` returns ``None`` for a stream that was not collected (for
    example on an unusual launch failure).  Treating it as an empty stream
    keeps the result contract total and is friendlier for baseline comparison.
    """

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode(encoding, errors="replace")


# A deliberately strict number pattern.  It accepts integers, decimals and
# scientific notation, but does not accidentally parse a number embedded in a
# word.  Named groups in a caller's pattern take precedence over generated
# metric names below.
NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_numeric_metrics(
    text: str,
    pattern: str | re.Pattern[str],
    *,
    prefix: str | None = None,
) -> dict[str, float]:
    """Extract numeric values selected by a regular expression.

    A pattern may use named groups (``(?P<loss>...)``), ordinary capture
    groups, or no groups.  Named groups become the metric names.  Ordinary
    groups use an obvious ``label=``/``label:`` prefix when present and
    otherwise use ``value``, ``value_2`` ...; a pattern without groups uses
    the matched number as ``value``.  Repeated names receive a numeric suffix
    rather than silently overwriting an earlier measurement.

    This intentionally returns floats because :class:`RunResult` exposes
    metrics as ``Mapping[str, float]``.  Patterns that match non-numeric text
    simply contribute no metric.
    """

    compiled = re.compile(pattern) if isinstance(pattern, str) else pattern
    metrics: dict[str, float] = {}
    for match in compiled.finditer(text):
        values: list[tuple[str, str | None]] = []
        named = match.groupdict()
        if named:
            values.extend(named.items())
        elif match.groups():
            # ``score=(...)`` is a common compact pattern.  Give its first
            # group the human label when one is obvious; callers can always
            # use a named group for an unambiguous name.
            inferred_name = "value"
            if isinstance(pattern, str):
                prefix_text = pattern[: match.start(1) - match.start()]
                label_match = re.search(
                    r"([A-Za-z_]\w*)\s*(?:=|:)\s*[^()]*$",
                    prefix_text,
                )
                if label_match:
                    inferred_name = label_match.group(1)
            values.extend(
                (inferred_name if index == 1 else f"{inferred_name}_{index}", value)
                for index, value in enumerate(match.groups(), start=1)
            )
        else:
            values.append(("value", match.group(0)))

        for name, raw in values:
            number = _number(raw)
            if number is None:
                continue
            key = f"{prefix}.{name}" if prefix else name
            if key in metrics:
                suffix = 2
                while f"{key}_{suffix}" in metrics:
                    suffix += 1
                key = f"{key}_{suffix}"
            metrics[key] = number
    return metrics


# A short alias is useful to callers that do not need to know whether the
# source was an artifact or a process stream.
extract_metrics = extract_numeric_metrics
extract_numbers = extract_numeric_metrics


def _merge_metrics(target: dict[str, float], values: Mapping[str, float]) -> None:
    for key, value in values.items():
        actual = key
        if actual in target:
            suffix = 2
            while f"{key}_{suffix}" in target:
                suffix += 1
            actual = f"{key}_{suffix}"
        target[actual] = value


@dataclass(frozen=True)
class CapturedOutput:
    """A baseline-friendly representation of process streams and artifacts."""

    stdout: str
    stderr: str
    output_hash: str
    stderr_hash: str
    artifacts: Mapping[str, str] = field(default_factory=dict)
    metrics: Mapping[str, float] = field(default_factory=dict)


class OutputCapture:
    """State-free object facade around :func:`capture_output`."""

    def __init__(
        self,
        artifact_specs: Iterable[ArtifactSpec] = (),
        *,
        encoding: str = "utf-8",
    ) -> None:
        self.artifact_specs = tuple(artifact_specs)
        self.encoding = encoding

    def capture(
        self,
        stdout: bytes | str | None,
        stderr: bytes | str | None,
        *,
        artifact_root: str | Path | None = None,
    ) -> CapturedOutput:
        return capture_output(
            stdout,
            stderr,
            artifact_specs=self.artifact_specs,
            artifact_root=artifact_root,
            encoding=self.encoding,
        )


def capture_output(
    stdout: bytes | str | None,
    stderr: bytes | str | None,
    *,
    artifact_specs: Iterable[ArtifactSpec] = (),
    artifact_root: str | Path | None = None,
    encoding: str = "utf-8",
) -> CapturedOutput:
    """Capture streams, artifact hashes and optional numeric metrics.

    Artifact paths are interpreted relative to ``artifact_root`` unless they
    are absolute.  Missing artifacts are omitted; a target that did not write
    an optional artifact should still produce a useful ``RunResult``.
    """

    stdout_bytes = stdout.encode(encoding) if isinstance(stdout, str) else (stdout or b"")
    stderr_bytes = stderr.encode(encoding) if isinstance(stderr, str) else (stderr or b"")
    stdout_text = decode_output(stdout_bytes, encoding=encoding)
    stderr_text = decode_output(stderr_bytes, encoding=encoding)

    artifacts: dict[str, str] = {}
    metrics: dict[str, float] = {}
    root = Path(artifact_root) if artifact_root is not None else None
    combined_text = stdout_text + stderr_text

    for spec in artifact_specs:
        path = Path(spec.path)
        if not path.is_absolute() and root is not None:
            path = root / path
        if path.is_file():
            artifacts[spec.path] = sha256_file(path)
            if spec.metric_pattern:
                try:
                    content = path.read_bytes()
                    artifact_text = decode_output(content, encoding=encoding)
                except OSError:
                    artifact_text = ""
                extracted = extract_numeric_metrics(
                    artifact_text,
                    spec.metric_pattern,
                    prefix=spec.path,
                )
                _merge_metrics(metrics, extracted)
                if not extracted:
                    _merge_metrics(
                        metrics,
                        extract_numeric_metrics(combined_text, spec.metric_pattern),
                    )
        elif spec.metric_pattern:
            # Stream metrics are useful for scripts that expose a scalar but
            # do not create a separate artifact.  Prefer stream matches only
            # when the artifact itself was unavailable.
            extracted = extract_numeric_metrics(
                combined_text,
                spec.metric_pattern,
                prefix=spec.path,
            )
            _merge_metrics(metrics, extracted)

    return CapturedOutput(
        stdout=stdout_text,
        stderr=stderr_text,
        output_hash=sha256_bytes(stdout_bytes),
        stderr_hash=sha256_bytes(stderr_bytes),
        artifacts=artifacts,
        metrics=metrics,
    )


hash_bytes = sha256_bytes
hash_file = sha256_file


__all__ = [
    "CapturedOutput",
    "NUMBER_PATTERN",
    "OutputCapture",
    "capture_output",
    "decode_output",
    "extract_numbers",
    "extract_metrics",
    "extract_numeric_metrics",
    "hash_bytes",
    "hash_file",
    "sha256_bytes",
    "sha256_file",
    "sha256_text",
]
