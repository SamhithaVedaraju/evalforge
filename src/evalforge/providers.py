"""Targets: the thing under test.

A target turns a TestCase into an Output. Built-in targets:

- ``python``    call your own app function (``module:function``)
- ``anthropic`` call a Claude model through the Anthropic SDK
- ``openai``    call an OpenAI chat model
- ``mock``      canned responses, for offline demos and tests
"""

from __future__ import annotations

import importlib
import os
import re
import sys
import time
from typing import Any, Callable

from .core import Output, TestCase

_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, case: TestCase) -> str:
    """Fill ``{{var}}`` placeholders from the case's input, context and vars."""
    values = {"input": case.input, "context": case.context or "", **case.vars}
    return _VAR.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


def load_ref(ref: str) -> Callable[..., Any]:
    """Import ``package.module:attr``. The working directory is importable."""
    if ":" not in ref:
        raise ValueError(f"expected 'module:function', got {ref!r}")
    module_name, attr = ref.split(":", 1)
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    return getattr(importlib.import_module(module_name), attr)


class Target:
    name = "target"

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def complete(self, case: TestCase) -> Output:
        start = time.perf_counter()
        try:
            result = self._call(case)
        except Exception as exc:  # the target failing is a test result, not a crash
            return Output(text="", latency_ms=_ms(start), error=f"{type(exc).__name__}: {exc}")
        text, context, usage = _normalize(result)
        return Output(text=text, latency_ms=_ms(start), context=context or case.context, usage=usage)

    def _call(self, case: TestCase) -> Any:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


class PythonTarget(Target):
    name = "python"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.fn = load_ref(config["ref"])

    def _call(self, case: TestCase) -> Any:
        return self.fn(case.input, **case.vars)

    def describe(self) -> str:
        return f"python:{self.config['ref']}"


class MockTarget(Target):
    """Returns the first response whose regex key matches the input."""

    name = "mock"

    def _call(self, case: TestCase) -> Any:
        for pattern, reply in (self.config.get("responses") or {}).items():
            if re.search(pattern, case.input, re.IGNORECASE):
                return render(reply, case)
        return self.config.get("default", "I don't know.")


class _ChatTarget(Target):
    default_model = ""

    @property
    def model(self) -> str:
        return self.config.get("model", self.default_model)

    def messages(self, case: TestCase) -> tuple[str | None, str]:
        system = self.config.get("system")
        prompt = render(self.config.get("prompt", "{{input}}"), case)
        return (render(system, case) if system else None), prompt

    def describe(self) -> str:
        return f"{self.name}:{self.model}"


class AnthropicTarget(_ChatTarget):
    name = "anthropic"
    default_model = "claude-haiku-5-5"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        import anthropic  # optional dependency: pip install evalforge[anthropic]

        self.client = anthropic.Anthropic()

    def _call(self, case: TestCase) -> Any:
        system, prompt = self.messages(case)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.config.get("max_tokens", 1024),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        if "temperature" in self.config:
            kwargs["temperature"] = self.config["temperature"]
        resp = self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return {"output": text, "usage": usage}


class OpenAITarget(_ChatTarget):
    name = "openai"
    default_model = "gpt-4o-mini"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        import openai  # optional dependency: pip install evalforge[openai]

        self.client = openai.OpenAI()

    def _call(self, case: TestCase) -> Any:
        system, prompt = self.messages(case)
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        kwargs: dict[str, Any] = {"model": self.model, "messages": messages}
        if "temperature" in self.config:
            kwargs["temperature"] = self.config["temperature"]
        resp = self.client.chat.completions.create(**kwargs)
        usage = {}
        if resp.usage:
            usage = {"input_tokens": resp.usage.prompt_tokens, "output_tokens": resp.usage.completion_tokens}
        return {"output": resp.choices[0].message.content or "", "usage": usage}


TARGETS: dict[str, type[Target]] = {
    "python": PythonTarget,
    "mock": MockTarget,
    "anthropic": AnthropicTarget,
    "openai": OpenAITarget,
}


def make_target(config: dict[str, Any]) -> Target:
    kind = config.get("type")
    if kind not in TARGETS:
        raise ValueError(f"unknown target type {kind!r}; choose from {sorted(TARGETS)}")
    return TARGETS[kind](config)


def _normalize(result: Any) -> tuple[str, str | None, dict[str, int]]:
    """Targets may return a string or a dict with output/context/usage."""
    if isinstance(result, dict):
        context = result.get("context")
        if isinstance(context, list):
            context = "\n".join(map(str, context))
        return str(result.get("output", "")), context, result.get("usage") or {}
    return str(result), None, {}


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000
