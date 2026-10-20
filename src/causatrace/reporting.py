"""Deterministic terminal and JSON reports for CausaTrace P0."""

from __future__ import annotations

import json
from html import escape
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


def _html_status_class(classification: str) -> str:
    return {
        "factor_supported": "supported",
        "factor_not_observed": "not-observed",
        "interaction_suspected": "interaction",
        "inconclusive": "inconclusive",
    }.get(classification, "neutral")


def render_html(report: AnalysisReport) -> str:
    """Render a self-contained HTML report with an inline consistency chart."""

    probes = sorted(report.probes, key=_probe_key)
    rows: list[str] = []
    bars: list[str] = []
    variance_values = [
        (probe, probe.comparison.max_absolute_difference)
        for probe in probes
        if probe.comparison is not None and probe.comparison.max_absolute_difference is not None
    ]
    for index, probe in enumerate(probes):
        css = _html_status_class(probe.classification)
        label = escape(f"{probe.factor}: {probe.treatment}")
        comparison = probe.comparison
        evidence = escape(comparison.explanation if comparison else "No comparison evidence.")
        rows.append(
            f"<tr class='{css}'><td>{label}</td><td>{escape(probe.classification)}</td>"
            f"<td>{probe.changed_trials}/{probe.trials}</td><td>{probe.consistency:.3f}</td>"
            f"<td>{evidence}</td></tr>"
        )
        x = 20 + index * 150
        height = max(2, int(120 * probe.consistency))
        y = 150 - height
        bars.append(
            f"<rect class='{css}' x='{x}' y='{y}' width='100' height='{height}'>"
            f"<title>{label}: {probe.consistency:.3f}</title></rect>"
        )
        bars.append(f"<text x='{x}' y='170'>{escape(probe.factor[:18])}</text>")

    findings = "".join(
        f"<li><code>{escape(finding.rule_id)}</code> {escape(finding.message)} "
        f"at <code>{escape(finding.location.path)}:{finding.location.line}</code></li>"
        for finding in sorted(report.static_findings, key=_static_key)
    ) or "<li>None</li>"
    limitations = "".join(
        f"<li>{escape(limitation)}</li>" for limitation in sorted(set(report.limitations))
    ) or "<li>None</li>"
    table = "".join(rows) or "<tr><td colspan='5'>No factor probes ran.</td></tr>"
    chart_width = max(360, 40 + len(probes) * 150)
    if variance_values:
        maximum = max(value for _probe, value in variance_values) or 1.0
        variance_bars: list[str] = []
        for index, (probe, value) in enumerate(variance_values):
            x = 20 + index * 150
            height = max(2, int(120 * value / maximum))
            y = 150 - height
            label = escape(f"{probe.factor}: {probe.treatment}")
            variance_bars.append(
                f"<rect class='{_html_status_class(probe.classification)}' x='{x}' y='{y}' width='100' height='{height}'>"
                f"<title>{label}: {value:g}</title></rect>"
            )
            variance_bars.append(f"<text x='{x}' y='170'>{escape(probe.factor[:18])}</text>")
        variance_chart = (
            f"<svg role='img' aria-label='Numeric variance chart' width='{max(360, 40 + len(variance_values) * 150)}' height='190' viewBox='0 0 {max(360, 40 + len(variance_values) * 150)} 190'>"
            f"<line x1='10' y1='150' x2='{max(350, 30 + len(variance_values) * 150)}' y2='150' stroke='#8993a4'/>{''.join(variance_bars)}</svg>"
        )
    else:
        variance_chart = "<p>No numeric metric variance was captured for these probes.</p>"
    return f"""<!doctype html>
<html lang='en'>
<head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>CausaTrace report</title>
<style>
body{{font-family:system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#172033}}
table{{border-collapse:collapse;width:100%;font-size:.9rem}}th,td{{border:1px solid #d6dbe5;padding:.5rem;text-align:left;vertical-align:top}}
th{{background:#edf1f7}}.supported{{fill:#16803c;background:#e5f6ea}}.not-observed{{fill:#6b7280;background:#f3f4f6}}
.interaction{{fill:#b7791f;background:#fff8e1}}.inconclusive{{fill:#b42318;background:#fff0ee}}.neutral{{fill:#42526e;background:#f5f7fa}}
svg{{border:1px solid #d6dbe5;background:#fff;max-width:100%;overflow:auto}}svg text{{font-size:11px;fill:#172033}}
code,pre{{font-family:ui-monospace,SFMono-Regular,monospace}}section{{margin:2rem 0}}
</style></head>
<body><h1>CausaTrace report</h1><p><strong>Target:</strong> {escape(report.target)}<br>
<strong>Status:</strong> <code>{escape(report.status)}</code></p>
<section><h2>Factor consistency</h2>
<svg role='img' aria-label='Factor consistency chart' width='{chart_width}' height='190' viewBox='0 0 {chart_width} 190'>
<line x1='10' y1='150' x2='{chart_width - 10}' y2='150' stroke='#8993a4'/>
{''.join(bars)}</svg></section>
<section><h2>Numeric variance</h2>{variance_chart}</section>
<section><h2>Runtime factor attribution</h2><table><thead><tr><th>Factor</th><th>Classification</th><th>Changed</th><th>Consistency</th><th>Evidence</th></tr></thead>
<tbody>{table}</tbody></table></section>
<section><h2>Static findings (not runtime proof)</h2><ul>{findings}</ul></section>
<section><h2>Limitations</h2><ul>{limitations}</ul></section>
<section><h2>Machine-readable snapshot</h2><pre>{escape(render_json(report))}</pre></section>
</body></html>
"""


class TerminalReporter:
    """Implementation of the shared ``Reporter`` protocol for terminal text."""

    def render(self, report: AnalysisReport) -> str:
        return render_terminal(report)


class JsonReporter:
    """Implementation of the shared ``Reporter`` protocol for JSON text."""

    def render(self, report: AnalysisReport) -> str:
        return render_json(report)


class HtmlReporter:
    """Implementation of the shared reporter protocol for self-contained HTML."""

    def render(self, report: AnalysisReport) -> str:
        return render_html(report)


TextReporter = TerminalReporter
JSONReporter = JsonReporter
HTMLReporter = HtmlReporter


def report_terminal(report: AnalysisReport) -> str:
    return render_terminal(report)


def report_json(report: AnalysisReport) -> str:
    return render_json(report)


def report_html(report: AnalysisReport) -> str:
    return render_html(report)


__all__ = [
    "JSONReporter",
    "JsonReporter",
    "HTMLReporter",
    "HtmlReporter",
    "TerminalReporter",
    "TextReporter",
    "render_json",
    "render_html",
    "render_terminal",
    "report_json",
    "report_html",
    "report_terminal",
]
