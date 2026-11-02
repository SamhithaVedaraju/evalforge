"""Core data types shared across evalforge."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TestCase:
    __test__ = False  # keep pytest from collecting this class

    id: str
    input: str
    vars: dict[str, Any] = field(default_factory=dict)
    context: str | None = None
    expected: str | None = None
    asserts: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class Output:
    """What a target returned for one test case."""

    text: str
    latency_ms: float
    context: str | None = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class AssertionResult:
    type: str
    passed: bool
    score: float
    reason: str = ""
    skipped: bool = False


@dataclass
class CaseResult:
    id: str
    input: str
    output: str
    latency_ms: float
    assertions: list[AssertionResult]
    tags: list[str] = field(default_factory=list)
    error: str | None = None
    attempt_pass_rate: float = 1.0

    @property
    def passed(self) -> bool:
        if self.error:
            return False
        return all(a.passed for a in self.assertions if not a.skipped)

    @property
    def score(self) -> float:
        scored = [a.score for a in self.assertions if not a.skipped]
        return sum(scored) / len(scored) if scored else (0.0 if self.error else 1.0)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        d["score"] = round(self.score, 4)
        return d


@dataclass
class SuiteResult:
    name: str
    target: str
    cases: list[CaseResult]
    started_at: str
    duration_s: float
    threshold: float = 1.0

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total if self.total else 0.0

    @property
    def ok(self) -> bool:
        return self.pass_rate >= self.threshold

    def to_dict(self) -> dict[str, Any]:
        latencies = sorted(c.latency_ms for c in self.cases)
        return {
            "name": self.name,
            "target": self.target,
            "started_at": self.started_at,
            "duration_s": round(self.duration_s, 3),
            "threshold": self.threshold,
            "summary": {
                "total": self.total,
                "passed": self.passed_count,
                "failed": self.total - self.passed_count,
                "pass_rate": round(self.pass_rate, 4),
                "ok": self.ok,
                "latency_p50_ms": round(_percentile(latencies, 0.5), 1),
                "latency_p95_ms": round(_percentile(latencies, 0.95), 1),
            },
            "cases": [c.to_dict() for c in self.cases],
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]
