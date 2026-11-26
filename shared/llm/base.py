"""
LLM access for the engine (semantic proposals: entity naming, descriptions,
synonyms, abbreviation guesses, sensitivity hints) and the answer service
(question understanding, SQL generation, answer synthesis). The LLM's output
is never trusted as confidence or fact; engine/confidence.py gives it a small,
configurable weight alongside deterministic evidence
(catalog.confidence.weights.llm_semantic_interpretation).

Provider, endpoint, model, timeout and thinking control come from an `ai:`
profile in config/config.yaml and are applied by shared/ai_providers.py
(ollama | openai). Nothing here hard-codes a provider or model.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

from shared import ai_providers

logger = logging.getLogger("shared.llm")

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINKING_TAG_RE = re.compile(r"<thinking>.*?</thinking>", re.IGNORECASE | re.DOTALL)


def strip_reasoning(text: str) -> str:
    """Strip a reasoning model's inlined <think>/<thinking> block, if any.
    A no-op for non-reasoning models' output."""
    if not text:
        return text
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINKING_TAG_RE.sub("", text)
    return text.strip()


@dataclass
class LlmResult:
    ok: bool
    text: str = ""
    error: Optional[str] = None


class LlmProvider:
    """One `ai:` profile. complete() never raises for an unreachable endpoint,
    a timeout or a bad reply: callers rely on LlmResult.ok being False to fall
    back to structural-only cataloging (AI must never be a single point of
    failure)."""

    def __init__(self, profile: str = "default"):
        self.profile = profile

    def reachable(self) -> bool:
        # OpenAI-compatible servers have no universal health endpoint;
        # reachability is inferred from real calls succeeding.
        if ai_providers.profile(self.profile)["provider"] != "ollama":
            return True
        return "error" not in ai_providers.check(self.profile)

    def complete(self, prompt: str, *, system: Optional[str] = None, json_mode: bool = False) -> LlmResult:
        cfg = ai_providers.profile(self.profile)
        if cfg["provider"] == "openai" and cfg["suppress_thinking"]:
            # Qwen's model-level soft switch, honoured by its chat template whatever
            # server fronts it; ai_providers also sends chat_template_kwargs.
            prompt = f"{prompt}\n/no_think"
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        try:
            return LlmResult(ok=True, text=ai_providers.chat(messages, self.profile, json_mode=json_mode))
        except ai_providers.AIError as exc:
            message = str(exc)
            timed_out = "timed out" in message.lower()
            logger.warning("LLM request (ai.%s) %s", self.profile, "timed out" if timed_out else f"failed: {message}")
            return LlmResult(ok=False, error="timeout" if timed_out else message)
