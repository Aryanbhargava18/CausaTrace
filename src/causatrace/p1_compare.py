"""Small, dependency-free helpers for CausaTrace P1 evaluations.

P0's :class:`causatrace.compare.ExactComparator` intentionally remains an
exact, hash-based comparison.  This module is an opt-in evaluation layer for
artifacts which are naturally compared with tolerances or structured parsers.
It has no dependency on the runner, orchestrator, CLI, or P0 models so it can
also be used by an evaluation script on its own.

The CSV and JUnit readers accept either text/bytes, file-like objects, or
``pathlib.Path`` instances.  All parsing is performed with the Python
standard library.
"""

from __future__ import annotations

import csv
import io
import math
import os
from dataclasses import dataclass, field
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TextIO
from xml.etree import ElementTree


Number = int | float
CSVSource = str | bytes | os.PathLike[str] | TextIO
XMLSource = str | bytes | os.PathLike[str] | TextIO


@dataclass(frozen=True, slots=True)
class NumericTolerance:
    """Absolute and relative tolerance used for an evaluation comparison.

    A value is within tolerance when its absolute difference is at most
    ``max(absolute, relative * max(abs(expected), abs(actual)))``.  This is
    the same scale rule used by :func:`math.isclose` and makes the behavior
    predictable around both zero and large values.
    """

    absolute: float = 0.0
    relative: float = 0.0

    def __post_init__(self) -> None:
        for name, value in (("absolute", self.absolute), ("relative", self.relative)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} tolerance must be a real number")
            if not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{name} tolerance must be finite and non-negative")

    def limit(self, expected: float, actual: float) -> float:
        """Return the permitted absolute difference for two finite values."""

        return max(float(self.absolute), float(self.relative) * max(abs(expected), abs(actual)))


@dataclass(frozen=True, slots=True)
class NumericComparison:
    """Evidence from comparing two numeric values."""

    expected: float
    actual: float
    absolute_difference: float
    relative_difference: float
    allowed_difference: float
    within_tolerance: bool

    @property
    def changed(self) -> bool:
        """Whether the values differ beyond the configured tolerance."""

        return not self.within_tolerance


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{label} must be a real number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{label} must be finite")
    return converted


def compare_numeric(
    expected: Number,
    actual: Number,
    *,
    tolerance: NumericTolerance | None = None,
    absolute_tolerance: float = 0.0,
    relative_tolerance: float = 0.0,
) -> NumericComparison:
    """Compare two numbers using absolute and/or relative tolerance.

    ``tolerance`` can be supplied as a :class:`NumericTolerance`; otherwise
    the two keyword tolerances are used.  Supplying both forms is rejected so
    a caller cannot accidentally use a different tolerance than intended.
    """

    if tolerance is not None and (absolute_tolerance != 0.0 or relative_tolerance != 0.0):
        raise ValueError("supply tolerance or absolute/relative tolerances, not both")
    expected_value = _finite_number(expected, "expected")
    actual_value = _finite_number(actual, "actual")
    selected = tolerance or NumericTolerance(absolute_tolerance, relative_tolerance)
    difference = abs(expected_value - actual_value)
    scale = max(abs(expected_value), abs(actual_value))
    relative_difference = 0.0 if difference == 0.0 else difference / scale if scale else math.inf
    allowed = selected.limit(expected_value, actual_value)
    return NumericComparison(
        expected=expected_value,
        actual=actual_value,
        absolute_difference=difference,
        relative_difference=relative_difference,
        allowed_difference=allowed,
        within_tolerance=difference <= allowed,
    )


def numbers_close(
    expected: Number,
    actual: Number,
    *,
    absolute_tolerance: float = 0.0,
    relative_tolerance: float = 0.0,
) -> bool:
    """Return only the boolean result of :func:`compare_numeric`."""

    return compare_numeric(
        expected,
        actual,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
    ).within_tolerance


# A descriptive alias for callers that prefer the wording used in reports.
compare_numbers = compare_numeric


@dataclass(frozen=True, slots=True)
class CSVCellDifference:
    """One changed cell in a :class:`CSVRowDifference`."""

    column: str
    baseline: str | None
    treatment: str | None
    numeric: bool = False
    absolute_difference: float | None = None
    relative_difference: float | None = None


