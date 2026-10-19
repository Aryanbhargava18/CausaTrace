"""Deterministic terminal and JSON reports for CausaTrace P0."""

from __future__ import annotations

import json
from typing import Any

from .models import AnalysisReport, Location, ProbeResult, StaticFinding


def _location_key(location: Location) -> tuple[Any, ...]:
    return (
        location.path,
        location.line,
        -1 if location.column is None else location.column,
        location.kind,
        location.operation or "",
        location.symbol or "",
        location.confidence,
        location.evidence or "",
    )


def _probe_key(probe: ProbeResult) -> tuple[str, str]:
    return (probe.factor, probe.treatment)


def _static_key(finding: StaticFinding) -> tuple[Any, ...]:
    return (
        finding.rule_id,
        finding.location.path,
        finding.location.line,
        -1 if finding.location.column is None else finding.location.column,
        finding.message,
        finding.suggestion or "",
    )


def _jsonable_report(report: AnalysisReport) -> dict[str, Any]:
    # AnalysisReport owns the shared serialization contract.  Make a shallow
    # copy so a reporter never mutates or reorders the caller's report.  Lists
    # are normalised here so JSON remains stable even if a caller collected
    # probes/findings from an unordered source.
    payload = dict(report.to_dict())
    probes = []
    for probe in payload.get("probes", []):
        value = dict(probe)
        value["locations"] = sorted(
            value.get("locations", []),
            key=lambda location: (
                location.get("path", ""),
                location.get("line", 0),
                -1 if location.get("column") is None else location.get("column"),
                location.get("kind", ""),
                location.get("operation") or "",
            ),
        )
        value["limitations"] = sorted(set(value.get("limitations", [])))
        probes.append(value)
    payload["probes"] = sorted(
        probes,
        key=lambda probe: (probe.get("factor", ""), probe.get("treatment", "")),
    )
    payload["static_findings"] = sorted(
        payload.get("static_findings", []),
        key=lambda finding: (
            finding.get("rule_id", ""),
            finding.get("location", {}).get("path", ""),
            finding.get("location", {}).get("line", 0),
            finding.get("message", ""),
        ),
    )
    payload["limitations"] = sorted(set(payload.get("limitations", [])))
    return payload


def render_json(report: AnalysisReport) -> str:
    """Render a stable machine-readable report with sorted object keys."""

    return json.dumps(
        _jsonable_report(report),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _format_location(location: Location) -> str:
    position = str(location.line)
    if location.column is not None:
        position += f":{location.column}"
    details = [location.kind]
    if location.operation:
        details.append(location.operation)
    if location.confidence != "unknown":
        details.append(f"confidence={location.confidence}")
    return f"{location.path}:{position} ({', '.join(details)})"


def _render_probe(lines: list[str], probe: ProbeResult) -> None:
    percentage = f"{probe.consistency:.3f}"
    lines.append(
        f"- {probe.factor} [{probe.treatment}]: {probe.classification} "
        f"({probe.changed_trials}/{probe.trials} changed, consistency {percentage})"
    )
    if probe.comparison is not None:
        comparison = probe.comparison
        evidence: list[str] = []
        if comparison.stdout_changed:
            evidence.append("stdout SHA-256")
        if comparison.stderr_changed:
            evidence.append("stderr SHA-256")
        if comparison.changed_artifacts:
            evidence.append("artifacts=" + ",".join(comparison.changed_artifacts))
        if comparison.changed_metrics:
            evidence.append("metrics=" + ",".join(comparison.changed_metrics))
        if comparison.max_absolute_difference is not None:
            evidence.append(f"max metric Δ={comparison.max_absolute_difference:g}")
        if evidence:
            lines.append("  Evidence: " + "; ".join(evidence))
    for location in sorted(probe.locations, key=_location_key):
        lines.append("  Runtime location: " + _format_location(location))
    for limitation in sorted(set(probe.limitations)):
        lines.append("  Limitation: " + limitation)


def render_terminal(report: AnalysisReport) -> str:
    """Render a readable, deterministic terminal report.

    Static findings are rendered in their own section and explicitly labelled
    as not being runtime proof.  Runtime locations only appear under probes.
    """

    lines = [
        "CausaTrace report",
        f"Target: {report.target}",
        f"Status: {report.status}",
        "",
        "Runtime factor attribution:",
    ]
    probes = sorted(report.probes, key=_probe_key)
    if probes:
        for probe in probes:
            _render_probe(lines, probe)
    else:
        lines.append("- none")

    lines.extend(("", "Static findings (not runtime proof):"))
    findings = sorted(report.static_findings, key=_static_key)
    if findings:
        for finding in findings:
            lines.append(f"- {finding.rule_id}: {finding.message}")
            lines.append("  Static location: " + _format_location(finding.location))
            if finding.suggestion:
                lines.append("  Suggestion: " + finding.suggestion)
    else:
        lines.append("- none")

    lines.extend(("", "Limitations:"))
    limitations = sorted(set(report.limitations))
    if limitations:
        lines.extend(f"- {limitation}" for limitation in limitations)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


class TerminalReporter:
    """Implementation of the shared ``Reporter`` protocol for terminal text."""

    def render(self, report: AnalysisReport) -> str:
        return render_terminal(report)


class JsonReporter:
    """Implementation of the shared ``Reporter`` protocol for JSON text."""

    def render(self, report: AnalysisReport) -> str:
        return render_json(report)


TextReporter = TerminalReporter
JSONReporter = JsonReporter


def report_terminal(report: AnalysisReport) -> str:
    return render_terminal(report)


def report_json(report: AnalysisReport) -> str:
    return render_json(report)


__all__ = [
    "JSONReporter",
    "JsonReporter",
    "TerminalReporter",
    "TextReporter",
    "render_json",
    "render_terminal",
    "report_json",
    "report_terminal",
]
