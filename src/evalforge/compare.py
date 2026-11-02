"""Compare a run against a baseline to catch regressions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Diff:
    regressions: list[str] = field(default_factory=list)  # passed before, fails now
    fixes: list[str] = field(default_factory=list)  # failed before, passes now
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    score_drops: list[tuple[str, float, float]] = field(default_factory=list)
    pass_rate: tuple[float, float] = (0.0, 0.0)
    p95_ms: tuple[float, float] = (0.0, 0.0)

    @property
    def has_regressions(self) -> bool:
        return bool(self.regressions)


def compare(baseline: dict[str, Any], current: dict[str, Any], score_tolerance: float = 0.1) -> Diff:
    old = {c["id"]: c for c in baseline["cases"]}
    new = {c["id"]: c for c in current["cases"]}
    diff = Diff(
        pass_rate=(baseline["summary"]["pass_rate"], current["summary"]["pass_rate"]),
        p95_ms=(baseline["summary"]["latency_p95_ms"], current["summary"]["latency_p95_ms"]),
    )
    for cid, case in new.items():
        before = old.get(cid)
        if before is None:
            diff.added.append(cid)
            continue
        if before["passed"] and not case["passed"]:
            diff.regressions.append(cid)
        elif not before["passed"] and case["passed"]:
            diff.fixes.append(cid)
        elif before["score"] - case["score"] > score_tolerance:
            diff.score_drops.append((cid, before["score"], case["score"]))
    diff.removed = [cid for cid in old if cid not in new]
    return diff


def to_markdown(diff: Diff) -> str:
    before, after = diff.pass_rate
    arrow = "🔺" if after > before else ("🔻" if after < before else "➖")
    lines = [
        "### evalforge: comparison with baseline",
        "",
        "| metric | baseline | current |",
        "|---|---|---|",
        f"| pass rate | {before:.1%} | {after:.1%} {arrow} |",
        f"| p95 latency | {diff.p95_ms[0]:.0f} ms | {diff.p95_ms[1]:.0f} ms |",
        "",
    ]
    sections = [
        ("❌ Regressions", diff.regressions),
        ("✅ Fixed", diff.fixes),
        ("➕ New tests", diff.added),
        ("➖ Removed tests", diff.removed),
    ]
    for title, ids in sections:
        if ids:
            lines.append(f"**{title} ({len(ids)})**: " + ", ".join(f"`{i}`" for i in ids))
    if diff.score_drops:
        drops = ", ".join(f"`{i}` {a:.2f}→{b:.2f}" for i, a, b in diff.score_drops)
        lines.append(f"**📉 Score drops**: {drops}")
    if not any(ids for _, ids in sections) and not diff.score_drops:
        lines.append("No changes in test outcomes.")
    return "\n".join(lines) + "\n"