@dataclass(frozen=True, slots=True)
class CSVRowDifference:
    """Differences for one aligned CSV row.

    ``row_number`` is the physical one-based row number in the baseline file
    when present, or in the treatment file for an added row.  ``key`` is set
    when keyed alignment was requested.
    """

    row_number: int
    cells: tuple[CSVCellDifference, ...] = ()
    status: str = "changed"
    key: tuple[str, ...] | None = None
    baseline_row_number: int | None = None
    treatment_row_number: int | None = None

    @property
    def changed_columns(self) -> tuple[str, ...]:
        return tuple(cell.column for cell in self.cells)

    @property
    def max_absolute_difference(self) -> float | None:
        values = [cell.absolute_difference for cell in self.cells if cell.absolute_difference is not None]
        return max(values) if values else None

    @property
    def mean_absolute_difference(self) -> float | None:
        values = [cell.absolute_difference for cell in self.cells if cell.absolute_difference is not None]
        return sum(values) / len(values) if values else None


@dataclass(frozen=True, slots=True)
class CSVComparison:
    """Row-level result of comparing two CSV documents."""

    changed: bool
    columns: tuple[str, ...]
    rows_compared: int
    row_differences: tuple[CSVRowDifference, ...] = ()
    header_changed: bool = False
    numeric_cells_compared: int = 0
    max_absolute_difference: float | None = None
    mean_absolute_difference: float | None = None

    @property
    def changed_rows(self) -> tuple[CSVRowDifference, ...]:
        """Alias that reads naturally in report code."""

        return self.row_differences


@dataclass(frozen=True, slots=True)
class _ParsedCSV:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    physical_rows: tuple[int, ...]


def _read_source(source: CSVSource | XMLSource) -> str | bytes:
    if isinstance(source, os.PathLike):
        return Path(source).read_bytes()
    if isinstance(source, bytes):
        return source
    if isinstance(source, str):
        return source
    value = source.read()
    if not isinstance(value, (str, bytes)):
        raise TypeError("source.read() must return text or bytes")
    return value


def _csv_text(source: CSVSource) -> str:
    value = _read_source(source)
    if isinstance(value, bytes):
        return value.decode("utf-8-sig")
    return value


def _parse_csv(source: CSVSource, *, has_header: bool) -> _ParsedCSV:
    reader = csv.reader(io.StringIO(_csv_text(source), newline=""))
    rows = list(reader)
    if has_header and rows:
        header = tuple(rows[0])
        data = rows[1:]
        physical_rows = tuple(range(2, len(rows) + 1))
    else:
        data = rows
        physical_rows = tuple(range(1, len(rows) + 1))
        width = max((len(row) for row in data), default=0)
        header = tuple(f"column_{index + 1}" for index in range(width))
    width = max(len(header), *(len(row) for row in data)) if data else len(header)
    if len(header) < width:
        header = header + tuple(f"column_{index + 1}" for index in range(len(header), width))
    return _ParsedCSV(tuple(header), tuple(tuple(row) for row in data), physical_rows)


def _column_indices(columns: Sequence[str], key_columns: Sequence[str | int]) -> tuple[int, ...]:
    indices: list[int] = []
    for key in key_columns:
        if isinstance(key, bool):
            raise TypeError("CSV key column indexes must be integers or names")
        if isinstance(key, int):
            index = key
            if index < 0 or index >= len(columns):
                raise ValueError(f"CSV key column index out of range: {key}")
        else:
            try:
                index = columns.index(key)
            except ValueError as error:
                raise ValueError(f"CSV key column not found: {key!r}") from error
        indices.append(index)
    if not indices:
        raise ValueError("key_columns must contain at least one column")
    return tuple(indices)


def _cell(row: tuple[str, ...] | None, index: int) -> str | None:
    if row is None or index >= len(row):
        return None
    return row[index]


