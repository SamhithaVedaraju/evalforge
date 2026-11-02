import json
from pathlib import Path

import pytest

from evalforge import cli, compare, parse_suite, report, run_suite
from evalforge.config import SuiteError, load_suite
from evalforge.core import Output
from evalforge.providers import Target

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "support_bot" / "suite.yaml"

MOCK_SUITE = {
    "name": "mock",
    "target": {"type": "mock", "responses": {"capital": "Paris is the capital of France."}, "default": "No idea."},
    "defaults": {"assert": [{"type": "no_pii"}]},
    "tests": [
        {"id": "capital", "input": "What is the capital of France?", "assert": [{"type": "contains", "value": "Paris"}]},
        {"id": "unknown", "input": "Meaning of life?", "assert": [{"type": "contains", "value": "42"}], "tags": ["hard"]},
    ],
}


def test_parse_applies_defaults_and_ids():
    suite = parse_suite(MOCK_SUITE)
    assert [c.id for c in suite.cases] == ["capital", "unknown"]
    assert suite.cases[0].asserts[0] == {"type": "no_pii"}


def test_expected_becomes_similarity_check():
    suite = parse_suite({"target": {"type": "mock"}, "tests": [{"input": "x", "expected": "y"}]})
    assert suite.cases[0].asserts == [{"type": "similar", "value": "y"}]


@pytest.mark.parametrize(
    "raw,msg",
    [
        ({"tests": [{"input": "x"}]}, "target"),
        ({"target": {"type": "mock"}, "tests": []}, "no tests"),
        ({"target": {"type": "mock"}, "tests": [{"id": "a", "input": "x"}, {"id": "a", "input": "y"}]}, "duplicate"),
        ({"target": {"type": "mock"}, "tests": [{"input": "x", "assert": [{"value": 1}]}]}, "type"),
    ],
)
def test_invalid_suites(raw, msg):
    with pytest.raises(SuiteError, match=msg):
        parse_suite(raw)


def test_redteam_expansion():
    suite = parse_suite({"target": {"type": "mock"}, "redteam": {"packs": ["harmful"], "secrets": ["s3cret"]}})
    assert len(suite.cases) == 3 and suite.secrets == ["s3cret"]
    assert {"type": "refuses"} in suite.cases[0].asserts


def test_run_mock_suite():
    result = run_suite(parse_suite(MOCK_SUITE))
    assert result.passed_count == 1 and not result.ok
    assert [c.id for c in result.cases] == ["capital", "unknown"]


def test_tag_filter():
    result = run_suite(parse_suite(MOCK_SUITE), tags=["hard"])
    assert [c.id for c in result.cases] == ["unknown"]


def test_target_errors_become_failures():
    class Boom(Target):
        def _call(self, case):
            raise RuntimeError("rate limited")

    result = run_suite(parse_suite(MOCK_SUITE), target=Boom({}))
    assert result.passed_count == 0
    assert "rate limited" in result.cases[0].error


def test_repeat_detects_flaky_outputs():
    class Flaky(Target):
        calls = 0

        def _call(self, case):
            Flaky.calls += 1
            return "Paris" if Flaky.calls % 2 else "Lyon"

    suite = parse_suite({**MOCK_SUITE, "tests": MOCK_SUITE["tests"][:1], "repeat": 4, "concurrency": 1})
    case = run_suite(suite, target=Flaky({})).cases[0]
    assert not case.passed and case.attempt_pass_rate == 0.5


def test_tests_file_jsonl(tmp_path):
    (tmp_path / "cases.jsonl").write_text('{"id": "j1", "input": "capital?"}\n', encoding="utf-8")
    (tmp_path / "s.yaml").write_text("target: {type: mock}\ntests_file: cases.jsonl\n", encoding="utf-8")
    assert load_suite(tmp_path / "s.yaml").cases[0].id == "j1"


def test_example_suite_passes_and_v2_regresses(monkeypatch):
    suite = load_suite(EXAMPLE)
    baseline = run_suite(suite)
    assert baseline.ok, [c.id for c in baseline.cases if not c.passed]

    monkeypatch.setenv("SUPPORT_BOT_VERSION", "2")
    current = run_suite(suite)
    diff = compare.compare(baseline.to_dict(), current.to_dict())
    assert {"refunds/window", "injection/system-prompt", "privacy/other-customers"} <= set(diff.regressions)
    assert "Regressions" in compare.to_markdown(diff)


def test_reports(tmp_path):
    result = run_suite(parse_suite(MOCK_SUITE))
    report.write_json(result, tmp_path / "r.json")
    report.write_junit(result, tmp_path / "j.xml")
    report.write_html(result, tmp_path / "r.html")
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["summary"]["failed"] == 1
    assert 'failures="1"' in (tmp_path / "j.xml").read_text(encoding="utf-8")
    page = (tmp_path / "r.html").read_text(encoding="utf-8")
    assert "__DATA__" not in page and '"capital"' in page
    assert "| `unknown` | contains |" in report.to_markdown(result)


def test_cli_run_and_baseline(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    base = tmp_path / "base.json"
    assert cli.main(["run", str(EXAMPLE), "-q", "-o", str(base)]) == 0
    monkeypatch.setenv("SUPPORT_BOT_VERSION", "2")
    assert cli.main(["run", str(EXAMPLE), "-q", "--baseline", str(base), "--threshold", "0"]) == 1
    assert "Regressions" in capsys.readouterr().out


def test_cli_init(tmp_path):
    path = tmp_path / "evalforge.yaml"
    assert cli.main(["init", str(path)]) == 0
    assert load_suite(path).cases
    assert cli.main(["init", str(path)]) == 1


def test_output_dict_target():
    class WithContext(Target):
        def _call(self, case):
            return {"output": "ok", "context": ["doc a", "doc b"], "usage": {"input_tokens": 3}}

    out: Output = WithContext({}).complete(parse_suite(MOCK_SUITE).cases[0])
    assert out.context == "doc a\ndoc b" and out.usage == {"input_tokens": 3}
