"""core/accounts/devices.py — device registration facade (Phase 8).

Thin convenience layer over SessionManager's device registry:

    register(user_id, name, kind) -> raw device token (shown once)
    list(user_id)                 -> [DeviceInfo] (no token material)
    revoke(user_id, device_id_or_token) -> bool
    revoke_all(user_id) -> count

Device kinds: "desktop" | "phone" | "tablet" | "other".

Legacy compat (docs/LEGACY_COMPAT.md §4): the phone UI stores
`shirazi_device_token` in localStorage, falling back to the Mark-LIV-era
`jarvis_device_token`. Server-side validation is by token *value*, so both
keys keep working: dashboard/server.py checks its legacy in-memory
`_device_sessions` map first, then this registry. Registering a device token
here does not disturb the legacy fallback — keep both paths working.
"""

from __future__ import annotations

import secrets
from typing import Optional

from .models import DeviceInfo
from .sessions import SessionManager

KINDS = ("desktop", "phone", "tablet", "other")


def _check_kind(kind: str) -> str:
    kind = (kind or "phone").strip().lower()
    return kind if kind in KINDS else "other"


def register(manager: SessionManager, user_id: str, *, name: str = "",
             kind: str = "phone", session_key: str = "") -> dict:
    """Register a device and return {"device_id", "device_token"}.

    The raw device token is returned ONCE — it is stored hashed and can
    never be recovered. Hand it to the device being paired."""
    kind = _check_kind(kind)
    if not session_key:
        # Phase 9 hardening: /api/device-login exchanges a device token for
        # a bearer only when the device record carries a non-empty channel
        # key. The QR flow passes its pairing key explicitly; api_v1 callers
        # don't — mint one here so the documented "register, then pair"
        # flow actually works (the raw device token stays the credential;
        # this key only seeds the phone<->desktop AES channel).
        session_key = secrets.token_urlsafe(32)
    token = manager.register_device(str(user_id), name=name or kind,
                                    kind=kind, session_key=session_key)
    dev = manager.find_device(token)
    return {"device_id": dev.id if dev else "",
            "device_token": token, "kind": kind}


def list_devices(manager: SessionManager, user_id: str,
                 include_revoked: bool = False) -> list[dict]:
    """Devices for API display. Never includes token material."""
    out = []
    for d in manager.list_devices(str(user_id),
                                  include_revoked=include_revoked):
        out.append({"id": d.id, "name": d.name, "kind": d.kind,
                    "created_at": d.created_at, "last_seen": d.last_seen,
                    "revoked": d.revoked})
    return out


def revoke(manager: SessionManager, device_id_or_token: str) -> bool:
    return manager.revoke_device(device_id_or_token)


def revoke_all(manager: SessionManager, user_id: str) -> int:
    return manager.revoke_all_devices(str(user_id))