def _numeric_cell(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value.strip())
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _row_pairs(
    baseline: _ParsedCSV,
    treatment: _ParsedCSV,
    key_columns: Sequence[str | int] | None,
) -> tuple[tuple[tuple[str, ...] | None, int | None, tuple[str, ...] | None, int | None, tuple[str, ...] | None], ...]:
    """Return aligned rows and physical line numbers, preserving input order."""

    if key_columns is None:
        count = max(len(baseline.rows), len(treatment.rows))
        return tuple(
            (
                baseline.rows[index] if index < len(baseline.rows) else None,
                baseline.physical_rows[index] if index < len(baseline.rows) else None,
                treatment.rows[index] if index < len(treatment.rows) else None,
                treatment.physical_rows[index] if index < len(treatment.rows) else None,
                None,
            )
            for index in range(count)
        )

    # Use the union of columns by position for key lookup.  A key name must
    # exist on both files, which avoids silently matching malformed schemas.
    columns = baseline.columns
    indices = _column_indices(columns, key_columns)
    treatment_indices = _column_indices(treatment.columns, key_columns)

    def key_for(row: tuple[str, ...], selected: tuple[int, ...]) -> tuple[str, ...]:
        return tuple(_cell(row, index) or "" for index in selected)

    baseline_by_key: dict[tuple[str, ...], tuple[tuple[str, ...], int]] = {}
    for row, line in zip(baseline.rows, baseline.physical_rows):
        key = key_for(row, indices)
        if key in baseline_by_key:
            raise ValueError(f"duplicate baseline CSV key: {key!r}")
        baseline_by_key[key] = (row, line)
    treatment_by_key: dict[tuple[str, ...], tuple[tuple[str, ...], int]] = {}
    for row, line in zip(treatment.rows, treatment.physical_rows):
        key = key_for(row, treatment_indices)
        if key in treatment_by_key:
            raise ValueError(f"duplicate treatment CSV key: {key!r}")
        treatment_by_key[key] = (row, line)

    pairs: list[tuple[tuple[str, ...] | None, int | None, tuple[str, ...] | None, int | None, tuple[str, ...] | None]] = []
    seen: set[tuple[str, ...]] = set()
    for key, (row, line) in baseline_by_key.items():
        treatment_value = treatment_by_key.get(key)
        pairs.append(
            (
                row,
                line,
                treatment_value[0] if treatment_value else None,
                treatment_value[1] if treatment_value else None,
                key,
            )
        )
        seen.add(key)
    for key, (row, line) in treatment_by_key.items():
        if key not in seen:
            pairs.append((None, None, row, line, key))
    return tuple(pairs)


def compare_csv(
    baseline: CSVSource,
    treatment: CSVSource,
    *,
    tolerance: NumericTolerance | None = None,
    absolute_tolerance: float = 0.0,
    relative_tolerance: float = 0.0,
    has_header: bool = True,
    key_columns: Sequence[str | int] | None = None,
) -> CSVComparison:
    """Compare CSV rows and report changed cells and numeric deltas.

    Rows are paired by position by default.  ``key_columns`` can contain
    header names or zero-based indexes to pair rows independently of order.
    Numeric-looking cells are compared with the supplied tolerance; other
    cells use exact text equality.  Maximum and mean absolute differences are
    calculated across every aligned numeric cell, including cells that fall
    within tolerance.
    """

    if tolerance is not None and (absolute_tolerance != 0.0 or relative_tolerance != 0.0):
        raise ValueError("supply tolerance or absolute/relative tolerances, not both")
    selected = tolerance or NumericTolerance(absolute_tolerance, relative_tolerance)
    baseline_data = _parse_csv(baseline, has_header=has_header)
    treatment_data = _parse_csv(treatment, has_header=has_header)
    width = max(len(baseline_data.columns), len(treatment_data.columns))
    columns = tuple(
        baseline_data.columns[index]
        if index < len(baseline_data.columns)
        else treatment_data.columns[index]
        for index in range(width)
    )
    header_changed = baseline_data.columns != treatment_data.columns
    differences: list[CSVRowDifference] = []
    numeric_differences: list[float] = []

    for baseline_row, baseline_line, treatment_row, treatment_line, key in _row_pairs(
        baseline_data, treatment_data, key_columns
    ):
        cells: list[CSVCellDifference] = []
        for index, column in enumerate(columns):
            left = _cell(baseline_row, index)
            right = _cell(treatment_row, index)
            left_number = _numeric_cell(left)
            right_number = _numeric_cell(right)
            if left_number is not None and right_number is not None:
                result = compare_numeric(left_number, right_number, tolerance=selected)
                numeric_differences.append(result.absolute_difference)
                if not result.within_tolerance:
                    cells.append(
                        CSVCellDifference(
                            column=column,
                            baseline=left,
                            treatment=right,
                            numeric=True,
                            absolute_difference=result.absolute_difference,
                            relative_difference=result.relative_difference,
                        )
                    )
            elif left != right:
                cells.append(CSVCellDifference(column=column, baseline=left, treatment=right))
        if cells or baseline_row is None or treatment_row is None:
            line = baseline_line if baseline_line is not None else treatment_line
            assert line is not None
            status = "removed" if treatment_row is None else "added" if baseline_row is None else "changed"
            differences.append(
                CSVRowDifference(
                    row_number=line,
                    cells=tuple(cells),
                    status=status,
                    key=key,
                    baseline_row_number=baseline_line,
                    treatment_row_number=treatment_line,
                )
            )

    return CSVComparison(
        changed=header_changed or bool(differences),
        columns=columns,
        rows_compared=max(len(baseline_data.rows), len(treatment_data.rows)),
        row_differences=tuple(differences),
        header_changed=header_changed,
        numeric_cells_compared=len(numeric_differences),
        max_absolute_difference=max(numeric_differences) if numeric_differences else None,
        mean_absolute_difference=(sum(numeric_differences) / len(numeric_differences)) if numeric_differences else None,
    )


