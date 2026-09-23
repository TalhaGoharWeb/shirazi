"""core/providers/future.py — the "Future provider" extension hook (Phase 4).

Adding a new AI provider without touching Shirazi's own code:

    1. Subclass core.providers.base.AIProvider.
    2. Implement `is_available()` and `complete()`.
    3. Register it:

           from core.providers.base import register_provider_class
           from core.providers.future import FutureProviderTemplate

           class GroqProvider(FutureProviderTemplate):
               name = "groq"
               tier = "Free API Tier"
               capabilities = frozenset({"text", "json"})
               API_URL = "https://api.groq.com/openai/v1/chat/completions"
               KEY_ENV = "GROQ_API_KEY"
               KEY_CONFIG = "groq_api_key"

           register_provider_class("groq", GroqProvider)

    4. Add a "groq" section to config/providers.json and (optionally) add
       "groq" to the `chain` list.

The registry picks the class up automatically from the config key. The
template below handles key resolution, free-only enforcement and error
mapping so a future provider is ~30 lines instead of ~150.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .base import (AIProvider, AuthError, ModelUnavailableError, ProviderError,
                   ProviderResult)
from .openrouter import OpenRouterProvider


class FutureProviderTemplate(OpenRouterProvider):
    """OpenAI-compatible chat-completions provider with a different name.

    Override: name, tier, capabilities, API_URL, KEY_ENV, KEY_CONFIG,
    and optionally DEFAULT_FREE_MODELS.
    """

    name = "future"
    tier = "?"
    capabilities = frozenset({"text", "json"})

    API_URL = ""
    KEY_ENV = ""
    KEY_CONFIG = ""

    def __init__(self, cfg: Optional[dict] = None, transport=None):
        super().__init__(cfg=cfg, transport=transport)
        if not self.API_URL:
            raise ValueError(f"{type(self).__name__}: API_URL must be set")

    def _key(self) -> str:
        if self.KEY_ENV and os.environ.get(self.KEY_ENV):
            return os.environ[self.KEY_ENV]
        if self.KEY_CONFIG:
            try:
                base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
                with open(os.path.join(base, "config", "api_keys.json"),
                          encoding="utf-8") as f:
                    return str(json.load(f).get(self.KEY_CONFIG) or "")
            except Exception:
                return ""
        return ""


def register_provider(key: str, cls: type[AIProvider]) -> None:
    """Public extension hook. Same as base.register_provider_class; exported
    here so the "future provider" story has one obvious import path."""
    from .base import register_provider_class
    register_provider_class(key, cls)
