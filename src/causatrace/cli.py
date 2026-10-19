from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .models import ArtifactSpec
from .orchestrator import AnalysisError, AnalysisOrchestrator
from .reporting import render_json, render_terminal


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
    analyze.add_argument("--format", choices=("terminal", "json"), default="terminal")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(argv)
    if parsed.command != "analyze":
        parser.print_help()
        return 2
    artifacts = tuple(ArtifactSpec(path=path) for path in parsed.artifact)
    try:
        report = AnalysisOrchestrator().analyze(
            parsed.target,
            project_root=parsed.project_root,
            artifacts=artifacts,
            metric_patterns=tuple(parsed.metric_regex),
            timeout_seconds=parsed.timeout,
        )
    except (AnalysisError, FileNotFoundError, NotADirectoryError, ValueError) as exc:
        print(f"causatrace: {exc}", file=sys.stderr)
        return 2
    output = render_json(report) if parsed.format == "json" else render_terminal(report)
    print(output, end="")
    return 0
