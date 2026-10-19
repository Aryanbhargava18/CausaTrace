"""Stable protocols shared by the implementation milestones."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import (
    AnalysisReport,
    ExecutionProfile,
    Location,
    OutputComparison,
    ProbeResult,
    RunRequest,
    RunResult,
    StaticFinding,
)


class Runner(Protocol):
    def run(self, request: RunRequest) -> RunResult: ...


class FactorProbe(Protocol):
    @property
    def name(self) -> str: ...

    def profiles(self, baseline: ExecutionProfile) -> tuple[ExecutionProfile, ...]: ...


class Comparator(Protocol):
    def compare(self, baseline: RunResult, treatment: RunResult) -> OutputComparison: ...


class Locator(Protocol):
    def find_static(self, root: Path) -> tuple[StaticFinding, ...]: ...

    def find_runtime(self, events: tuple[dict, ...]) -> tuple[Location, ...]: ...


class Reporter(Protocol):
    def render(self, report: AnalysisReport) -> str: ...
