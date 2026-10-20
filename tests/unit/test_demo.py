from __future__ import annotations

from pathlib import Path

from causatrace.demo import run_demo


def test_demo_proves_cause_and_rejects_unrepaired_randomness() -> None:
    root = Path(__file__).resolve().parents[2]

    assert run_demo(root) == 0
