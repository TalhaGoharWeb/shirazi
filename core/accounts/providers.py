"""core/accounts/providers.py — per-user AI provider management (Phase 8).

Extends config/providers.json into a per-user model:

    effective_chain(user_id)   ordered provider keys for this user
    provider_status(user_id)   per-provider: tier, enabled, available,
                               key_configured (bool — NEVER the key)
    set_enabled(user_id, name, enabled, actor)   admin-gated, FREE-FIRST
    set_primary(user_id, name, actor)
    set_chain(user_id, [names], actor)
    set_key(user_id, provider, value)  → encrypted SecretStore (namespaced)
    has_key(user_id, provider) -> bool (env or store; value never exposed)

FREE-FIRST (hard rule, enforced in code):
- "Local (Free)" and "Free API Tier" providers are the defaults. The shipped
  global chain is gemini → openrouter → ollama — all free.
- A provider whose tier is "Optional Paid Provider" can only be *enabled*
  for a user with paid_opt_in=True, and enabling one requires an explicit
  opt-in call (opt_in_paid) — paid is never assumed, never a default, and
  the "groq" reserved slot (no registered provider class) can never be
  enabled at all until an implementation registers.
- API keys are NEVER stored in the accounts db or in provider configs —
  AccountStore.set_provider_config() refuses key-like fields, and keys go
  to the encrypted SecretStore namespaced per user.

Key resolution order (core/accounts/secrets.resolve_key):
    env var (key_env, e.g. GEMINI_API_KEY) → SecretStore
    (user-namespaced, then global) → legacy config/api_keys.json plaintext
    (compat; migrated on first boot by main.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .models import ProviderSummary, Role
from .store import AccountStore

_BASE = Path(__file__).resolve().parent.parent.parent
_PROVIDERS_FILE = _BASE / "config" / "providers.json"

_FREE_TIERS = ("Local (Free)", "Free API Tier")
_PAID_TIER = "Optional Paid Provider"


def load_global_config() -> dict:
    try:
        return json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"chain": [], "providers": {}}


def _registered_provider_keys() -> set[str]:
    """Provider keys with a real implementation class registered.

    Importing the registry triggers the built-in registrations; function-
    level import keeps core.accounts importable without pulling the whole
    provider stack (avoids import cycles with core.gemini)."""
    try:
        from core.providers import registry  # noqa: F401  (registers classes)
        from core.providers.base import _PROVIDER_CLASSES
        return set(_PROVIDER_CLASSES.keys())
    except Exception:
        return set()


def global_chain() -> list[str]:
    cfg = load_global_config()
    chain = [k for k in (cfg.get("chain") or []) if isinstance(k, str)]
    if chain:
        return chain
    return [cfg.get("default_provider", "gemini"), "ollama"]


def effective_chain(store: AccountStore, user_id: str) -> list[str]:
    """This user's chain: per-user override when set and valid, else global."""
    cfgs = store.list_provider_configs(str(user_id))
    for provider, cfg in cfgs.items():
        chain = cfg.get("chain")
        if isinstance(chain, list) and chain:
            known = set(load_global_config().get("providers", {}))
            cleaned = [c for c in chain if c in known]
            if cleaned:
                return cleaned
    return global_chain()


def provider_status(store: AccountStore, user_id: str) -> list[ProviderSummary]:
    """Per-provider status for the user. Key presence is a bool — the key
    value is NEVER included (it must never reach the phone frontend)."""
    from .secrets import resolve_key  # local import: no cycles at module load
    cfg = load_global_config()
    providers = cfg.get("providers", {})
    user_cfgs = store.list_provider_configs(str(user_id))
    chain = effective_chain(store, user_id)
    registered = _registered_provider_keys()
    out = []
    for name, section in providers.items():
        ucfg = user_cfgs.get(name, {})
        enabled = ucfg.get("enabled", section.get("enabled", True))
        key_config = section.get("key_config", "")
        key_env = section.get("key_env", "")
        key_present = bool(resolve_key(key_config, key_env,
                                       user_id=str(user_id))) if key_config else True
        # availability: registered implementation + (keyless or key present)
        if name not in registered:
            available, reason = False, "no provider implementation registered"
        elif not enabled:
            available, reason = False, "disabled"
        elif key_config and not key_present:
            available, reason = False, "no API key configured"
        else:
            available, reason = True, "ok"
        out.append(ProviderSummary(
            name=name, tier=section.get("tier", "?"),
            enabled=bool(enabled), available=available, reason=reason,
            key_configured=key_present,
            capabilities=list(section.get("capabilities", []))))
    # chain order first, then the rest
    order = {n: i for i, n in enumerate(chain)}
    out.sort(key=lambda s: order.get(s.name, 999))
    return out


