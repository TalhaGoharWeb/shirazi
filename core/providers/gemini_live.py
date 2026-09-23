"""core/providers/gemini_live.py — Gemini Live voice-session provider (Phase 4).

INTERFACE + DOCUMENTED LIMITATION — not a full reimplementation.

A Gemini Live voice session is a long-lived, bidirectional websocket tied to
the app's mic/speaker pipelines, the HUD, and the phone-audio relay. That
lifecycle already exists and is correct in main.py's `ShiraziLive`
(connect, mic streaming, tool-call dispatch, reconnect). Re-implementing it
here as a "clean" provider would be a stub that *claims* voice capability it
does not have — so this module does the honest thing:

  * it exposes the AIProvider contract for the Live path (capabilities,
    config read, model name, availability),
  * `create_session()` returns the existing ShiraziLive wiring contract and
    documents exactly what the caller (main.py) must provide,
  * it does NOT fake a session.

If a future phase builds a standalone Live session manager, this is where it
lands — behind the same interface.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from .base import (AIProvider, ProviderError, AuthError, OutageError,
                   ProviderResult)


class GeminiLiveProvider(AIProvider):
    """The voice-conversation path. Wraps, does not replace, main.py."""

    name = "gemini_live"
    tier = "Free API Tier"  # shares the Gemini free-tier quota pool
    capabilities = frozenset({"live", "text"})

    DEFAULT_MODEL = "models/gemini-3.1-flash-live-preview"

    def __init__(self, cfg: Optional[dict] = None):
        self._cfg = cfg or {}
        self._model = self._cfg.get("live_model") or self.DEFAULT_MODEL

    # ── AIProvider contract ───────────────────────────────────────────────────
    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> tuple[bool, str]:
        try:
            from core import gemini as g
            if not g.api_key():
                return False, "no Gemini API key is configured"
        except Exception:
            return False, "could not read the Gemini API key"
        return True, f"ok (live session managed by main.py ShiraziLive, model {self._model})"

    def complete(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> ProviderResult:
        """One-shot text over a *throwaway* Live turn — this is the pattern
        core/gemini.py already uses and measures (the LIVE ladder rung)."""
        try:
            from core import gemini as g
        except Exception as e:
            raise OutageError(f"core/gemini.py unavailable: {e}")
        if not g.api_key():
            raise AuthError("no Gemini API key is configured")
        try:
            text = g.text(prompt, tier="live", config=None,
                          timeout_ms=max(10000, int(timeout_s * 1000)))
        except Exception as e:  # noqa: BLE001 — translated below
            from .gemini import _classify
            raise _classify(e)
        if not text:
            raise OutageError("the Live turn came back empty")
        return ProviderResult(text=text, provider=self.name, model=self._model)

    # ── The honest interface ──────────────────────────────────────────────────
    def create_session(self, *, on_tool_call: Callable, on_audio_out: Callable,
                       config_factory: Callable[[], Any]) -> dict:
        """Describe (not build) the Live voice session.

        The real session needs: mic PCM stream, speaker playback, Qt HUD
        marshalling, the tool registry, phone-audio relay — all of which live
        in main.py's ShiraziLive. This returns the contract a future
        implementation must satisfy, so nothing can "succeed" here while
        silently doing nothing.

        LIMITATION (documented, not hidden): there is intentionally no working
        standalone Live session in this module. Callers must use the
        ShiraziLive path in main.py until a phase implements one.
        """
        raise NotImplementedError(
            "GeminiLiveProvider.create_session is interface-only in Phase 4: "
            "the voice session lifecycle lives in main.py (ShiraziLive). "
            "Contract — on_tool_call(name, args)->str, on_audio_out(pcm)->None, "
            "config_factory()->LiveConnectConfig. See docs/PHASE4.md § providers."
        )

    def health_check(self) -> dict:
        h = super().health_check()
        h["note"] = "voice session implemented in main.py ShiraziLive (interface-only here)"
        return h
