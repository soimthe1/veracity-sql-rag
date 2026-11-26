from __future__ import annotations

from typing import Optional

from shared.config import InferenceConfig
from shared.llm.base import LlmProvider


def build_llm_provider(cfg: InferenceConfig) -> Optional[LlmProvider]:
    """Returns None when inference is disabled or its ai: profile is missing or
    switched off. Callers treat a None provider as "skip semantic inference,
    structural discovery continues" (spec section 14), never as an error."""
    if not cfg.effectively_enabled:
        return None
    return LlmProvider(cfg.profile)
