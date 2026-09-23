"""core/providers/gemini.py — Gemini one-shot text provider (Phase 4).

Adapter over the existing core/gemini.py ladder (`call()/text()/as_json()`),
which remains the single place Gemini REST logic lives. This class:

  * reads its model ladder from config/providers.json (`gemini.text_models`),
    falling back to the module's built-in 3.x ladder;
  * translates every failure into the ProviderError taxonomy, mapping the
    existing ladder's failure modes (429 → RateLimitError, missing key →
    AuthError, retired/unknown model → ModelUnavailableError);
  * honors "deprecated" pins: a 2.5-era model in the ladder is tried like any
    other rung, but its use is logged as deprecated so the config can be
    cleaned up (2.5 REST shuts down 2026-10-16 — config ships 3.x first).

The Live voice session is NOT here — see gemini_live.py (interface only;
the session lifecycle lives in main.py's ShiraziLive).
"""

from __future__ import annotations

from typing import Any, Optional

from .base import (AIProvider, ProviderError, AuthError, RateLimitError,
                   ModelUnavailableError, NetworkError, OutageError,
                   ProviderResult)


def _gemini_module():
    from core import gemini as g  # local import: core/gemini.py imports nothing heavy
    return g


class GeminiProvider(AIProvider):
    name = "gemini"
    tier = "Free API Tier"  # quota-limited free tier; NOT unlimited
    capabilities = frozenset({"text", "json", "vision", "search"})

    def __init__(self, cfg: Optional[dict] = None):
        self._cfg = cfg or {}
        self._ladder = list(self._cfg.get("text_models") or [])

    # ── Availability ──────────────────────────────────────────────────────────
    def _key(self) -> str:
        g = _gemini_module()
        key = g.api_key()
        return key

    def is_available(self) -> tuple[bool, str]:
        try:
            if not self._key():
                return False, "no Gemini API key is configured"
        except Exception:
            return False, "could not read the Gemini API key"
        return True, "ok"

    # ── Completion ────────────────────────────────────────────────────────────
    def complete(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> ProviderResult:
        if not self._key():
            raise AuthError("no Gemini API key is configured")

        g = _gemini_module()
        ladder = self._ladder or g.default_text_ladder()

        contents = prompt
        config = {"system_instruction": system} if system else None
        last_err: Optional[ProviderError] = None

        for model in ladder:
            if model == "live":  # Live rung belongs to gemini_live.py, not here
                continue
            try:
                text = g.call_once(contents, model, config=config,
                                   timeout_ms=max(10000, int(timeout_s * 1000)))
                if _is_deprecated(model):
                    from core import logger as _log
                    _log.warn("providers",
                              f"deprecated model '{model}' was used — it retires "
                              f"2026-10-16; update config/providers.json")
                return ProviderResult(text=text, provider=self.name,
                                      model=model,
                                      used_fallback=last_err is not None)
            except Exception as e:  # noqa: BLE001 — translated below
                last_err = _classify(e)
                if isinstance(last_err, RateLimitError):
                    g.cool_model(model)

        raise last_err or OutageError("all Gemini text models failed")


def _is_deprecated(model: str) -> bool:
    m = (model or "").lower()
    return "2.5" in m or "2.0" in m


def _classify(exc: Exception) -> ProviderError:
    """Map a raw exception to the taxonomy without parsing brittle strings more
    than necessary — status codes and keyword families are stable enough."""
    msg = str(exc)
    low = msg.lower()
    if any(k in msg for k in ("401", "403")) or "api key" in low or "invalid key" in low:
        return AuthError(msg[:160])
    if "429" in msg or "resource_exhausted" in low or "quota" in low:
        return RateLimitError(msg[:160])
    if "404" in msg or "not found" in low or "not supported" in low or "retired" in low:
        return ModelUnavailableError(msg[:160])
    if any(k in low for k in ("timed out", "timeout", "connection", "dns", "unreachable",
                              "network", "name resolution")):
        return NetworkError(msg[:160])
    return OutageError(f"{type(exc).__name__}: {msg[:160]}")
