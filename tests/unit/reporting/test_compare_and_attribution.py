from __future__ import annotations

import json

from causatrace.attribution import (
    BASELINE_UNSTABLE,
    FACTOR_NOT_OBSERVED,
    FACTOR_SUPPORTED,
    INCONCLUSIVE,
    INTERACTION_SUSPECTED,
    Attributor,
    attribute_factor,
)
from causatrace.models import AnalysisReport, Location, OutputComparison, ProbeResult, RunResult, StaticFinding
from causatrace.reporting import render_json, render_terminal


def result(
    digest: str,
    *,
    metric: float = 1.0,
    artifact: str = "artifact-hash",
    exit_code: int | None = 0,
) -> RunResult:
    return RunResult(
        command=("python", "program.py"),
        exit_code=exit_code,
        timed_out=False,
        stdout=digest,
        stderr="",
        output_hash=digest,
        stderr_hash="stderr",
        artifacts={"result.bin": artifact},
        metrics={"score": metric},
    )


def test_comparison_uses_exact_hashes_artifacts_and_metrics() -> None:
    from causatrace.compare import ExactComparator

    comparison = ExactComparator().compare(
        result("same", metric=1.0, artifact="a"),
        result("same", metric=1.0000000001, artifact="b"),
    )

    assert comparison.changed is True
    assert comparison.stdout_changed is False
    assert comparison.changed_artifacts == ("result.bin",)
    assert comparison.changed_metrics == ("score",)
    assert comparison.max_absolute_difference == 1.000000082740371e-10
    assert "no tolerance" in comparison.explanation


def test_paired_trials_classify_supported_not_observed_and_interaction() -> None:
    baseline = [result("same"), result("same"), result("same")]
    supported = attribute_factor("random", baseline, [result("a"), result("b"), result("c")])
    absent = attribute_factor("random", baseline, baseline)
    mixed = attribute_factor("random", baseline, [result("a"), result("same"), result("c")])

    assert supported.classification == FACTOR_SUPPORTED
    assert supported.changed_trials == 3
    assert supported.consistency == 1.0
    assert absent.classification == FACTOR_NOT_OBSERVED
    assert mixed.classification == INTERACTION_SUSPECTED
    assert mixed.changed_trials == 2


def test_unstable_baseline_protects_probe_and_report_status() -> None:
    unstable = [result("a"), result("b"), result("a")]
    probe = attribute_factor("random", unstable, [result("x"), result("y"), result("z")])
    report = Attributor().analyze(
        target="program.py",
        baseline=unstable[0],
        baseline_repeat=unstable[1],
        probes=(probe,),
    )

    assert probe.classification == INCONCLUSIVE
    assert BASELINE_UNSTABLE == report.status
    assert report.probes[0].classification == INCONCLUSIVE
    assert "Baseline repeats" in " ".join(report.limitations)


def test_all_baseline_trials_are_checked_not_only_the_first_repeat() -> None:
    baselines = [result("same"), result("same"), result("late-change")]
    report = Attributor().analyze(
        target="program.py",
        baseline=baselines[0],
        baseline_repeat=baselines[1],
        baseline_trials=baselines,
    )

    assert report.status == BASELINE_UNSTABLE


def test_terminal_and_json_keep_static_findings_separate_and_deterministic() -> None:
    probe = ProbeResult(
        factor="random",
        treatment="treatment",
        trials=1,
        changed_trials=1,
        consistency=1.0,
        comparison=OutputComparison(
            changed=True,
            stdout_changed=True,
            stderr_changed=False,
            changed_artifacts=(),
            changed_metrics=(),
            explanation="changed",
        ),
        locations=(Location("program.py", 7, kind="runtime_user", operation="random.random"),),
        classification=FACTOR_SUPPORTED,
    )
    report = AnalysisReport(
        schema_version="1.0",
        target="program.py",
        status="stable_baseline",
        probes=(probe,),
        static_findings=(
            StaticFinding(
                "R001",
                "uses an unordered operation",
                Location("program.py", 3, kind="static_rule", confidence="heuristic"),
                "sort the values",
            ),
        ),
    )

    terminal = render_terminal(report)
    assert "Runtime location: program.py:7" in terminal
    assert "Static findings (not runtime proof):" in terminal
    assert "Static location: program.py:3" in terminal

    first = render_json(report)
    second = render_json(report)
    assert first == second
    payload = json.loads(first)
    assert payload["static_findings"][0]["rule_id"] == "R001"
    assert payload["probes"][0]["locations"][0]["kind"] == "runtime_user"
