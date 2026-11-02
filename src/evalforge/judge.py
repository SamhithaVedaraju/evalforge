"""LLM-as-judge: grade an output against a natural-language rubric."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .core import TestCase

JUDGE_PROMPT = """You are a strict evaluator grading an AI assistant's answer.

<question>
{input}
</question>

<reference_context>
{context}
</reference_context>

<answer>
{output}
</answer>

<rubric>
{rubric}
</rubric>

Grade the answer ONLY against the rubric. Respond with a single JSON object and nothing else:
{{"reason": "<one or two sentences>", "score": <number from 0 to 1>, "pass": <true|false>}}"""

_API_KEYS = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class JudgeUnavailable(RuntimeError):
    """Raised when no judge is configured or its API key is missing."""


class Judge:
    def __init__(self, config: dict[str, Any] | None):
        self.config = config or {}
        self.provider = self.config.get("provider", "anthropic")
        self._target = None

    def available(self) -> bool:
        if self.provider == "mock":
            return True
        return bool(os.environ.get(_API_KEYS.get(self.provider, ""), ""))

    def _get_target(self):
        if self._target is None:
            from .providers import make_target

            self._target = make_target({"type": self.provider, "temperature": 0, **self.config})
        return self._target

    def grade(self, rubric: str, case: TestCase, output: str, context: str | None) -> tuple[bool, float, str]:
        if not self.available():
            raise JudgeUnavailable(f"set {_API_KEYS.get(self.provider, 'an API key')} to run llm_rubric checks")
        prompt = JUDGE_PROMPT.format(
            input=case.input, context=context or "(none)", output=output, rubric=rubric
        )
        judge_case = TestCase(id=f"judge:{case.id}", input=prompt)
        result = self._get_target().complete(judge_case)
        if result.error:
            raise RuntimeError(f"judge call failed: {result.error}")
        return parse_verdict(result.text, self.config.get("pass_score", 0.7))


def parse_verdict(text: str, pass_score: float = 0.7) -> tuple[bool, float, str]:
    """Parse the judge's JSON, tolerating code fences or surrounding prose."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"judge returned no JSON: {text[:200]!r}")
    data = json.loads(match.group(0))
    score = max(0.0, min(1.0, float(data.get("score", 1.0 if data.get("pass") else 0.0))))
    passed = bool(data["pass"]) if "pass" in data else score >= pass_score
    return passed, score, str(data.get("reason", ""))
