"""Deterministic, offline evaluator demo for the shipped planted fixtures."""

from __future__ import annotations

from pathlib import Path

from .attribution import FACTOR_SUPPORTED
from .models import RunRequest
from .orchestrator import AnalysisOrchestrator
from .reporting import render_terminal
from .verification import verify_five_runs


def run_demo(project_root: str | Path) -> int:
    """Run one positive causal proof and two verification guardrails.

    The demo returns zero only when CausaTrace both identifies the planted hash
    factor and distinguishes a stable control from an unrepaired random script.
    The random script's expected verification failure is therefore a success of
    the demo, not a command failure.
    """

    root = Path(project_root).resolve()
    orchestrator = AnalysisOrchestrator()
    causal_report = orchestrator.analyze(
        root / "tests" / "fixtures" / "hash_dependent_set.py",
        project_root=root,
        include_numpy=True,
    )
    supported = [
        probe
        for probe in causal_report.probes
        if probe.factor == "hash_seed" and probe.classification == FACTOR_SUPPORTED
    ]

    clean_request = RunRequest(
        target="tests/fixtures/clean_control.py",
        project_root=str(root),
    )
    random_request = RunRequest(
        target="tests/fixtures/unseeded_python_random.py",
        project_root=str(root),
    )
    clean_verification = verify_five_runs(clean_request)
    random_verification = verify_five_runs(random_request)

    print("CausaTrace evaluator demo")
    print("=" * 28)
    print("1. Controlled causal intervention")
    print(render_terminal(causal_report), end="")
    print("2. Verification guardrails")
    print(
        f"- clean control: {'PASS' if clean_verification.verified else 'FAIL'} "
        f"({len(set(clean_verification.output_hashes))} output hash(es))"
    )
    print(
        f"- unrepaired random script: {'EXPECTED FAIL' if not random_verification.verified else 'UNSAFE PASS'} "
        f"({len(set(random_verification.output_hashes))} output hashes)"
    )
    print("- evidence: five-run stdout/stderr/artifact hash verification")

    return 0 if supported and clean_verification.verified and not random_verification.verified else 1


__all__ = ["run_demo"]