def compare_csv_text(
    baseline: str,
    treatment: str,
    **kwargs: Any,
) -> CSVComparison:
    """Text-oriented alias for :func:`compare_csv`."""

    return compare_csv(baseline, treatment, **kwargs)


def compare_csv_files(
    baseline: os.PathLike[str] | str,
    treatment: os.PathLike[str] | str,
    **kwargs: Any,
) -> CSVComparison:
    """Path-oriented CSV comparison helper."""

    return compare_csv(Path(baseline), Path(treatment), **kwargs)


@dataclass(frozen=True, slots=True)
class JUnitCase:
    """A normalized JUnit ``testcase`` element."""

    classname: str
    name: str
    outcome: str
    time_seconds: float | None = None
    suite: str | None = None
    message: str | None = None
    details: str = ""
    stdout: str = ""
    stderr: str = ""

    @property
    def passed(self) -> bool:
        return self.outcome == "passed"


@dataclass(frozen=True, slots=True)
class JUnitReport:
    """Normalized JUnit results with counts derived from parsed testcases."""

    cases: tuple[JUnitCase, ...] = ()
    time_seconds: float | None = None
    suites: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed(self) -> int:
        return sum(case.outcome == "passed" for case in self.cases)

    @property
    def failures(self) -> int:
        return sum(case.outcome == "failure" for case in self.cases)

    @property
    def errors(self) -> int:
        return sum(case.outcome == "error" for case in self.cases)

    @property
    def skipped(self) -> int:
        return sum(case.outcome == "skipped" for case in self.cases)

    @property
    def failed(self) -> int:
        """Number of failed or errored cases."""

        return self.failures + self.errors


# Common spelling used by callers that want the type to say "test case".
JUnitTestCase = JUnitCase


