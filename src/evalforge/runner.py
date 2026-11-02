"""Run a suite against its target."""

from __future__ import annotations

import fnmatch
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from . import assertions
from .config import Suite
from .core import CaseResult, SuiteResult, TestCase
from .judge import Judge
from .providers import Target, make_target


def select_cases(cases: list[TestCase], tags: list[str] | None = None, pattern: str | None = None) -> list[TestCase]:
    if tags:
        cases = [c for c in cases if set(tags) & set(c.tags)]
    if pattern:
        cases = [c for c in cases if fnmatch.fnmatch(c.id, pattern)]
    return cases


def run_case(case: TestCase, target: Target, judge: Judge, secrets: list[str], repeat: int = 1) -> CaseResult:
    """Run one case ``repeat`` times. It passes only if every attempt passes,
    so nondeterministic failures surface instead of hiding behind a lucky run."""
    attempts: list[CaseResult] = []
    for _ in range(max(1, repeat)):
        output = target.complete(case)
        ctx = assertions.Context(case=case, output=output, judge=judge, secrets=secrets)
        results = [] if output.error else [assertions.evaluate(spec, ctx) for spec in case.asserts]
        attempts.append(
            CaseResult(
                id=case.id,
                input=case.input,
                output=output.text,
                latency_ms=output.latency_ms,
                assertions=results,
                tags=case.tags,
                error=output.error,
            )
        )
    passed = sum(a.passed for a in attempts)
    shown = next((a for a in attempts if not a.passed), attempts[-1])
    shown.attempt_pass_rate = passed / len(attempts)
    shown.latency_ms = sum(a.latency_ms for a in attempts) / len(attempts)
    return shown


def run_suite(
    suite: Suite,
    tags: list[str] | None = None,
    pattern: str | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
    target: Target | None = None,
) -> SuiteResult:
    target = target or make_target(suite.target)
    judge = Judge(suite.judge)
    cases = select_cases(suite.cases, tags, pattern)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    t0 = time.perf_counter()

    def work(case: TestCase) -> CaseResult:
        result = run_case(case, target, judge, suite.secrets, suite.repeat)
        if on_result:
            on_result(result)
        return result

    with ThreadPoolExecutor(max_workers=max(1, suite.concurrency)) as pool:
        results = list(pool.map(work, cases))  # map keeps suite order

    return SuiteResult(
        name=suite.name,
        target=target.describe(),
        cases=results,
        started_at=started,
        duration_s=time.perf_counter() - t0,
        threshold=suite.threshold,
    )
