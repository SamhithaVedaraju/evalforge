"""Assertion registry.

Each assertion takes (spec, ctx) and returns an AssertionResult. ``spec`` is the
YAML mapping (``type``, ``value``, plus options); any assertion can be flipped
with ``negate: true``.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema

from . import guardrails
from .core import AssertionResult, Output, TestCase
from .judge import Judge, JudgeUnavailable


@dataclass
class Context:
    case: TestCase
    output: Output
    judge: Judge
    secrets: list[str]


AssertFn = Callable[[dict[str, Any], Context], AssertionResult]
REGISTRY: dict[str, AssertFn] = {}


def register(name: str):
    def deco(fn: AssertFn) -> AssertFn:
        REGISTRY[name] = fn
        return fn

    return deco


def _result(spec: dict[str, Any], passed: bool, reason: str, score: float | None = None) -> AssertionResult:
    return AssertionResult(spec["type"], passed, score if score is not None else float(passed), reason)


def _values(spec: dict[str, Any]) -> list[str]:
    v = spec.get("value")
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


@register("equals")
def _equals(spec, ctx):
    ok = ctx.output.text.strip() == str(spec["value"]).strip()
    return _result(spec, ok, "" if ok else "output differs from expected value")


@register("contains")
def _contains(spec, ctx):
    hay = ctx.output.text if spec.get("case_sensitive") else ctx.output.text.lower()
    needles = _values(spec)
    missing = [n for n in needles if (n if spec.get("case_sensitive") else n.lower()) not in hay]
    return _result(spec, not missing, f"missing: {missing}" if missing else "")


@register("contains_any")
def _contains_any(spec, ctx):
    hay = ctx.output.text.lower()
    hit = [n for n in _values(spec) if n.lower() in hay]
    return _result(spec, bool(hit), f"found {hit[0]!r}" if hit else f"none of {_values(spec)} found")


@register("not_contains")
def _not_contains(spec, ctx):
    hay = ctx.output.text.lower()
    hit = [n for n in _values(spec) if n.lower() in hay]
    return _result(spec, not hit, f"found forbidden: {hit}" if hit else "")


@register("regex")
def _regex(spec, ctx):
    ok = re.search(str(spec["value"]), ctx.output.text, re.IGNORECASE | re.MULTILINE) is not None
    return _result(spec, ok, "" if ok else f"no match for /{spec['value']}/")


@register("is_json")
def _is_json(spec, ctx):
    try:
        json.loads(_strip_fences(ctx.output.text))
        return _result(spec, True, "")
    except json.JSONDecodeError as exc:
        return _result(spec, False, f"invalid JSON: {exc.msg}")


@register("json_schema")
def _json_schema(spec, ctx):
    try:
        data = json.loads(_strip_fences(ctx.output.text))
    except json.JSONDecodeError as exc:
        return _result(spec, False, f"invalid JSON: {exc.msg}")
    try:
        jsonschema.validate(data, spec["value"])
        return _result(spec, True, "")
    except jsonschema.ValidationError as exc:
        return _result(spec, False, f"schema violation at {list(exc.absolute_path)}: {exc.message}")


@register("max_length")
def _max_length(spec, ctx):
    n = len(ctx.output.text)
    return _result(spec, n <= int(spec["value"]), f"{n} chars (max {spec['value']})")


@register("min_length")
def _min_length(spec, ctx):
    n = len(ctx.output.text)
    return _result(spec, n >= int(spec["value"]), f"{n} chars (min {spec['value']})")


@register("max_latency_ms")
def _max_latency(spec, ctx):
    ms = ctx.output.latency_ms
    return _result(spec, ms <= float(spec["value"]), f"{ms:.0f} ms (max {spec['value']})")


@register("similar")
def _similar(spec, ctx):
    expected = str(spec.get("value", ctx.case.expected or ""))
    ratio = difflib.SequenceMatcher(None, ctx.output.text.lower(), expected.lower()).ratio()
    threshold = float(spec.get("threshold", 0.7))
    return _result(spec, ratio >= threshold, f"similarity {ratio:.2f} (min {threshold})", ratio)


@register("grounded")
def _grounded(spec, ctx):
    context = ctx.output.context or ctx.case.context
    if not context:
        return AssertionResult("grounded", True, 1.0, "no context to check against", skipped=True)
    score, unsupported = guardrails.groundedness(
        ctx.output.text, context, float(spec.get("min_overlap", 0.5))
    )
    threshold = float(spec.get("threshold", 0.8))
    reason = f"{score:.0%} of claims supported by context"
    if unsupported:
        reason += f"; unsupported: {unsupported[0]!r}" + (f" (+{len(unsupported) - 1} more)" if len(unsupported) > 1 else "")
    return _result(spec, score >= threshold, reason, score)


@register("no_pii")
def _no_pii(spec, ctx):
    found = guardrails.find_pii(ctx.output.text, spec.get("kinds"))
    allow = {a.lower() for a in spec.get("allow", [])}
    found = [(k, m) for k, m in found if m.lower() not in allow]
    return _result(spec, not found, f"found {found[:3]}" if found else "")


@register("no_injection")
def _no_injection(spec, ctx):
    secrets = list(spec.get("secrets", [])) + ctx.secrets
    hits = guardrails.injection_signals(ctx.output.text, secrets)
    return _result(spec, not hits, "; ".join(hits[:3]))


@register("refuses")
def _refuses(spec, ctx):
    ok = guardrails.is_refusal(ctx.output.text)
    return _result(spec, ok, "" if ok else "expected a refusal")


@register("llm_rubric")
def _llm_rubric(spec, ctx):
    try:
        passed, score, reason = ctx.judge.grade(
            str(spec["value"]), ctx.case, ctx.output.text, ctx.output.context or ctx.case.context
        )
    except JudgeUnavailable as exc:
        return AssertionResult("llm_rubric", True, 1.0, f"skipped: {exc}", skipped=True)
    threshold = spec.get("threshold")
    if threshold is not None:
        passed = score >= float(threshold)
    return _result(spec, passed, reason, score)


@register("python")
def _python(spec, ctx):
    from .providers import load_ref

    fn = load_ref(spec["value"])
    out = fn(ctx.output.text, ctx.case)
    if isinstance(out, AssertionResult):
        return out
    if isinstance(out, tuple):
        passed, reason = out
        return _result(spec, bool(passed), str(reason))
    if isinstance(out, (int, float)) and not isinstance(out, bool):
        threshold = float(spec.get("threshold", 0.5))
        return _result(spec, out >= threshold, f"score {out:.2f}", float(out))
    return _result(spec, bool(out), "")


def evaluate(spec: dict[str, Any], ctx: Context) -> AssertionResult:
    kind = spec.get("type")
    if kind not in REGISTRY:
        return AssertionResult(str(kind), False, 0.0, f"unknown assertion type {kind!r}")
    try:
        result = REGISTRY[kind](spec, ctx)
    except Exception as exc:
        return AssertionResult(kind, False, 0.0, f"assertion error: {type(exc).__name__}: {exc}")
    if spec.get("negate") and not result.skipped:
        result = AssertionResult(f"not {kind}", not result.passed, 1.0 - result.score, result.reason)
    return result


def _strip_fences(text: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return (m.group(1) if m else text).strip()