def _xml_text(element: ElementTree.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def _xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_time(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _xml_root(source: XMLSource) -> ElementTree.Element:
    value = _read_source(source)
    if isinstance(value, bytes):
        return ElementTree.fromstring(value)
    if value.lstrip().startswith("<"):
        return ElementTree.fromstring(value)
    return ElementTree.parse(value).getroot()


def parse_junit_xml(source: XMLSource) -> JUnitReport:
    """Parse common JUnit XML variants with only ``xml.etree``.

    Both a single ``testsuite`` root and a ``testsuites`` root containing
    nested suites are accepted.  Counts are derived from testcase elements so
    a stale producer summary cannot misreport the parsed result.
    """

    root = _xml_root(source)
    cases: list[JUnitCase] = []
    suites: list[str] = []

    def visit_suite(suite_element: ElementTree.Element, inherited_name: str | None = None) -> None:
        suite_name = suite_element.attrib.get("name") or inherited_name
        if suite_name is not None and suite_name not in suites:
            suites.append(suite_name)
        for child in list(suite_element):
            child_name = _xml_name(child.tag)
            if child_name == "testcase":
                failure = next((item for item in list(child) if _xml_name(item.tag) == "failure"), None)
                error = next((item for item in list(child) if _xml_name(item.tag) == "error"), None)
                skipped = next((item for item in list(child) if _xml_name(item.tag) == "skipped"), None)
                disabled = child.attrib.get("status", "").lower() == "disabled" or child.attrib.get("disabled") in {
                    "1",
                    "true",
                    "yes",
                }
                if error is not None:
                    outcome_element = error
                    outcome = "error"
                elif failure is not None:
                    outcome_element = failure
                    outcome = "failure"
                elif skipped is not None or disabled:
                    outcome_element = skipped
                    outcome = "skipped"
                else:
                    outcome_element = None
                    outcome = "passed"
                system_out = next((item for item in list(child) if _xml_name(item.tag) == "system-out"), None)
                system_err = next((item for item in list(child) if _xml_name(item.tag) == "system-err"), None)
                message = outcome_element.attrib.get("message") if outcome_element is not None else None
                cases.append(
                    JUnitCase(
                        classname=child.attrib.get("classname", ""),
                        name=child.attrib.get("name", ""),
                        outcome=outcome,
                        time_seconds=_xml_time(child.attrib.get("time")),
                        suite=suite_name,
                        message=message,
                        details=_xml_text(outcome_element),
                        stdout=_xml_text(system_out),
                        stderr=_xml_text(system_err),
                    )
                )
            elif child_name == "testsuite":
                visit_suite(child, suite_name)

    if _xml_name(root.tag) == "testsuite":
        visit_suite(root)
        total_time = _xml_time(root.attrib.get("time"))
    else:
        for child in list(root):
            if _xml_name(child.tag) == "testsuite":
                visit_suite(child)
        total_time = _xml_time(root.attrib.get("time"))

    return JUnitReport(cases=tuple(cases), time_seconds=total_time, suites=tuple(suites))


parse_junit = parse_junit_xml


@dataclass(frozen=True, slots=True)
class HTMLReportSection:
    """A dependency-free section for :func:`render_html_report`."""

    heading: str
    body: str


@dataclass(frozen=True, slots=True)
class HTMLReport:
    """Self-contained HTML report data model for P1 evaluation output."""

    title: str = "CausaTrace P1 evaluation"
    sections: tuple[HTMLReportSection, ...] = field(default_factory=tuple)

    def render(self) -> str:
        sections = "\n".join(
            f"    <section><h2>{escape(section.heading)}</h2><pre>{escape(section.body)}</pre></section>"
            for section in self.sections
        )
        return (
            "<!doctype html>\n"
            "<html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{escape(self.title)}</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:72rem;margin:2rem auto;padding:0 1rem}"
            "pre{background:#f4f4f4;padding:1rem;overflow:auto;white-space:pre-wrap}"
            "section{margin:1.5rem 0}</style></head><body>\n"
            f"  <h1>{escape(self.title)}</h1>\n{sections}\n"
            "</body></html>\n"
        )


def render_html_report(
    report: HTMLReport | Iterable[HTMLReportSection],
    *,
    title: str = "CausaTrace P1 evaluation",
) -> str:
    """Render an isolated HTML report and escape all caller-provided text."""

    if isinstance(report, HTMLReport):
        return report.render()
    return HTMLReport(title=title, sections=tuple(report)).render()


__all__ = [
    "CSVCellDifference",
    "CSVComparison",
    "CSVRowDifference",
    "HTMLReport",
    "HTMLReportSection",
    "JUnitCase",
    "JUnitReport",
    "JUnitTestCase",
    "NumericComparison",
    "NumericTolerance",
    "compare_csv",
    "compare_csv_files",
    "compare_csv_text",
    "compare_numeric",
    "compare_numbers",
    "numbers_close",
    "parse_junit",
    "parse_junit_xml",
    "render_html_report",
]
