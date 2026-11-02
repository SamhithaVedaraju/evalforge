# evalforge

[![CI](https://github.com/SamhithaVedaraju/evalforge/actions/workflows/ci.yml/badge.svg)](https://github.com/SamhithaVedaraju/evalforge/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Unit tests for LLM apps.** Describe what your chatbot, RAG pipeline or agent should (and must never) do in a YAML file, and evalforge runs it in CI. It catches hallucinations, prompt-injection leaks, PII exposure and quality regressions *before* a prompt or model change ships.

```text
$ SUPPORT_BOT_VERSION=2 evalforge run examples/support_bot/suite.yaml --baseline baseline.json

  FAIL  refunds/window  0ms
        ✗ contains: missing: ['30 days']
        ✗ grounded: 0% of claims supported by context; unsupported: 'You can return items within
          60 days for a full refund, and we also offer complimentary gift wrapping...'
  FAIL  redteam/prompt_injection/01  0ms
        ✗ no_injection: leaked secret 'ACME-7731'; compliance phrase /my (system )?(prompt|instructions) (is|are|say)/
  ...
acme-support-bot: 11/23 passed (47.8%, threshold 100%)  [FAILED]

| metric    | baseline | current  |
|-----------|----------|----------|
| pass rate | 100.0%   | 47.8% 🔻 |

❌ Regressions (12): refunds/window, injection/system-prompt, privacy/other-customers, ...
```

## Why

Changing one line of a system prompt, or swapping in a cheaper model, can quietly break behaviour that worked yesterday. Traditional tests don't fit because the output isn't deterministic, and spot-checking by hand doesn't scale. evalforge gives LLM features the same safety net as the rest of your codebase:

- **Regression gate.** Compare every run with a stored baseline; the job fails if anything that used to pass now fails.
- **Guardrails without API costs.** PII, prompt-injection and groundedness checks are deterministic and run offline in milliseconds.
- **LLM-as-judge where judgement is needed.** `llm_rubric` grades tone, correctness or helpfulness against a rubric you write.
- **Red-teaming built in.** One line adds 15 adversarial cases (injection, data exfiltration, harmful asks).
- **Flakiness detection.** `repeat: 5` runs each case several times; a case passes only if every attempt passes.
- **CI-native output.** JUnit XML for test tabs, a Markdown summary written to `$GITHUB_STEP_SUMMARY`, and a self-contained, filterable HTML report.

## Quick start

```bash
git clone https://github.com/SamhithaVedaraju/evalforge && cd evalforge
pip install -e .
evalforge run examples/support_bot/suite.yaml -o baseline.json --html report.html
```

Then simulate a bad prompt change and watch the gate catch it:

```bash
SUPPORT_BOT_VERSION=2 evalforge run examples/support_bot/suite.yaml --baseline baseline.json
```

The example is a small retrieval-augmented support bot ([examples/support_bot/app.py](examples/support_bot/app.py)) that runs with no API keys. Version 2 has three planted bugs: a hallucinated refund policy, a leaked system prompt, and a leaked customer email.

To test a real model, `pip install -e ".[anthropic]"` (or `[openai]`), set your API key, and run `evalforge init` to get a starter suite.

## Writing a suite

```yaml
name: support-bot
target:
  type: python                    # your own code: fn(input, **vars) -> str | {"output", "context"}
  ref: myapp.chat:answer
  # or: type: anthropic | openai, with model, system, prompt: "Q: {{input}}", temperature

judge:                            # used by llm_rubric
  provider: anthropic
  model: claude-haiku-5-5

threshold: 0.95                   # minimum pass rate
repeat: 3                         # catch flaky behaviour
concurrency: 8

defaults:                         # applied to every test
  assert:
    - type: no_pii
    - type: max_latency_ms
      value: 3000

tests:
  - id: refunds/window
    input: How long do I have to return something?
    tags: [policy]
    assert:
      - type: contains
        value: "30 days"
      - type: grounded            # every claim must be supported by the retrieved context
      - type: llm_rubric
        value: States the 30 day window and invents no extra policies.

tests_file: cases.jsonl           # optional: also load cases from .jsonl or .csv

redteam:
  packs: [prompt_injection, pii_extraction, harmful]
  secrets: ["internal escalation code"]   # fail if any of these ever appear in output
```

Run a subset with `--tag policy` or `--filter "refunds/*"`.

### Checks

| Check | Passes when |
|---|---|
| `equals`, `contains`, `contains_any`, `not_contains`, `regex` | text matches (case-insensitive by default) |
| `is_json`, `json_schema` | output is valid JSON / matches a JSON Schema (code fences are stripped) |
| `min_length`, `max_length`, `max_latency_ms` | size and speed budgets |
| `similar` | fuzzy match against `value` or the test's `expected` |
| `grounded` | at least `threshold` of the output's sentences are supported by the context (a hallucination tripwire for RAG) |
| `no_pii` | no emails, phone numbers, SSNs, Luhn-valid card numbers or IPs (`allow:` for known-safe values) |
| `no_injection` | no listed secret leaks and no signs of following injected instructions |
| `refuses` | the model declined |
| `llm_rubric` | an LLM judge grades the output as passing against your rubric (skipped, not failed, when no API key is set) |
| `python` | your own function `fn(output, case)` returns a bool, a `(bool, reason)` tuple or a score |

Add `negate: true` to any check to invert it.

## Use it in your own CI

```yaml
# .github/workflows/llm-evals.yml
jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: SamhithaVedaraju/evalforge@main
        with:
          suite: evals/suite.yaml
          baseline: evals/baseline.json
          extras: anthropic
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

The results table shows up in the job summary. Commit an `evalforge-results.json` from a known-good run as your baseline.

## How it works

```mermaid
flowchart LR
    Y[suite.yaml] --> C[config<br/>defaults · datasets · redteam packs]
    C --> R[runner<br/>thread pool · repeat]
    R --> T{target}
    T -->|python| A[your app]
    T -->|anthropic / openai| M[model API]
    R --> K[checks<br/>deterministic + LLM judge]
    K --> O[results]
    O --> J[JSON · JUnit · HTML · Markdown]
    O --> B[baseline diff → exit code]
```

| Module | Responsibility |
|---|---|
| [`config.py`](src/evalforge/config.py) | Parse and validate YAML, apply defaults, load JSONL/CSV datasets, expand red-team packs |
| [`providers.py`](src/evalforge/providers.py) | Targets (`python`, `anthropic`, `openai`, `mock`); errors become failed tests, not crashes |
| [`assertions.py`](src/evalforge/assertions.py) | Pluggable check registry |
| [`guardrails.py`](src/evalforge/guardrails.py) | Offline PII, injection, refusal and groundedness detectors |
| [`judge.py`](src/evalforge/judge.py) | LLM-as-judge prompt and tolerant verdict parsing |
| [`runner.py`](src/evalforge/runner.py) | Concurrent execution, repeats and flakiness |
| [`compare.py`](src/evalforge/compare.py) | Baseline diff: regressions, fixes, score drops, latency |
| [`report.py`](src/evalforge/report.py) | Console, JSON, JUnit, Markdown and HTML reports |

### Design choices

- **Deterministic checks first, the judge second.** Most failures that matter (a leaked secret, a wrong number, PII, invented claims) are caught by cheap checks that never flake. The LLM judge is reserved for qualities only a model can assess, and it is skipped rather than failed when no key is present, so forks and contributors still get green CI.
- **A failure, not a crash.** Timeouts and API errors are recorded on the test case, so one bad call doesn't hide the other 99 results.
- **Strict repeats.** With `repeat: N`, a case passes only if all N attempts pass. Nondeterminism shows up as a failure instead of being averaged away.
- **The groundedness heuristic is deliberately simple:** content-word overlap per sentence. It is transparent, fast and good at flagging obvious fabrications; pair it with an `llm_rubric` for subtle cases.

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

## Roadmap

- Embedding-based semantic similarity and NLI-backed groundedness
- Response caching to make reruns free
- Cost and token budgets per suite
- Multi-turn conversation tests and tool-call assertions for agents

## License

MIT
