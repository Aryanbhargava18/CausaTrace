"""Construction of controlled execution environments and P0 factor profiles."""

from __future__ import annotations

import importlib.util
import os
from dataclasses import replace
from typing import Mapping

from .models import ExecutionProfile


CHILD_PROCESS_LIMITATION = (
    "Child-process tracing is not supported; runtime events cover the top-level process only."
)


def build_environment(
    profile: ExecutionProfile,
    *,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment passed to one top-level execution.

    The supplied profile is layered over the host environment (or ``base``),
    then its typed controls are translated to environment variables consumed
    by :mod:`causatrace.shim`.  ``PYTHONHASHSEED`` is intentionally set before
    Python starts; unlike the other controls it cannot be changed safely after
    interpreter startup.
    """

    environment = dict(os.environ if base is None else base)
    environment.update({str(key): str(value) for key, value in profile.environment.items()})

    if profile.python_random_seed is not None:
        environment["CAUSATRACE_PYTHON_RANDOM_SEED"] = str(profile.python_random_seed)
    else:
        environment.pop("CAUSATRACE_PYTHON_RANDOM_SEED", None)
    if profile.numpy_seed is not None:
        environment["CAUSATRACE_NUMPY_SEED"] = str(profile.numpy_seed)
    else:
        environment.pop("CAUSATRACE_NUMPY_SEED", None)
    if profile.listing_seed is not None:
        environment["CAUSATRACE_LISTING_SEED"] = str(profile.listing_seed)
    else:
        environment.pop("CAUSATRACE_LISTING_SEED", None)

    if profile.timezone is not None:
        environment["TZ"] = profile.timezone
        environment["CAUSATRACE_TIMEZONE"] = profile.timezone
    else:
        environment.pop("CAUSATRACE_TIMEZONE", None)
    if profile.locale is not None:
        environment["LC_ALL"] = profile.locale
        environment.setdefault("LANG", profile.locale)
    return environment


def _next_int(value: int | None) -> int:
    return 1 if value is None else value + 1


def _next_hash_seed(environment: Mapping[str, str]) -> str:
    current = environment.get("PYTHONHASHSEED")
    if current is None or current == "random":
        return "1"
    try:
        return str(int(current) + 1)
    except ValueError:
        return "1"


class HashSeedFactor:
    """Probe a changed interpreter hash seed."""

    name = "hash_seed"

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]:
        environment = dict(baseline.environment)
        environment["PYTHONHASHSEED"] = _next_hash_seed(environment)
        return (replace(baseline, name=f"{baseline.name}:{self.name}", environment=environment),)


class PythonRandomFactor:
    """Probe the module-level Python pseudo-random generator."""

    name = "python_random"

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]:
        return (
            replace(
                baseline,
                name=f"{baseline.name}:{self.name}",
                python_random_seed=_next_int(baseline.python_random_seed),
            ),
        )


class NumpyRandomFactor:
    """Probe NumPy's legacy module-level random generator when available."""

    name = "numpy_random"

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]:
        return (
            replace(
                baseline,
                name=f"{baseline.name}:{self.name}",
                numpy_seed=_next_int(baseline.numpy_seed),
            ),
        )


class FilesystemListingFactor:
    """Probe deterministic changes to directory listing order."""

    name = "filesystem_listing"

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]:
        return (
            replace(
                baseline,
                name=f"{baseline.name}:{self.name}",
                listing_seed=_next_int(baseline.listing_seed),
            ),
        )


class TimezoneFactor:
    """Probe a second timezone while retaining the baseline timezone control."""

    name = "timezone"

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]:
        timezones = ("UTC", "Asia/Kolkata", "America/Los_Angeles", "Pacific/Kiritimati")
        return tuple(
            replace(
                baseline,
                name=f"{baseline.name}:{self.name}:{timezone}",
                timezone=timezone,
            )
            for timezone in timezones
            if timezone != baseline.timezone
        )


