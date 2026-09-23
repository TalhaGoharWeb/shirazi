"""core/providers/openrouter.py — OpenRouter free-tier provider (Phase 4).

OpenRouter's `:free` model endpoints are the free story for non-Google text:
keyed, rate-limited, quota-limited — "Free API Tier", never "unlimited".

FREE-FIRST RULE ENFORCED IN CODE: when the provider config sets
`"free_only": true` (the shipped default), only models whose id ends in
`:free` are ever called. A non-free model id in the config with free_only on
is rejected at call time, not silently billed.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from .base import (AIProvider, AuthError, RateLimitError, ModelUnavailableError,
                   NetworkError, OutageError, ProviderError, ProviderResult)


class OpenRouterProvider(AIProvider):
    name = "openrouter"
    tier = "Free API Tier"  # :free endpoints — rate/quota limited
    capabilities = frozenset({"text", "json"})

    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, cfg: Optional[dict] = None, transport=None):
        self._cfg = cfg or {}
        self._models = list(self._cfg.get("models") or [])
        self._free_only = bool(self._cfg.get("free_only", True))
        self._transport = transport  # injected in tests: fn(url, headers, payload, timeout)

    # ── Key resolution: env first, then config/api_keys.json key_config ────────
    def _key(self) -> str:
        env_name = self._cfg.get("key_env", "OPENROUTER_API_KEY")
        if os.environ.get(env_name):
            return os.environ[env_name]
        try:
            base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            with open(os.path.join(base, "config", "api_keys.json"),
                      encoding="utf-8") as f:
                data = json.load(f)
            return str(data.get(self._cfg.get("key_config", "openrouter_api_key")) or "")
        except Exception:
            return ""

    def is_available(self) -> tuple[bool, str]:
        if not self._cfg.get("enabled", True):
            return False, "disabled in config/providers.json"
        if not self._key():
            return False, "no OpenRouter API key configured (OPENROUTER_API_KEY / api_keys.json)"
        if not self._models:
            return False, "no models configured in config/providers.json"
        return True, "ok"

    # ── Completion ────────────────────────────────────────────────────────────
    def _pick_model(self) -> str:
        for m in self._models:
            if self._free_only and not m.endswith(":free"):
                continue
            return m
        if self._free_only:
            raise ModelUnavailableError(
                "free_only is on but no ':free' model is configured — "
                "refusing to call a billable model")
        return self._models[0]

    def complete(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> ProviderResult:
        key = self._key()
        if not key:
            raise AuthError("no OpenRouter API key is configured")
        try:
            model = self._pick_model()
        except ProviderError:
            raise

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        payload: dict = {"model": model, "messages": messages}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://shirazi.local",
            "X-Title": "Shirazi",
        }

        try:
            body = self._post(headers, payload, timeout_s)
            text = (body.get("choices", [{}])[0].get("message", {})
                    .get("content", "") or "").strip()
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001 — translated below
            raise _classify_http(e)

        if not text:
            raise OutageError("OpenRouter returned an empty reply")
        return ProviderResult(text=text, provider=self.name, model=model)

    # ── Transport (injectable for tests) ──────────────────────────────────────
    def _post(self, headers: dict, payload: dict, timeout_s: float) -> dict:
        if self._transport is not None:
            status, data = self._transport(self.API_URL, headers, payload, timeout_s)
        else:
            import urllib.request
            req = urllib.request.Request(
                self.API_URL, data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    status, data = resp.status, json.loads(resp.read().decode("utf-8"))
            except Exception as e:  # includes HTTPError
                raise _classify_http(e)
        if status == 200:
            return data
        raise _classify_status(status, data)

    def health_check(self) -> dict:
        h = super().health_check()
        h["free_only"] = self._free_only
        return h


def _classify_status(status: int, data: dict) -> ProviderError:
    msg = str(data)[:160]
    if status in (401, 403):
        return AuthError(f"OpenRouter auth failed ({status}): {msg}")
    if status == 429:
        return RateLimitError(f"OpenRouter rate limit ({status})")
    if status == 404:
        return ModelUnavailableError(f"OpenRouter model not found ({status}): {msg}")
    return OutageError(f"OpenRouter HTTP {status}: {msg}")


def _classify_http(exc: Exception) -> ProviderError:
    name = type(exc).__name__
    msg = str(exc)[:160]
    if "HTTPError" in name:
        code = getattr(exc, "code", 0)
        try:
            data = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:
            data = {"error": msg}
        return _classify_status(code, data)
    if "URLError" in name or "Timeout" in name or "timeout" in msg.lower():
        return NetworkError(f"OpenRouter unreachable: {msg}")
    return OutageError(f"{name}: {msg}")
