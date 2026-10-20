from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from . import __version__
from .benchmark import render_benchmark_terminal, run_fixture_benchmark
from .fixes import preview_fixes
from .models import ArtifactSpec
from .orchestrator import AnalysisError, AnalysisOrchestrator
from .p1_compare import compare_csv_files, parse_junit_xml
from .pytest_support import run_pytest
from .reporting import render_html, render_json, render_terminal
from .verification import clean_machine_profile, verify_five_runs
from .models import RunRequest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="causatrace",
        description="Find tested factors that change a Python program's output.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command")
    analyze = commands.add_parser("analyze", help="analyze one Python script")
    analyze.add_argument("target", type=Path)
    analyze.add_argument("--project-root", type=Path)
    analyze.add_argument("--format", choices=("terminal", "json", "html"), default="terminal")
    analyze.add_argument("--output", type=Path, help="write the selected report to a file")
    analyze.add_argument("--timeout", type=float, default=30.0)
    analyze.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="PATH",
        help="artifact path inside the project to hash after each run",
    )
    analyze.add_argument(
        "--metric-regex",
        action="append",
        default=[],
        metavar="REGEX",
        help="regular expression used to extract numeric stdout/stderr metrics",
    )
    fix = commands.add_parser("fix", help="preview safe reproducibility repairs")
    fix.add_argument("target", type=Path)
    fix.add_argument("--project-root", type=Path)
    fix.add_argument("--write", action="store_true", help="apply the displayed diff")
    fix.add_argument("--verify", action="store_true", help="deprecated; --write verifies automatically")
    fix.add_argument("--clean-machine", action="store_true", help="verify with the clean-machine profile")

    verify = commands.add_parser("verify", help="verify five identical output hashes")
    verify.add_argument("target", type=Path)
    verify.add_argument("--project-root", type=Path)
    verify.add_argument("--clean-machine", action="store_true")
    verify.add_argument("--format", choices=("terminal", "json"), default="terminal")

    benchmark = commands.add_parser("benchmark", help="run the planted fixture benchmark")
    benchmark.add_argument("--project-root", type=Path, default=Path("."))
    benchmark.add_argument("--format", choices=("terminal", "json"), default="terminal")

    junit = commands.add_parser("junit", help="parse an existing JUnit XML result")
    junit.add_argument("path", type=Path)
    junit.add_argument("--format", choices=("terminal", "json"), default="terminal")

    csv_compare = commands.add_parser("compare-csv", help="compare two CSV artifacts with P1 tolerances")
    csv_compare.add_argument("baseline", type=Path)
    csv_compare.add_argument("treatment", type=Path)
    csv_compare.add_argument("--key", action="append", default=[], help="header column used to align rows")
    csv_compare.add_argument("--absolute-tolerance", type=float, default=0.0)
    csv_compare.add_argument("--relative-tolerance", type=float, default=0.0)
    csv_compare.add_argument("--format", choices=("terminal", "json"), default="terminal")

    pytest_command = commands.add_parser("pytest", help="run pytest and parse JUnit XML")
    pytest_command.add_argument("--project-root", type=Path, default=Path("."))
    pytest_command.add_argument("--timeout", type=float, default=120.0)
    pytest_command.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)
    target_arguments: tuple[str, ...] = ()
    if "--" in raw_args and "analyze" in raw_args:
        separator = raw_args.index("--")
        target_arguments = tuple(raw_args[separator + 1 :])
        raw_args = raw_args[:separator]
    parsed = parser.parse_args(raw_args)
    if parsed.command is None:
        parser.print_help()
        return 2
    try:
        if parsed.command == "analyze":
            artifacts = tuple(ArtifactSpec(path=path) for path in parsed.artifact)
            report = AnalysisOrchestrator().analyze(
                parsed.target,
                project_root=parsed.project_root,
                arguments=target_arguments,
                artifacts=artifacts,
                metric_patterns=tuple(parsed.metric_regex),
                timeout_seconds=parsed.timeout,
            )
            if parsed.format == "json":
                output = render_json(report)
            elif parsed.format == "html":
                output = render_html(report)
            else:
                output = render_terminal(report)
            if parsed.output:
                parsed.output.write_text(output, encoding="utf-8")
            else:
                print(output, end="")
            return 1 if report.status in {"baseline_unstable", "inconclusive"} else 0

        if parsed.command == "fix":
            source = parsed.target.read_text(encoding="utf-8")
            preview = preview_fixes(source, filename=str(parsed.target))
            if preview.warnings:
                for warning in preview.warnings:
                    print(f"Warning: {warning}", file=sys.stderr)
            if preview.unified_diff:
                print(preview.unified_diff, end="")
            else:
                print("No safe P1 repair was identified.")
            if not parsed.write:
                print("Preview only; no files were modified.", file=sys.stderr)
                return 0
            original_source = source
            parsed.target.write_text(preview.transformed_source, encoding="utf-8")
            print(f"Wrote {parsed.target}.")
            if parsed.write or parsed.verify:
                target = parsed.target.resolve()
                root = parsed.project_root.resolve() if parsed.project_root else target.parent
                try:
                    relative_target = target.relative_to(root)
                except ValueError as exc:
                    parsed.target.write_text(original_source, encoding="utf-8")
                    raise ValueError("target must be inside project-root") from exc
                request = RunRequest(target=str(relative_target), project_root=str(root))
                result = verify_five_runs(
                    request,
                    profile=clean_machine_profile() if parsed.clean_machine else None,
                )
                print(
                    f"Verification: {'PASS' if result.verified else 'FAIL'} "
                    f"({len(set(result.output_hashes))} distinct output hashes across five runs)"
                )
                for warning in result.warnings:
                    print(f"Warning: {warning}", file=sys.stderr)
                if not result.verified:
                    parsed.target.write_text(original_source, encoding="utf-8")
                    print("Verification failed; restored the original source.", file=sys.stderr)
                    return 1
            return 0

        if parsed.command == "verify":
            root = parsed.project_root.resolve() if parsed.project_root else parsed.target.resolve().parent
            target = parsed.target.resolve()
            request = RunRequest(target=str(target.relative_to(root)), project_root=str(root))
            result = verify_five_runs(
                request,
                profile=clean_machine_profile() if parsed.clean_machine else None,
            )
            if parsed.format == "json":
                print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))
            else:
                print(
                    f"Verification: {'PASS' if result.verified else 'FAIL'}\n"
                    f"Profile: {result.profile.name}\n"
                    f"Output hashes: {len(set(result.output_hashes))} distinct across five runs"
                )
                for warning in result.warnings:
                    print(f"Warning: {warning}")
            return 0 if result.verified else 1

        if parsed.command == "benchmark":
            rows = run_fixture_benchmark(parsed.project_root)
            if parsed.format == "json":
                print(json.dumps([asdict(row) for row in rows], indent=2, sort_keys=True))
            else:
                print(render_benchmark_terminal(rows), end="")
            return 0 if all(row.correct for row in rows) else 1

        if parsed.command == "junit":
            result = parse_junit_xml(parsed.path)
            payload = {
                "total": result.total,
                "passed": result.passed,
                "failures": result.failures,
                "errors": result.errors,
                "skipped": result.skipped,
                "time_seconds": result.time_seconds,
                "suites": result.suites,
            }
            if parsed.format == "json":
                print(json.dumps(payload, indent=2, sort_keys=True))
            else:
                print(
                    f"JUnit: {result.passed}/{result.total} passed, "
                    f"{result.failures} failures, {result.errors} errors, {result.skipped} skipped"
                )
            return 0 if result.failed == 0 else 1

        if parsed.command == "compare-csv":
            result = compare_csv_files(
                parsed.baseline,
                parsed.treatment,
                key_columns=tuple(parsed.key) or None,
                absolute_tolerance=parsed.absolute_tolerance,
                relative_tolerance=parsed.relative_tolerance,
            )
            if parsed.format == "json":
                print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))
            else:
                print(
                    f"CSV: {'CHANGED' if result.changed else 'UNCHANGED'}; "
                    f"rows={result.rows_compared}; changed_rows={len(result.changed_rows)}; "
                    f"max_abs_delta={result.max_absolute_difference}"
                )
                for row in result.changed_rows:
                    print(f"- row {row.row_number} ({row.status}): {', '.join(row.changed_columns)}")
            return 1 if result.changed else 0

        if parsed.command == "pytest":
            result = run_pytest(
                parsed.project_root,
                arguments=tuple(parsed.pytest_args),
                timeout_seconds=parsed.timeout,
            )
            print(
                f"pytest: {result.junit.passed}/{result.junit.total} passed, "
                f"{result.junit.failed} failed, exit_code={result.exit_code}"
            )
            return result.exit_code
    except (AnalysisError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"causatrace: {exc}", file=sys.stderr)
        return 2
    return 2
