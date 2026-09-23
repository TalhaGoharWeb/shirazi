"""core/providers/base.py — the AIProvider contract + error taxonomy (Phase 4).

Every provider implements `AIProvider.complete()`. Providers NEVER raise
their raw SDK/library exceptions: they translate them into `ProviderError`
subclasses, and the registry decides whether to try the next provider in
the chain. Nothing here touches the network itself — that is each
implementation's job.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Error taxonomy ────────────────────────────────────────────────────────────
# Caught by core/providers/registry.py. Every branch of `complete()` maps to
# exactly one of these, so failover logic never has to parse message strings.

class ProviderError(Exception):
    """Base: a provider could not answer. Carries a human-safe reason."""

    def __init__(self, reason: str = ""):
        super().__init__(reason)
        self.reason = reason


class AuthError(ProviderError):
    """Invalid, expired, or missing API key / credentials."""


class RateLimitError(ProviderError):
    """429 / quota exhausted / too many requests."""


class ModelUnavailableError(ProviderError):
    """Named model does not exist, was retired, or is not enabled for the key."""


class NetworkError(ProviderError):
    """Connection failed, DNS, timeout — the provider was unreachable."""


class OutageError(ProviderError):
    """Provider reachable but erroring (5xx, bad gateway, malformed reply)."""


class ProviderResult:
    """What `complete()` hands back — always safe to show the user."""

    __slots__ = ("text", "provider", "model", "used_fallback")

    def __init__(self, text: str, provider: str = "", model: str = "",
                 used_fallback: bool = False):
        self.text = text
        self.provider = provider
        self.model = model
        self.used_fallback = used_fallback

    def __bool__(self) -> bool:
        return bool(self.text)


# ── The provider contract ─────────────────────────────────────────────────────

class AIProvider(abc.ABC):
    """One pluggable AI backend.

    Subclass, implement `complete()`, register with
    `core.providers.future.register_provider()` (or the built-ins in
    registry.py). Everything else — failover, key resolution, config — is
    handled around you.
    """

    # Short id, matches the key in config/providers.json, e.g. "gemini".
    name: str = "?"
    # Human tier label. MUST be one of:
    #   "Local (Free)" | "Free API Tier" | "Optional Paid Provider"
    tier: str = "?"
    # Subset of {"text", "json", "live", "vision", "search"}.
    capabilities: frozenset = frozenset()

    @abc.abstractmethod
    def is_available(self) -> tuple[bool, str]:
        """(available, reason). False + reason when misconfigured (no key,
        daemon not running). Must never raise and never do slow network I/O —
        a lightweight local probe (socket connect to localhost) is fine."""

    @abc.abstractmethod
    def complete(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> ProviderResult:
        """Generate text. Raises a ProviderError subclass on failure —
        never the vendor SDK's own exception, never a raw socket error."""

    def health_check(self) -> dict[str, Any]:
        """Cheap self-report for diagnostics UI. Never raises."""
        ok, reason = self.is_available()
        return {
            "provider": self.name,
            "tier": self.tier,
            "available": ok,
            "reason": reason,
            "capabilities": sorted(self.capabilities),
        }


# Extension point for future providers: a tiny, explicit registration table.
# core/providers/future.py documents the contract a third-party class must
# satisfy (which is exactly AIProvider + a factory taking the config dict).
_PROVIDER_CLASSES: dict[str, type[AIProvider]] = {}


def register_provider_class(key: str, cls: type[AIProvider]) -> None:
    """Register an AIProvider subclass under a config key (e.g. "groq")."""
    if not (isinstance(cls, type) and issubclass(cls, AIProvider)):
        raise TypeError("register_provider_class expects an AIProvider subclass")
    _PROVIDER_CLASSES[key] = cls


def provider_class(key: str) -> Optional[type[AIProvider]]:
    return _PROVIDER_CLASSES.get(key)
