"""core/providers/ollama.py — local Ollama provider (Phase 4).

The offline leg of the chain: no key, no network beyond localhost, no quota.
Adapter over core/llm_client.py (`call_llm_text`), which already knows how to
auto-start `ollama serve` and pick the configured model.
"""

from __future__ import annotations

from typing import Optional

from .base import (AIProvider, NetworkError, OutageError, ProviderError,
                   ProviderResult)


class OllamaProvider(AIProvider):
    name = "ollama"
    tier = "Local (Free)"  # truly free; needs the model downloaded locally
    capabilities = frozenset({"text", "json"})

    def __init__(self, cfg: Optional[dict] = None):
        self._cfg = cfg or {}

    def is_available(self) -> tuple[bool, str]:
        if not self._cfg.get("enabled", True):
            return False, "disabled in config/providers.json"
        try:
            from core import llm_client as lc
            url, model = lc.get_llm_settings()
            if lc.ensure_ollama_running(timeout=5):
                return True, f"ok (ollama at {url}, model {model})"
            return False, f"ollama not reachable at {url} and could not be started"
        except Exception as e:  # noqa: BLE001 — never raise from a probe
            return False, f"probe failed: {e}"

    def complete(self, prompt: str, *, system: str = "",
                 timeout_s: float = 60.0, json_mode: bool = False) -> ProviderResult:
        try:
            from core import llm_client as lc
        except Exception as e:
            raise OutageError(f"core/llm_client.py unavailable: {e}")
        try:
            url, model = lc.get_llm_settings()
            if not lc.ensure_ollama_running(timeout=10):
                raise NetworkError(f"ollama not reachable at {url}")
            text = lc.call_llm_text(
                prompt,
                system=system or None,
                timeout=max(10, int(timeout_s)),
            )
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 — translated
            raise NetworkError(f"ollama call failed: {str(e)[:160]}")
        if not (text or "").strip():
            raise OutageError("ollama returned an empty reply")
        return ProviderResult(text=text.strip(), provider=self.name, model=model)
