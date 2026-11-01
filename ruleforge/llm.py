"""LLM providers.

- AnthropicProvider calls the Messages API over plain HTTPS (no SDK needed).
- ReplayProvider plays back a cassette: a JSON list of model responses. It
  makes the whole pipeline (including the repair loop) deterministic in tests
  and CI, and lets anyone run the demo without an API key.
- Wrapping AnthropicProvider in a Recorder saves a live session as a cassette.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Protocol


class Provider(Protocol):
    def complete(self, system: str, messages: list[dict]) -> str: ...


class AnthropicProvider:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, api_key: str | None = None, max_tokens: int = 8000):
        self.model = model or os.environ.get("RULEFORGE_MODEL", "claude-sonnet-5-5")
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set (use --cassette for offline replay)")
        self.max_tokens = max_tokens

    def complete(self, system: str, messages: list[dict]) -> str:
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }).encode()
        req = urllib.request.Request(self.URL, data=body, method="POST", headers={
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        })
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.load(resp)
        return "".join(b.get("text", "") for b in data["content"] if b["type"] == "text")


class ReplayProvider:
    def __init__(self, cassette: str | Path):
        self.responses = json.loads(Path(cassette).read_text())["responses"]
        self.calls = 0

    def complete(self, system: str, messages: list[dict]) -> str:
        if self.calls >= len(self.responses):
            raise RuntimeError("cassette exhausted: the pipeline asked for more turns than were recorded")
        out = self.responses[self.calls]
        self.calls += 1
        return out if isinstance(out, str) else "```json\n" + json.dumps(out, indent=2) + "\n```"


class Recorder:
    def __init__(self, inner: Provider, path: str | Path):
        self.inner, self.path, self.responses = inner, Path(path), []

    def complete(self, system: str, messages: list[dict]) -> str:
        out = self.inner.complete(system, messages)
        self.responses.append(out)
        self.path.write_text(json.dumps({"responses": self.responses}, indent=2))
        return out