def baseline_profile(name: str = "baseline") -> ExecutionProfile:
    """Return the pinned P0 control profile."""

    return ExecutionProfile(
        name=name,
        environment={"PYTHONHASHSEED": "0"},
        python_random_seed=0,
        numpy_seed=0,
        timezone="UTC",
        locale="C.UTF-8",
    )


# Descriptive aliases make the public surface discoverable without forcing
# callers to depend on one naming convention.
HashSeedFactorProbe = HashSeedFactor
PythonRandomFactorProbe = PythonRandomFactor
NumpyRandomFactorProbe = NumpyRandomFactor
FilesystemListingFactorProbe = FilesystemListingFactor
TimezoneFactorProbe = TimezoneFactor


def numpy_available() -> bool:
    """Return whether the current locked environment contains NumPy."""

    return importlib.util.find_spec("numpy") is not None


def default_factor_probes(*, include_numpy: bool | None = None) -> tuple[object, ...]:
    """Return the P0 probes in stable order.

    NumPy is optional in the execution environment.  A caller can force its
    inclusion for a controlled test with ``include_numpy=True`` or suppress it
    with ``False``.
    """

    if include_numpy is None:
        include_numpy = numpy_available()
    probes: list[object] = [
        HashSeedFactor(),
        PythonRandomFactor(),
    ]
    if include_numpy:
        probes.append(NumpyRandomFactor())
    probes.extend([FilesystemListingFactor(), TimezoneFactor()])
    return tuple(probes)


def profiles_for_factor(
    factor: str,
    baseline: ExecutionProfile,
    *,
    include_numpy: bool | None = None,
) -> tuple[ExecutionProfile, ...]:
    """Return the treatment profiles for one named P0 factor."""

    normalized = factor.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "pythonhashseed": "hash_seed",
        "pyth_hash_seed": "hash_seed",
        "python_random": "python_random",
        "numpy": "numpy_random",
        "numpy_random": "numpy_random",
        "filesystem": "filesystem_listing",
        "filesystem_listing": "filesystem_listing",
        "listing": "filesystem_listing",
        "tz": "timezone",
        "timezone": "timezone",
        "hash_seed": "hash_seed",
    }
    normalized = aliases.get(normalized, normalized)
    for probe in default_factor_probes(include_numpy=include_numpy):
        if probe.name == normalized:
            return probe.profiles(baseline)
    raise ValueError(f"unsupported P0 factor: {factor}")


def p0_factor_profiles(
    baseline: ExecutionProfile,
    *,
    include_numpy: bool | None = None,
) -> dict[str, tuple[ExecutionProfile, ...]]:
    """Return all available P0 treatment profiles keyed by factor name."""

    return {
        probe.name: probe.profiles(baseline)
        for probe in default_factor_probes(include_numpy=include_numpy)
    }


def all_released_profile(name: str = "released") -> ExecutionProfile:
    """Return a deterministic profile that releases all supported controls."""

    return ExecutionProfile(
        name=name,
        environment={"PYTHONHASHSEED": "1"},
        python_random_seed=1,
        numpy_seed=1,
        listing_seed=1,
        timezone="Pacific/Kiritimati",
        locale="C.UTF-8",
    )


# Alternate spelling used by some callers.
released_profile = all_released_profile


__all__ = [
    "CHILD_PROCESS_LIMITATION",
    "FilesystemListingFactor",
    "FilesystemListingFactorProbe",
    "HashSeedFactor",
    "HashSeedFactorProbe",
    "NumpyRandomFactor",
    "NumpyRandomFactorProbe",
    "PythonRandomFactor",
    "PythonRandomFactorProbe",
    "TimezoneFactor",
    "TimezoneFactorProbe",
    "all_released_profile",
    "baseline_profile",
    "build_environment",
    "default_factor_probes",
    "numpy_available",
    "p0_factor_profiles",
    "profiles_for_factor",
    "released_profile",
]
