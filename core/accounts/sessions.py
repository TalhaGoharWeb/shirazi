"""core/accounts/sessions.py — general session mechanism (Phase 8).

The dashboard's bearer-token auth is the *single-user instance* of this
mechanism: DashboardServer issues sessions for the "local" user and validates
tokens through SessionManager.validate() instead of a bare in-memory set.

Design:
- Raw tokens exist only transiently (returned once at creation/login).
  SQLite stores SHA-256(token) — a db dump never yields a usable token.
- Sessions have TTL + revocation. validate() checks both and touches
  last_seen.
- Device tokens are long-lived credentials bound to a registered device
  (phone/tablet/desktop). They mint short-lived bearer sessions via
  /api/device-login, exactly like the legacy `jarvis_device_token` flow —
  the legacy in-memory fallback is kept working alongside (see
  docs/LEGACY_COMPAT.md §4).
- adopt() registers an externally-minted token (e.g. the dashboard's
  secrets.token_urlsafe(32) bearers) so old code paths keep working while
  gaining expiry/revocation/persistence.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from typing import Optional

from .models import DeviceInfo, SessionInfo
from .store import AccountStore

DEFAULT_TTL_HOURS = 24.0       # bearer sessions
DEVICE_TTL_HOURS = 24.0 * 30   # adopted dashboard bearers: long-lived


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionManager:
    def __init__(self, store: AccountStore,
                 default_ttl_hours: float = DEFAULT_TTL_HOURS):
        self._store = store
        self._ttl = float(default_ttl_hours)
        self._lock = threading.RLock()

    # ── Bearer sessions ──────────────────────────────────────────────────
    def create(self, user_id: str, *, kind: str = "bearer",
               ttl_hours: Optional[float] = None,
               device_id: Optional[str] = None, label: str = "",
               session_key: str = "") -> str:
        """Mint a new token. Returns the RAW token (shown once, never
        stored)."""
        token = secrets.token_urlsafe(32)
        self.adopt(user_id, token, kind=kind,
                   ttl_hours=self._ttl if ttl_hours is None else ttl_hours,
                   device_id=device_id, label=label, session_key=session_key)
        return token

    def adopt(self, user_id: str, token: str, *, kind: str = "bearer",
              ttl_hours: float = DEVICE_TTL_HOURS,
              device_id: Optional[str] = None, label: str = "",
              session_key: str = "") -> dict:
        """Register an externally-minted token (dashboard's existing
        token_urlsafe(32) bearers). Idempotent for the same token."""
        now = time.time()
        th = _hash(token)
        with self._lock, self._store._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(token_hash, user_id, device_id, kind,"
                " label, session_key, created_at, expires_at, revoked,"
                " last_seen) VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(token_hash) DO UPDATE SET"
                " expires_at=excluded.expires_at, revoked=0,"
                " session_key=excluded.session_key, label=excluded.label",
                (th, str(user_id), device_id, kind, label, session_key or "",
                 now, now + float(ttl_hours) * 3600, 0, now))
        return {"user_id": str(user_id), "kind": kind, "label": label,
                "expires_at": now + float(ttl_hours) * 3600}

    def validate(self, token: str) -> Optional[dict]:
        """Validate a token → session dict, or None. Checks revocation and
        expiry; touches last_seen. Never raises."""
        if not token:
            return None
        th = _hash(str(token))
        try:
            with self._lock, self._store._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM sessions WHERE token_hash=?", (th,)).fetchone()
                if row is None or row["revoked"]:
                    return None
                now = time.time()
                if row["expires_at"] and row["expires_at"] <= now:
                    return None
                conn.execute(
                    "UPDATE sessions SET last_seen=? WHERE token_hash=?",
                    (now, th))
        except Exception:
            return None
        return {"user_id": row["user_id"], "kind": row["kind"],
                "device_id": row["device_id"], "label": row["label"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "last_seen": now}

    def session_key_for(self, token: str) -> Optional[str]:
        """The AES session key bound to a token (dashboard /api/command
        `enc` payloads). None when unknown — the caller falls back to the
        legacy in-memory map."""
        info = self.validate(token)
        if not info:
            return None
        try:
            with self._lock, self._store._connect() as conn:
                row = conn.execute(
                    "SELECT session_key FROM sessions WHERE token_hash=?",
                    (_hash(str(token)),)).fetchone()
        except Exception:
            return None
        sk = row["session_key"] if row else ""
        return sk or None

    def expire(self, token: str) -> bool:
        """Force a token to expire now (logout)."""
        th = _hash(str(token))
        with self._lock, self._store._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET expires_at=? WHERE token_hash=?",
                (time.time(), th))
            return cur.rowcount > 0

    def revoke(self, token: str) -> bool:
        th = _hash(str(token))
        with self._lock, self._store._connect() as conn:
            cur = conn.execute(
                "UPDATE sessions SET revoked=1 WHERE token_hash=?", (th,))
            return cur.rowcount > 0

    def revoke_all(self, user_id: str, *,
                   except_token: Optional[str] = None) -> int:
        """Revoke every session of a user (optionally keeping one)."""
        with self._lock, self._store._connect() as conn:
            if except_token:
                cur = conn.execute(
                    "UPDATE sessions SET revoked=1 WHERE user_id=?"
                    " AND token_hash != ?",
                    (str(user_id), _hash(str(except_token))))
            else:
                cur = conn.execute(
                    "UPDATE sessions SET revoked=1 WHERE user_id=?",
                    (str(user_id),))
            return cur.rowcount

    def list_sessions(self, user_id: str,
                      include_revoked: bool = False) -> list[dict]:
        """Sessions for a user. Token hashes are truncated — raw tokens are
        never recoverable from here."""
        with self._lock, self._store._connect() as conn:
            q = ("SELECT * FROM sessions WHERE user_id=?"
                 + ("" if include_revoked else " AND revoked=0")
                 + " ORDER BY last_seen DESC")
            rows = conn.execute(q, (str(user_id),)).fetchall()
        return [{
            "token_prefix": r["token_hash"][:12], "kind": r["kind"],
            "device_id": r["device_id"], "label": r["label"],
            "created_at": r["created_at"], "expires_at": r["expires_at"],
            "last_seen": r["last_seen"], "revoked": bool(r["revoked"]),
        } for r in rows]

    def purge_expired(self) -> int:
        """Delete expired sessions. Returns the count removed."""
        with self._lock, self._store._connect() as conn:
            cur = conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (time.time(),))
            return cur.rowcount

    # ── Devices ──────────────────────────────────────────────────────────
    def register_device(self, user_id: str, *, name: str = "",
                        kind: str = "phone", session_key: str = "") -> str:
        """Register a device; returns the RAW device token (shown once).
        A device token mints bearer sessions via device_login()."""
        dev_token = secrets.token_urlsafe(32)
        self.register_device_token(str(user_id), dev_token, name=name,
                                   kind=kind, session_key=session_key)
        return dev_token

    def register_device_token(self, user_id: str, device_token: str, *,
                              name: str = "", kind: str = "phone",
                              session_key: str = "") -> str:
        """Register an externally-minted device token (the dashboard's
        device-login flow). Returns the device id. Idempotent."""
        dev_id = "dev_" + uuid.uuid4().hex[:12]
        now = time.time()
        th = _hash(device_token)
        with self._lock, self._store._connect() as conn:
            row = conn.execute(
                "SELECT id FROM devices WHERE token_hash=?", (th,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE devices SET session_key=?, revoked=0,"
                    " last_seen=? WHERE token_hash=?",
                    (session_key or "", now, th))
                return row["id"]
            conn.execute(
                "INSERT INTO devices(id, user_id, token_hash, name, kind,"
                " session_key, created_at, last_seen, revoked)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (dev_id, str(user_id), th, name or kind, kind,
                 session_key or "", now, now, 0))
        return dev_id

    def device_login(self, device_token: str, *,
                     ttl_hours: float = DEFAULT_TTL_HOURS,
                     label: str = "device-login") -> Optional[str]:
        """Mint a fresh bearer token for a registered, non-revoked device.
        Returns the raw bearer token, or None."""
        dev = self.find_device(device_token)
        if dev is None:
            return None
        bearer = self.create(dev.user_id, kind="bearer",
                             ttl_hours=ttl_hours, device_id=dev.id,
                             label=label, session_key=dev.session_key or "")
        self.touch_device(device_token)
        return bearer

    def find_device(self, device_token: str) -> Optional[DeviceInfo]:
        if not device_token:
            return None
        th = _hash(str(device_token))
        try:
            with self._lock, self._store._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM devices WHERE token_hash=? AND revoked=0",
                    (th,)).fetchone()
        except Exception:
            return None
        if row is None:
            return None
        d = DeviceInfo(id=row["id"], user_id=row["user_id"],
                       name=row["name"], kind=row["kind"],
                       created_at=row["created_at"],
                       last_seen=row["last_seen"], revoked=bool(row["revoked"]),
                       session_key=row["session_key"] or "")
        return d

    def device_session_key(self, device_token: str) -> Optional[str]:
        dev = self.find_device(device_token)
        return (dev.session_key or None) if dev else None

    def touch_device(self, device_token: str) -> None:
        try:
            with self._lock, self._store._connect() as conn:
                conn.execute(
                    "UPDATE devices SET last_seen=? WHERE token_hash=?",
                    (time.time(), _hash(str(device_token))))
        except Exception:
            pass

    def list_devices(self, user_id: str,
                     include_revoked: bool = False) -> list[DeviceInfo]:
        with self._lock, self._store._connect() as conn:
            q = ("SELECT * FROM devices WHERE user_id=?"
                 + ("" if include_revoked else " AND revoked=0")
                 + " ORDER BY last_seen DESC")
            rows = conn.execute(q, (str(user_id),)).fetchall()
        return [DeviceInfo(id=r["id"], user_id=r["user_id"], name=r["name"],
                           kind=r["kind"], created_at=r["created_at"],
                           last_seen=r["last_seen"],
                           revoked=bool(r["revoked"])) for r in rows]

    def revoke_device(self, device_id_or_token: str) -> bool:
        """Revoke a device by id or by raw device token. Also revokes the
        bearer sessions that were minted for that device."""
        ident = str(device_id_or_token)
        th = _hash(ident)
        with self._lock, self._store._connect() as conn:
            cur = conn.execute(
                "UPDATE devices SET revoked=1 WHERE id=? OR token_hash=?",
                (ident, th))
            if cur.rowcount:
                dev = conn.execute(
                    "SELECT id FROM devices WHERE id=? OR token_hash=?",
                    (ident, th)).fetchone()
                if dev:
                    conn.execute(
                        "UPDATE sessions SET revoked=1 WHERE device_id=?",
                        (dev["id"],))
            return cur.rowcount > 0

    def revoke_all_devices(self, user_id: str) -> int:
        with self._lock, self._store._connect() as conn:
            cur = conn.execute(
                "UPDATE devices SET revoked=1 WHERE user_id=?",
                (str(user_id),))
            return cur.rowcount