def _require_admin(store: AccountStore, actor_id: str) -> None:
    actor = store.get_user(actor_id)
    if actor is None or not actor.is_admin():
        raise PermissionError("provider management requires an admin account")


def set_enabled(store: AccountStore, user_id: str, provider: str,
                enabled: bool, *, actor_id: str) -> dict:
    """Enable/disable a provider for a user. Admin-gated. FREE-FIRST:
    enabling an 'Optional Paid Provider' tier requires the user's explicit
    paid_opt_in, and a provider with no registered implementation can never
    be enabled (reserved slots stay reserved)."""
    _require_admin(store, actor_id)
    cfg = load_global_config()
    section = cfg.get("providers", {}).get(provider)
    if section is None:
        raise ValueError(f"unknown provider '{provider}'")
    if enabled:
        tier = section.get("tier", "")
        if tier == _PAID_TIER:
            user = store.get_user(user_id)
            if user is None or not user.paid_opt_in:
                raise PermissionError(
                    f"FREE-FIRST: '{provider}' is an Optional Paid Provider — "
                    "it cannot be enabled until the user explicitly opts in "
                    "to paid providers (paid_opt_in). Free tiers stay default.")
        if provider not in _registered_provider_keys():
            raise ValueError(
                f"'{provider}' has no registered provider implementation — "
                "it cannot be enabled (reserved slot).")
    current = store.get_provider_config(str(user_id), provider)
    current["enabled"] = bool(enabled)
    store.set_provider_config(str(user_id), provider, current)
    return {"provider": provider, "enabled": bool(enabled)}


def set_primary(store: AccountStore, user_id: str, provider: str, *,
                actor_id: str) -> list[str]:
    """Put `provider` first in the user's chain. Admin-gated."""
    _require_admin(store, actor_id)
    known = set(load_global_config().get("providers", {}))
    if provider not in known:
        raise ValueError(f"unknown provider '{provider}'")
    chain = effective_chain(store, user_id)
    chain = [provider] + [c for c in chain if c != provider]
    return set_chain(store, user_id, chain, actor_id=actor_id)


def set_chain(store: AccountStore, user_id: str, chain: list[str], *,
              actor_id: str) -> list[str]:
    """Replace the user's provider chain. Admin-gated. FREE-FIRST: a chain
    may not *drop* every free tier — at least one Local/Free rung must
    remain, so the assistant never ends up paid-only by misconfiguration."""
    _require_admin(store, actor_id)
    cfg = load_global_config()
    providers = cfg.get("providers", {})
    cleaned = [c for c in chain if c in providers]
    if not cleaned:
        raise ValueError("chain must contain at least one known provider")
    free_left = [c for c in cleaned
                 if providers[c].get("tier") in _FREE_TIERS]
    if not free_left:
        raise ValueError(
            "FREE-FIRST: refusing a chain with no free tier — keep at least "
            "one 'Local (Free)' or 'Free API Tier' provider in the chain.")
    current = store.get_provider_config(str(user_id), "__chain__")
    current["chain"] = cleaned
    store.set_provider_config(str(user_id), "__chain__", current)
    return cleaned


def opt_in_paid(store: AccountStore, user_id: str, *, actor_id: str,
                opt_in: bool = True) -> bool:
    """Explicit paid opt-in/out. Admin-gated. This is the ONLY path that
    allows paid providers — never a default, never implied."""
    _require_admin(store, actor_id)
    user = store.update_user(str(user_id), paid_opt_in=bool(opt_in))
    return bool(user and user.paid_opt_in)


def set_key(store: AccountStore, user_id: str, provider: str,
            value: str, *, actor_id: str) -> bool:
    """Store a provider API key in the encrypted SecretStore, namespaced per
    user. Admin-gated. Never logged, never returned."""
    _require_admin(store, actor_id)
    from .secrets import SecretStore
    cfg = load_global_config()
    section = cfg.get("providers", {}).get(provider)
    if section is None:
        raise ValueError(f"unknown provider '{provider}'")
    key_config = section.get("key_config") or f"{provider}_api_key"
    SecretStore().set(f"user:{user_id}:provider:{provider}:{key_config}",
                      value)
    return True


def delete_key(store: AccountStore, user_id: str, provider: str, *,
               actor_id: str) -> bool:
    _require_admin(store, actor_id)
    from .secrets import SecretStore
    cfg = load_global_config()
    section = cfg.get("providers", {}).get(provider)
    if section is None:
        raise ValueError(f"unknown provider '{provider}'")
    key_config = section.get("key_config") or f"{provider}_api_key"
    return SecretStore().delete(
        f"user:{user_id}:provider:{provider}:{key_config}")
