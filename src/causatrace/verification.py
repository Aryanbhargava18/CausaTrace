"""Isolated five-run exact-hash verification for repaired source.

Verification delegates execution to :class:`~causatrace.runner.SubprocessRunner`.
That runner already makes a temporary copy and limits instrumentation to the
top-level process, so this module does not add file writes, child-process
management, or orchestration behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from .models import ExecutionProfile, RunRequest, RunResult
from .profiles import baseline_profile
from .runner import CHILD_PROCESS_LIMITATION, SubprocessRunner


def clean_machine_profile(name: str = "clean-machine") -> ExecutionProfile:
    """Return a minimal, host-normalized profile for repeated verification.

    Unlike :func:`baseline_profile`, this profile does not inject Python or
    NumPy random seeds. It uses a different hash seed and a fixed shuffled
    listing order, with a minimal declared environment, so repaired code is
    checked under conditions closer to a clean machine.
    """

    return ExecutionProfile(
        name=name,
        environment={"PYTHONHASHSEED": "123"},
        inherit_environment=False,
        listing_seed=37,
        timezone="UTC",
        locale="C.UTF-8",
    )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Evidence from exactly five top-level subprocess runs."""

    verified: bool
    profile: ExecutionProfile
    runs: tuple[RunResult, ...]
    output_hashes: tuple[str, ...]
    warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Alias suitable for test and report callers."""

        return self.verified

    @property
    def hashes(self) -> tuple[str, ...]:
        """Short alias for the exact stdout SHA-256 sequence."""

        return self.output_hashes


def _run_hash_is_exact(runs: tuple[RunResult, ...]) -> bool:
    if len(runs) != 5:
        return False
    if any(run.exit_code != 0 or run.timed_out for run in runs):
        return False
    # Output, stderr, and artifact hashes are all exact evidence exposed by
    # SubprocessRunner.  Metrics are intentionally not part of this helper's
    # hash contract; callers can compare them through the existing comparator.
    if len({run.output_hash for run in runs}) != 1:
        return False
    if len({run.stderr_hash for run in runs}) != 1:
        return False
    artifact_hashes = {tuple(sorted(run.artifacts.items())) for run in runs}
    return len(artifact_hashes) == 1


def verify_five_runs(
    request: RunRequest,
    *,
    runner: SubprocessRunner | None = None,
    profile: ExecutionProfile | None = None,
) -> VerificationResult:
    """Run one request five times and require exact hash equality.

    ``profile`` defaults to the existing pinned baseline profile.  Pass
    :func:`clean_machine_profile` explicitly to verify a repair without P0's
    random/listing injections.  The request is copied with the selected
    profile; the caller's object is never mutated.
    """

    selected_profile = profile or baseline_profile()
    selected_runner = runner or SubprocessRunner()
    run_request = replace(request, profile=selected_profile)
    runs = tuple(selected_runner.run(run_request) for _ in range(5))
    warnings: list[str] = []
    if any(run.exit_code != 0 for run in runs):
        warnings.append("At least one verification run exited with a non-zero status.")
    if any(run.timed_out for run in runs):
        warnings.append("At least one verification run timed out.")
    if len({run.output_hash for run in runs}) > 1:
        warnings.append("Verification output SHA-256 hashes differ across the five runs.")
    if len({run.stderr_hash for run in runs}) > 1:
        warnings.append("Verification stderr SHA-256 hashes differ across the five runs.")
    if len({tuple(sorted(run.artifacts.items())) for run in runs}) > 1:
        warnings.append("Verification artifact SHA-256 hashes differ across the five runs.")
    limitations = tuple(sorted({item for run in runs for item in run.limitations}))
    if CHILD_PROCESS_LIMITATION not in limitations:
        limitations = tuple(sorted((*limitations, CHILD_PROCESS_LIMITATION)))
    return VerificationResult(
        verified=_run_hash_is_exact(runs),
        profile=selected_profile,
        runs=runs,
        output_hashes=tuple(run.output_hash for run in runs),
        warnings=tuple(warnings),
        limitations=limitations,
    )


def verify_baseline(request: RunRequest, *, runner: SubprocessRunner | None = None) -> VerificationResult:
    """Verify five runs with the pinned baseline profile."""

    return verify_five_runs(request, runner=runner, profile=baseline_profile())


def verify_clean_machine(request: RunRequest, *, runner: SubprocessRunner | None = None) -> VerificationResult:
    """Verify five runs with the clean-machine profile."""

    return verify_five_runs(request, runner=runner, profile=clean_machine_profile())


verify_exact_hashes = verify_five_runs


__all__ = [
    "VerificationResult",
    "clean_machine_profile",
    "verify_baseline",
    "verify_clean_machine",
    "verify_exact_hashes",
    "verify_five_runs",
]
