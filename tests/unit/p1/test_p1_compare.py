from __future__ import annotations

from pathlib import Path

import pytest

from causatrace.p1_compare import (
    NumericTolerance,
    compare_csv_text,
    compare_numeric,
    parse_junit_xml,
    render_html_report,
    HTMLReportSection,
)


def test_numeric_comparison_reports_absolute_and_relative_evidence() -> None:
    result = compare_numeric(
        100.0,
        100.001,
        tolerance=NumericTolerance(absolute=0.01, relative=0.0),
    )

    assert result.within_tolerance is True
    assert result.absolute_difference == pytest.approx(0.001)
    assert result.relative_difference == pytest.approx(0.00001, rel=1e-5)
    assert result.allowed_difference == pytest.approx(0.01)


def test_numeric_comparison_rejects_invalid_tolerances() -> None:
    with pytest.raises(ValueError):
        NumericTolerance(absolute=-1)
    with pytest.raises(ValueError):
        NumericTolerance(relative=float("inf"))
    with pytest.raises(ValueError):
        compare_numeric(1, 2, absolute_tolerance=0.1, tolerance=NumericTolerance())


def test_csv_comparison_reports_changed_rows_and_numeric_deltas() -> None:
    baseline = "id,score,label\nA,1.0,ok\nB,2.0,ok\n"
    treatment = "id,score,label\nB,2.02,ok\nA,1.0,changed\nC,3.0,new\n"

    result = compare_csv_text(
        baseline,
        treatment,
        key_columns=("id",),
        absolute_tolerance=0.01,
    )

    assert result.changed is True
    assert result.rows_compared == 3
    assert result.numeric_cells_compared == 2
    assert result.max_absolute_difference == pytest.approx(0.02)
    assert result.mean_absolute_difference == pytest.approx(0.01)
    assert [row.status for row in result.changed_rows] == ["changed", "changed", "added"]
    assert result.changed_rows[0].key == ("A",)


def test_junit_parser_handles_failure_error_skip_and_namespaces(tmp_path: Path) -> None:
    xml = """<?xml version='1.0'?>
    <testsuites xmlns='urn:test'>
      <testsuite name='unit'>
        <testcase classname='tests.test_one' name='ok' time='0.1'/>
        <testcase classname='tests.test_one' name='bad' time='0.2'>
          <failure message='assertion'>expected output</failure>
        </testcase>
        <testcase classname='tests.test_one' name='broken'><error>boom</error></testcase>
        <testcase classname='tests.test_one' name='later'><skipped/></testcase>
      </testsuite>
    </testsuites>"""
    path = tmp_path / "results.xml"
    path.write_text(xml, encoding="utf-8")

    result = parse_junit_xml(path)

    assert result.total == 4
    assert result.passed == 1
    assert result.failures == 1
    assert result.errors == 1
    assert result.skipped == 1
    assert result.failed == 2
    assert result.suites == ("unit",)
    assert result.cases[1].message == "assertion"


def test_html_report_is_self_contained_and_escapes_text() -> None:
    html = render_html_report(
        [HTMLReportSection("<title>", "<unsafe>&")],
        title="CausaTrace <P1>",
    )

    assert "<!doctype html>" in html
    assert "CausaTrace &lt;P1&gt;" in html
    assert "&lt;unsafe&gt;&amp;" in html
    assert "http" not in html
