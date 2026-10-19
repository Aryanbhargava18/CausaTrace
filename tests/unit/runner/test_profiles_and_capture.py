from __future__ import annotations

import hashlib

from causatrace.capture import OutputCapture, extract_numeric_metrics
from causatrace.models import ArtifactSpec, ExecutionProfile
from causatrace.profiles import build_environment, profiles_for_factor


def test_profile_controls_are_translated_without_network_or_global_state() -> None:
    profile = ExecutionProfile(
        name="treatment",
        environment={"PYTHONHASHSEED": "19"},
        python_random_seed=4,
        numpy_seed=5,
        listing_seed=6,
        timezone="UTC",
        locale="C.UTF-8",
    )
    environment = build_environment(profile, base={"BASE": "kept"})

    assert environment["BASE"] == "kept"
    assert environment["PYTHONHASHSEED"] == "19"
    assert environment["CAUSATRACE_PYTHON_RANDOM_SEED"] == "4"
    assert environment["CAUSATRACE_NUMPY_SEED"] == "5"
    assert environment["CAUSATRACE_LISTING_SEED"] == "6"
    assert environment["TZ"] == "UTC"
    assert profiles_for_factor("PYTHONHASHSEED", profile)[0].environment["PYTHONHASHSEED"] == "20"


def test_capture_preserves_raw_hash_and_extracts_named_numbers() -> None:
    capture = OutputCapture(
        (ArtifactSpec("missing.txt", metric_pattern=r"score=(?P<score>[-+]?\d+(?:\.\d+)?)"),)
    ).capture(b"score=2.5\n", b"warning\n")

    assert capture.stdout == "score=2.5\n"
    assert capture.output_hash == hashlib.sha256(b"score=2.5\n").hexdigest()
    assert capture.stderr_hash == hashlib.sha256(b"warning\n").hexdigest()
    assert capture.metrics == {"missing.txt.score": 2.5}
    assert extract_numeric_metrics("loss=1.25", r"loss=(\d+\.\d+)") == {"loss": 1.25}
