"""core/providers/registry.py — the provider chain (Phase 4).

Reads config/providers.json, builds the configured provider chain
(primary → fallback → offline), and exposes one call:

    from core.providers import registry
    result = registry.generate("Explain quantum dots briefly.")

`generate()` walks the chain: primary first, then each fallback, then the
offline (local) leg. It NEVER raises and NEVER returns None — on total
failure it returns a ProviderResult with an honest, user-safe message
explaining what happened and what to do. The app does not crash because a
provider is down.

Failure semantics per rung (graceful handling, no crash):
    invalid/expired key   → AuthError     → try next rung; note it
    rate limit / 429      → RateLimitError→ skip rung (cooldown handled by
                                          the provider itself)
    model retired/missing → ModelUnavailableError → try next rung
    no network            → NetworkError  → try next rung
    provider 5xx/bad body → OutageError   → try next rung
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from .base import (AIProvider, ProviderError, ProviderResult,
                   provider_class, register_provider_class)
from .gemini import GeminiProvider
from .gemini_live import GeminiLiveProvider
from .ollama import OllamaProvider
from .openrouter import OpenRouterProvider

# Built-ins, registered under their config keys.
register_provider_class("gemini", GeminiProvider)
register_provider_class("gemini_live", GeminiLiveProvider)
register_provider_class("openrouter", OpenRouterProvider)
register_provider_class("ollama", OllamaProvider)

_BASE = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _BASE / "config" / "providers.json"

_lock = threading.Lock()
_config_cache: Optional[dict] = None
_providers_cache: dict[str, AIProvider] = {}


def load_config(refresh: bool = False) -> dict:
    """config/providers.json as a dict. Cached; missing file → empty chain."""
    global _config_cache
    with _lock:
        if _config_cache is not None and not refresh:
            return _config_cache
        try:
            _config_cache = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            _config_cache = {"chain": [], "providers": {}}
        return _config_cache


def chain() -> list[str]:
    """Ordered provider keys: primary → fallbacks → offline leg."""
    cfg = load_config()
    configured = [k for k in (cfg.get("chain") or [])]
    if configured:
        return configured
    # No chain configured: default to primary + offline, like the mission spec.
    default = cfg.get("default_provider", "gemini")
    return [default, "ollama"]


def get(name: str, refresh: bool = False) -> Optional[AIProvider]:
    """Instantiate (cached) the provider for a config key. None if unknown or
    disabled — callers treat that as "rung unavailable", not an error."""
    with _lock:
        if name in _providers_cache and not refresh:
            return _providers_cache[name]
    cfg = load_config()
    section = (cfg.get("providers") or {}).get(name)
    if not section or not section.get("enabled", True):
        return None
    cls = provider_class(name)
    if cls is None:
        return None
    try:
        inst = cls(section)
    except Exception:
        return None
    with _lock:
        _providers_cache[name] = inst
    return inst


def available_providers() -> list[dict[str, Any]]:
    """health_check() for every configured, enabled provider. Never raises."""
    out = []
    for name in chain():
        p = get(name)
        if p is None:
            out.append({"provider": name, "available": False,
                        "reason": "not configured or disabled"})
            continue
        try:
            out.append(p.health_check())
        except Exception as e:  # noqa: BLE001 — a probe must not crash us
            out.append({"provider": name, "available": False,
                        "reason": f"health check failed: {e}"})
    return out


def generate(prompt: str, *, system: str = "", timeout_s: float = 30.0,
             json_mode: bool = False, skip: tuple[str, ...] = ()) -> ProviderResult:
    """Generate text via the provider chain. Never raises, never returns None.

    On total failure the result text is a plain-language explanation the
    assistant can speak — including what the user can do (check key, go
    offline, wait out the rate limit). No API keys or tracebacks leak.
    """
    failures: list[tuple[str, str]] = []
    tried_any = False

    for name in chain():
        if name in skip:
            continue
        provider = get(name)
        if provider is None:
            continue
        ok, reason = provider.is_available()
        if not ok:
            failures.append((name, f"unavailable: {reason}"))
            continue
        tried_any = True
        try:
            result = provider.complete(prompt, system=system,
                                       timeout_s=timeout_s, json_mode=json_mode)
        except ProviderError as e:
            failures.append((name, f"{type(e).__name__}: {e.reason or e}"))
            continue
        except Exception as e:  # noqa: BLE001 — last-resort net, stay honest
            failures.append((name, f"unexpected failure: {type(e).__name__}"))
            continue
        result.used_fallback = bool(failures)
        return result

    return ProviderResult(
        text=_failure_message(failures, tried_any),
        provider="none", model="none", used_fallback=True)


def _failure_message(failures: list[tuple[str, str]], tried_any: bool) -> str:
    if not failures and not tried_any:
        return ("I couldn't reach any AI provider — none is configured. "
                "Add a Gemini or OpenRouter key in the settings, or install "
                "Ollama locally for fully offline answers.")
    detail = "; ".join(f"{n}: {r}" for n, r in failures[:3])
    hint = ("If this keeps happening, check your API keys in the settings, "
            "or install Ollama for answers that work without any key.")
    return (f"I couldn't get an answer right now ({detail}). {hint}")


def reset() -> None:
    """Drop caches (used by tests and by the settings UI after a config edit)."""
    global _config_cache, _providers_cache
    with _lock:
        _config_cache = None
        _providers_cache = {}
