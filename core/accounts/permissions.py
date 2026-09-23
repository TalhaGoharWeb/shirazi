"""core/accounts/permissions.py — per-user permission policies (Phase 8).

Layers per-user policies over the global engine (core/permissions.py):

    effective_level(user_id, tool)  → Level for this user+tool
    set_override(admin_id, user_id, tool, level)   admin-only
    clear_override(admin_id, user_id, tool)        admin-only
    can_use_privileged(user_id)                   role + developer_mode

Rules:
- A per-user override in the accounts db wins over the global default.
- Roles: admin follows the global engine (PRIVILEGED needs developer_mode
  AND the human banner). standard users can NEVER use PRIVILEGED tools —
  the override layer denies them even when developer_mode is on, because a
  non-owner account must not flip the machine into developer mode's blast
  radius. (developer_mode itself is a machine-owner setting in
  config/api_keys.json, not a per-user flag.)
- Unknown tools stay fail-closed (USER_CONFIRMATION) via the global engine.
"""

from __future__ import annotations

from typing import Optional

import core.permissions as _global
from core.permissions import Level
from .store import AccountStore


def effective_level(store: AccountStore, user_id: str,
                    tool_name: str) -> Level:
    """The permission level for this user+tool: per-user override first,
    then the global default. standard-role users get PRIVILEGED denied."""
    override = store.get_permission(str(user_id), str(tool_name))
    if override:
        try:
            level = Level(override)
        except ValueError:
            level = _global.default_level_for(tool_name)
    else:
        level = _global.default_level_for(tool_name)
    if level is Level.PRIVILEGED and not _admin_privileged_ok(store, user_id):
        # A standard user may not hold PRIVILEGED, even by override.
        return Level.USER_CONFIRMATION
    return level


def _admin_privileged_ok(store: AccountStore, user_id: str) -> bool:
    user = store.get_user(str(user_id))
    return bool(user is not None and user.is_admin())


def check(store: AccountStore, user_id: str, tool_name: str,
          parameters: Optional[dict] = None):
    """Per-user version of core.permissions.check(). Returns a CheckResult.

    The global check() runs first (developer_mode/safe_mode flags,
    sensitive sub-actions); then the per-user policy tightens it:
    - a per-user USER_CONFIRMATION/PRIVILEGED override can only *raise*
      the level, never lower a global USER_CONFIRMATION to SAFE.
    - standard users: PRIVILEGED → denied.
    """
    base = _global.check(tool_name, parameters)
    user_level = effective_level(store, user_id, tool_name)
    rank = {Level.SAFE: 0, Level.READ_ONLY: 1,
            Level.USER_CONFIRMATION: 2, Level.PRIVILEGED: 3}
    if rank[user_level] < rank[base.level]:
        # Per-user policy may not loosen the global engine.
        return base
    if user_level is Level.PRIVILEGED and not _admin_privileged_ok(store, user_id):
        return _global.CheckResult(False, False, Level.PRIVILEGED,
                                   f"user '{user_id}' is not an admin — "
                                   f"'{tool_name}' (PRIVILEGED) denied.")
    if rank[user_level] > rank[base.level]:
        needs_confirm = user_level in (Level.USER_CONFIRMATION, Level.PRIVILEGED)
        return _global.CheckResult(True, needs_confirm, user_level,
                                   f"per-user policy: '{tool_name}' is "
                                   f"{user_level.value} for this user.")
    return base


def set_override(store: AccountStore, admin_id: str, user_id: str,
                 tool: str, level: str) -> str:
    """Set a per-user tool level. Admin-only. Returns the stored level."""
    _require_admin(store, admin_id)
    level = str(level or "").upper()
    if level not in {l.value for l in Level}:
        raise ValueError(f"unknown permission level '{level}'")
    if level == Level.PRIVILEGED.value:
        target = store.get_user(str(user_id))
        if target is None or not target.is_admin():
            raise PermissionError(
                "PRIVILEGED can only be granted to admin accounts")
    store.set_permission(str(user_id), str(tool), level)
    return level


def clear_override(store: AccountStore, admin_id: str, user_id: str,
                   tool: str) -> bool:
    _require_admin(store, admin_id)
    return store.clear_permission(str(user_id), str(tool))


def list_overrides(store: AccountStore, user_id: str) -> dict[str, str]:
    return store.list_permissions(str(user_id))


def effective_policy(store: AccountStore, user_id: str) -> dict[str, str]:
    """Every known tool → effective level for this user (for /api/v1)."""
    tools = sorted(set(_global.known_tools())
                   | set(store.list_permissions(str(user_id))))
    return {t: effective_level(store, user_id, t).value for t in tools}


def _require_admin(store: AccountStore, actor_id: str) -> None:
    actor = store.get_user(actor_id)
    if actor is None or not actor.is_admin():
        raise PermissionError("permission management requires an admin account")
