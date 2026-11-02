"""Load and validate YAML suite files."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import redteam
from .core import TestCase


class SuiteError(ValueError):
    pass


@dataclass
class Suite:
    name: str
    target: dict[str, Any]
    cases: list[TestCase]
    judge: dict[str, Any] | None = None
    threshold: float = 1.0
    repeat: int = 1
    concurrency: int = 4
    secrets: list[str] = field(default_factory=list)
    path: Path | None = None


def load_suite(path: str | Path) -> Suite:
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SuiteError(f"{path}: invalid YAML: {exc}") from exc
    return parse_suite(raw, base_dir=path.parent, path=path)


def parse_suite(raw: dict[str, Any], base_dir: Path = Path("."), path: Path | None = None) -> Suite:
    if "target" not in raw:
        raise SuiteError("suite needs a 'target' section")
    defaults = (raw.get("defaults") or {}).get("assert", [])

    case_dicts = list(raw.get("tests") or [])
    if raw.get("tests_file"):
        case_dicts += _load_cases_file(base_dir / raw["tests_file"])

    cases = [_parse_case(c, i, defaults) for i, c in enumerate(case_dicts, 1)]
    attack_cases, secrets = redteam.expand(raw.get("redteam"))
    cases += attack_cases

    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise SuiteError(f"duplicate test id {c.id!r}")
        seen.add(c.id)
    if not cases:
        raise SuiteError("suite has no tests")

    return Suite(
        name=raw.get("name", path.stem if path else "suite"),
        target=raw["target"],
        cases=cases,
        judge=raw.get("judge"),
        threshold=float(raw.get("threshold", 1.0)),
        repeat=int(raw.get("repeat", 1)),
        concurrency=int(raw.get("concurrency", 4)),
        secrets=secrets,
        path=path,
    )


def _parse_case(c: dict[str, Any], index: int, defaults: list[dict[str, Any]]) -> TestCase:
    if "input" not in c:
        raise SuiteError(f"test #{index} is missing 'input'")
    asserts = list(c.get("assert") or [])
    if c.get("expected") and not asserts:
        asserts = [{"type": "similar", "value": c["expected"]}]
    for a in asserts:
        if "type" not in a:
            raise SuiteError(f"test #{index}: every assertion needs a 'type'")
    if not c.get("skip_defaults"):
        asserts = list(defaults) + asserts
    return TestCase(
        id=str(c.get("id", f"case-{index:03d}")),
        input=str(c["input"]),
        vars=dict(c.get("vars") or {}),
        context=c.get("context"),
        expected=c.get("expected"),
        asserts=asserts,
        tags=list(c.get("tags") or []),
    )


def _load_cases_file(path: Path) -> list[dict[str, Any]]:
    """JSONL or CSV datasets. CSV columns: id,input,expected,context,tags (``;``-separated)."""
    if not path.exists():
        raise SuiteError(f"tests_file not found: {path}")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if path.suffix == ".csv":
        rows = []
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("tags"):
                    row["tags"] = [t.strip() for t in row["tags"].split(";") if t.strip()]
                rows.append({k: v for k, v in row.items() if v not in (None, "")})
        return rows
    raise SuiteError(f"tests_file must be .jsonl or .csv, got {path.suffix}")
