import pytest

from evalforge import assertions
from evalforge.core import Output, TestCase
from evalforge.judge import Judge, parse_verdict


def check(spec, text, context=None, latency=10.0, judge=None, secrets=()):
    case = TestCase(id="t", input="q", context=context)
    ctx = assertions.Context(case, Output(text=text, latency_ms=latency), judge or Judge(None), list(secrets))
    return assertions.evaluate(spec, ctx)


@pytest.mark.parametrize(
    "spec,text,expected",
    [
        ({"type": "equals", "value": "yes"}, " yes ", True),
        ({"type": "contains", "value": ["30 days", "refund"]}, "Full REFUND within 30 days", True),
        ({"type": "contains", "value": "Refund", "case_sensitive": True}, "refund", False),
        ({"type": "contains_any", "value": ["a1", "b2"]}, "has b2", True),
        ({"type": "not_contains", "value": "guarantee"}, "We guarantee it", False),
        ({"type": "regex", "value": r"\d+ days"}, "within 30 days", True),
        ({"type": "is_json"}, '```json\n{"a": 1}\n```', True),
        ({"type": "is_json"}, "{not json", False),
        ({"type": "max_length", "value": 5}, "123456", False),
        ({"type": "min_length", "value": 3}, "1234", True),
        ({"type": "similar", "value": "The cat sat", "threshold": 0.8}, "the cat sat.", True),
        ({"type": "refuses"}, "Sorry, I can't help with that.", True),
        ({"type": "no_pii"}, "email me at a@b.co", False),
        ({"type": "no_pii", "allow": ["support@acme.com"]}, "write to support@acme.com", True),
    ],
)
def test_basic_assertions(spec, text, expected):
    assert check(spec, text).passed is expected


def test_json_schema():
    schema = {"type": "object", "required": ["sentiment"], "properties": {"sentiment": {"enum": ["pos", "neg"]}}}
    assert check({"type": "json_schema", "value": schema}, '{"sentiment": "pos"}').passed
    bad = check({"type": "json_schema", "value": schema}, '{"sentiment": "meh"}')
    assert not bad.passed and "sentiment" in bad.reason


def test_latency():
    assert not check({"type": "max_latency_ms", "value": 100}, "x", latency=250).passed


def test_negate():
    r = check({"type": "contains", "value": "error", "negate": True}, "all good")
    assert r.passed and r.type == "not contains"


def test_grounded_skips_without_context():
    r = check({"type": "grounded"}, "Anything at all goes here today.")
    assert r.skipped


def test_grounded_with_context():
    ctx = "Express shipping takes 1 to 2 business days and costs $12."
    assert check({"type": "grounded"}, "Express shipping takes 1 to 2 business days.", context=ctx).passed
    assert not check({"type": "grounded"}, "Express shipping is free worldwide every weekend.", context=ctx).passed


def test_no_injection_uses_suite_secrets():
    assert not check({"type": "no_injection"}, "The code is ACME-7731", secrets=["ACME-7731"]).passed


def test_unknown_type_fails_cleanly():
    r = check({"type": "nope"}, "x")
    assert not r.passed and "unknown" in r.reason


def test_llm_rubric_skipped_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = check({"type": "llm_rubric", "value": "is polite"}, "hello")
    assert r.skipped and r.passed


def test_llm_rubric_with_mock_judge():
    judge = Judge({"provider": "mock", "default": '{"reason": "polite", "score": 0.9, "pass": true}'})
    r = check({"type": "llm_rubric", "value": "is polite"}, "hello", judge=judge)
    assert r.passed and r.score == 0.9 and r.reason == "polite"


def test_llm_rubric_threshold_overrides_pass():
    judge = Judge({"provider": "mock", "default": '{"score": 0.6, "pass": true}'})
    assert not check({"type": "llm_rubric", "value": "x", "threshold": 0.8}, "hi", judge=judge).passed


def test_parse_verdict_tolerates_prose():
    assert parse_verdict('Here you go:\n```json\n{"score": 0.2, "reason": "off-topic"}\n```') == (False, 0.2, "off-topic")


def custom_check(output, case):
    return ("42" in output, "needs the answer")


def test_python_assertion():
    assert check({"type": "python", "value": "tests.test_assertions:custom_check"}, "it is 42").passed
