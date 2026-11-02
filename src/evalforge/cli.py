"""Command-line interface.

    evalforge run suite.yaml --html report.html --junit junit.xml --baseline base.json
    evalforge compare base.json results.json
    evalforge init
    evalforge list-checks
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, compare, report
from .assertions import REGISTRY
from .config import SuiteError, load_suite
from .redteam import PACKS
from .runner import run_suite, select_cases

STARTER = """\
# evalforge suite. Run with: evalforge run evalforge.yaml
name: my-llm-app
target:
  type: anthropic            # python | anthropic | openai | mock
  model: claude-haiku-5-5
  system: You are a concise, friendly support assistant for Acme Inc.
  prompt: "{{input}}"

judge:                       # used by llm_rubric checks; skipped if no API key is set
  provider: anthropic
  model: claude-haiku-5-5

threshold: 0.9               # minimum pass rate for the run to succeed
repeat: 1                    # >1 runs each case several times to catch flaky outputs

defaults:
  assert:
    - type: no_pii
    - type: max_latency_ms
      value: 10000

tests:
  - id: greeting
    input: Hi there!
    assert:
      - type: max_length
        value: 400
      - type: llm_rubric
        value: Replies with a short, friendly greeting and offers to help.

redteam:
  packs: [prompt_injection]
  secrets: ["You are a concise, friendly support assistant"]
"""


def _write_summary(markdown: str, path: str | None) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown + "\n")


def cmd_run(args: argparse.Namespace) -> int:
    try:
        suite = load_suite(args.suite)
    except (SuiteError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.repeat:
        suite.repeat = args.repeat
    if args.threshold is not None:
        suite.threshold = args.threshold

    selected = len(select_cases(suite.cases, args.tag, args.filter))
    print(f"evalforge {__version__} · {suite.name} · {selected} test(s)")
    result = run_suite(
        suite,
        tags=args.tag,
        pattern=args.filter,
        on_result=None if args.quiet else report.print_case,
    )
    report.print_summary(result)

    data = result.to_dict()
    if args.output:
        report.write_json(result, args.output)
    if args.html:
        report.write_html(result, args.html)
        print(f"  HTML report: {args.html}")
    if args.junit:
        report.write_junit(result, args.junit)

    summary_path = args.markdown or os.environ.get("GITHUB_STEP_SUMMARY")
    _write_summary(report.to_markdown(result), summary_path)

    exit_code = 0 if result.ok else 1
    if args.baseline:
        baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        diff = compare.compare(baseline, data)
        md = compare.to_markdown(diff)
        print()
        print(md)
        _write_summary(md, summary_path)
        if diff.has_regressions:
            exit_code = 1
    return exit_code


def cmd_compare(args: argparse.Namespace) -> int:
    old = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    new = json.loads(Path(args.current).read_text(encoding="utf-8"))
    diff = compare.compare(old, new, args.score_tolerance)
    print(compare.to_markdown(diff))
    return 1 if diff.has_regressions else 0


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.write_text(STARTER, encoding="utf-8")
    print(f"wrote {path}. Next: evalforge run {path}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print("assertions:")
    for name in sorted(REGISTRY):
        print(f"  {name}")
    print("redteam packs:")
    for name, attacks in PACKS.items():
        print(f"  {name} ({len(attacks)} attacks)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="evalforge", description="Regression tests and guardrails for LLM apps.")
    p.add_argument("--version", action="version", version=f"evalforge {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="run a suite")
    r.add_argument("suite")
    r.add_argument("-o", "--output", help="write JSON results here (use as a future --baseline)")
    r.add_argument("--html", help="write a self-contained HTML report")
    r.add_argument("--junit", help="write JUnit XML for CI test views")
    r.add_argument("--markdown", help="append a Markdown summary (defaults to $GITHUB_STEP_SUMMARY)")
    r.add_argument("--baseline", help="fail if any test that passed in this JSON now fails")
    r.add_argument("--tag", action="append", help="only run tests with this tag (repeatable)")
    r.add_argument("--filter", help="only run tests whose id matches this glob")
    r.add_argument("--repeat", type=int, help="override the suite's repeat count")
    r.add_argument("--threshold", type=float, help="override the suite's pass-rate threshold")
    r.add_argument("-q", "--quiet", action="store_true", help="only print the summary")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("compare", help="diff two JSON result files")
    c.add_argument("baseline")
    c.add_argument("current")
    c.add_argument("--score-tolerance", type=float, default=0.1)
    c.set_defaults(func=cmd_compare)

    i = sub.add_parser("init", help="write a starter suite")
    i.add_argument("path", nargs="?", default="evalforge.yaml")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    sub.add_parser("list-checks", help="list assertion types and redteam packs").set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
