"""core/accounts/store.py — multi-user-capable SQLite account store (Phase 8).

One file: config/shirazi_accounts.db (git-ignored — personal data, never
committed). Schema is created on first use; a missing/corrupt db is rebuilt,
never fatal — the single-user desktop flow must keep working.

Tables:
    users             profile: role, voice, language, avatar, prefs
    prefs             generic per-user key/value preferences
    provider_configs  per-user provider chain/enable config (NO keys —
                      keys live in the encrypted SecretStore)
    permissions       per-user tool-level overrides over core.permissions
    sessions          bearer/device/api tokens (SHA-256 hashes, never raw)
    devices           registered devices (desktop/phone/tablet)
    usage             per-user/per-provider daily counters
    conversations     per-user conversation history (storage only; no
                      summariser wired yet — see docs/SAAS.md)
    automation_rules  stored automation rules (storage only; the rule
                      *engine* is design-only — see docs/SAAS.md)

Thread-safe via RLock. Raw tokens and secrets are NEVER stored here —
sessions.py stores only SHA-256 hashes; API keys live in
core/accounts/secrets.py (env → OS keyring → encrypted file).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from .models import AutomationRule, Role, User

_BASE = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _BASE / "config" / "shirazi_accounts.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    role        TEXT NOT NULL DEFAULT 'standard',
    voice       TEXT NOT NULL DEFAULT 'Charon',
    language    TEXT NOT NULL DEFAULT 'en',
    avatar      TEXT NOT NULL DEFAULT 'reactor',
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL DEFAULT 0,
    paid_opt_in INTEGER NOT NULL DEFAULT 0,
    extra       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS prefs (
    user_id TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS provider_configs (
    user_id  TEXT NOT NULL,
    provider TEXT NOT NULL,
    config   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (user_id, provider)
);
CREATE TABLE IF NOT EXISTS permissions (
    user_id TEXT NOT NULL,
    tool    TEXT NOT NULL,
    level   TEXT NOT NULL,
    PRIMARY KEY (user_id, tool)
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    device_id   TEXT,
    kind        TEXT NOT NULL DEFAULT 'bearer',
    label       TEXT NOT NULL DEFAULT '',
    session_key TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    last_seen   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS devices (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    token_hash TEXT UNIQUE,
    name       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'phone',
    session_key TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL DEFAULT 0,
    revoked    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id);
CREATE TABLE IF NOT EXISTS usage (
    user_id  TEXT NOT NULL,
    provider TEXT NOT NULL,
    kind     TEXT NOT NULL,
    day      TEXT NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, provider, kind, day)
);
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at   REAL NOT NULL DEFAULT 0,
    summary    TEXT NOT NULL DEFAULT '',
    messages   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, started_at);
CREATE TABLE IF NOT EXISTS automation_rules (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    name       TEXT NOT NULL,
    trigger    TEXT NOT NULL DEFAULT '{}',
    action     TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_user ON automation_rules(user_id);
"""


class AccountStore:
    """SQLite-backed account storage. Constructing it never raises for a
    missing db — schema init failures degrade to a clear warning, because
    this layer must never break the single-user desktop boot."""

    def __init__(self, db_path: Optional[Path] = None):
        self._path = Path(db_path) if db_path else _DB_PATH
        self._lock = threading.RLock()
        self._available = False
        self._init()

    @property
    def available(self) -> bool:
        return self._available

    @property
    def path(self) -> Path:
        return self._path

    # ── Setup ────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
            self._available = True
        except Exception as e:
            print(f"[SHIRAZI] ⚠️ account store unavailable ({e}) — "
                  f"single-user in-memory mode")

    def _require(self) -> None:
        if not self._available:
            raise RuntimeError("account store unavailable")

    # ── Users ────────────────────────────────────────────────────────────
    def ensure_user(self, user_id: str, *, name: str = "",
                    role: str = Role.STANDARD, **kwargs) -> User:
        """Get or create a user. Safe to call on every boot (idempotent)."""
        user_id = str(user_id or "").strip() or "local"
        if role not in Role.ALL:
            raise ValueError(f"unknown role '{role}'")
        with self._lock:
            self._require()
            now = time.time()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO users(id, name, role, voice, language,"
                        " avatar, created_at, last_seen, paid_opt_in, extra)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (user_id, name, role,
                         kwargs.get("voice", "Charon"),
                         kwargs.get("language", "en"),
                         kwargs.get("avatar", "reactor"),
                         now, now,
                         1 if kwargs.get("paid_opt_in") else 0,
                         json.dumps(kwargs.get("extra", {}),
                                    ensure_ascii=False)))
                    row = conn.execute(
                        "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            return self._row_to_user(row)

    def get_user(self, user_id: str) -> Optional[User]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM users WHERE id=?",
                    (str(user_id),)).fetchone()
        return self._row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM users ORDER BY created_at").fetchall()
        return [self._row_to_user(r) for r in rows]

    def update_user(self, user_id: str, **fields) -> Optional[User]:
        allowed = {"name", "role", "voice", "language", "avatar",
                   "paid_opt_in", "extra", "last_seen"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "role" in updates and updates["role"] not in Role.ALL:
            raise ValueError(f"unknown role '{updates['role']}'")
        if "extra" in updates:
            updates["extra"] = json.dumps(updates["extra"] or {},
                                         ensure_ascii=False)
        if "paid_opt_in" in updates:
            updates["paid_opt_in"] = 1 if updates["paid_opt_in"] else 0
        if not updates:
            return self.get_user(user_id)
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "UPDATE users SET {} WHERE id=?".format(
                        ", ".join(f"{k}=?" for k in updates)),
                    (*updates.values(), str(user_id)))
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> bool:
        """Delete a user and all their rows (sessions, devices, usage,
        prefs, provider configs, permissions, conversations, rules)."""
        if str(user_id) == "local":
            raise ValueError("the local single-user account cannot be deleted")
        with self._lock:
            self._require()
            with self._connect() as conn:
                for table in ("sessions", "devices", "usage", "prefs",
                              "provider_configs", "permissions",
                              "conversations", "automation_rules"):
                    conn.execute(f"DELETE FROM {table} WHERE user_id=?",
                                 (str(user_id),))
                cur = conn.execute("DELETE FROM users WHERE id=?",
                                   (str(user_id),))
                return cur.rowcount > 0

    @staticmethod
    def _row_to_user(row) -> User:
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:
            extra = {}
        return User(
            id=row["id"], name=row["name"] or "", role=row["role"],
            voice=row["voice"], language=row["language"], avatar=row["avatar"],
            created_at=row["created_at"], last_seen=row["last_seen"],
            paid_opt_in=bool(row["paid_opt_in"]),
            extra=extra if isinstance(extra, dict) else {})

    # ── Prefs (generic per-user key/value) ───────────────────────────────
    def set_pref(self, user_id: str, key: str, value: Any) -> None:
        key = str(key or "").strip()
        if not key:
            raise ValueError("pref key must not be empty")
        blob = json.dumps(value, ensure_ascii=False)
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO prefs(user_id, key, value) VALUES(?,?,?)"
                    " ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                    (str(user_id), key, blob))

    def get_pref(self, user_id: str, key: str, default: Any = None) -> Any:
        with self._lock:
            self._require()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM prefs WHERE user_id=? AND key=?",
                    (str(user_id), str(key))).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def list_prefs(self, user_id: str) -> dict:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT key, value FROM prefs WHERE user_id=?",
                    (str(user_id),)).fetchall()
        out = {}
        for r in rows:
            try:
                out[r["key"]] = json.loads(r["value"])
            except Exception:
                out[r["key"]] = r["value"]
        return out

    # ── Provider configs (per user; NEVER keys) ──────────────────────────
    def set_provider_config(self, user_id: str, provider: str,
                            config: dict) -> None:
        if not isinstance(config, dict):
            raise ValueError("provider config must be a dict")
        for banned in ("api_key", "key", "secret", "token"):
            if banned in config:
                raise ValueError(
                    f"refusing to store '{banned}' in provider config — "
                    "keys live in the encrypted SecretStore, never here")
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO provider_configs(user_id, provider, config)"
                    " VALUES(?,?,?)"
                    " ON CONFLICT(user_id, provider) DO UPDATE"
                    " SET config=excluded.config",
                    (str(user_id), str(provider),
                     json.dumps(config, ensure_ascii=False)))

    def get_provider_config(self, user_id: str, provider: str) -> dict:
        with self._lock:
            self._require()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT config FROM provider_configs"
                    " WHERE user_id=? AND provider=?",
                    (str(user_id), str(provider))).fetchone()
        if row is None:
            return {}
        try:
            cfg = json.loads(row["config"])
            return cfg if isinstance(cfg, dict) else {}
        except Exception:
            return {}

    def list_provider_configs(self, user_id: str) -> dict[str, dict]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT provider, config FROM provider_configs"
                    " WHERE user_id=?", (str(user_id),)).fetchall()
        out = {}
        for r in rows:
            try:
                out[r["provider"]] = json.loads(r["config"])
            except Exception:
                out[r["provider"]] = {}
        return out

    # ── Per-user permission overrides ────────────────────────────────────
    def set_permission(self, user_id: str, tool: str, level: str) -> None:
        from core.permissions import Level
        level = str(level or "").upper()
        if level not in {l.value for l in Level}:
            raise ValueError(f"unknown permission level '{level}'")
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO permissions(user_id, tool, level)"
                    " VALUES(?,?,?)"
                    " ON CONFLICT(user_id, tool) DO UPDATE"
                    " SET level=excluded.level",
                    (str(user_id), str(tool), level))

    def get_permission(self, user_id: str, tool: str) -> Optional[str]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT level FROM permissions WHERE user_id=? AND tool=?",
                    (str(user_id), str(tool))).fetchone()
        return row["level"] if row else None

    def clear_permission(self, user_id: str, tool: str) -> bool:
        with self._lock:
            self._require()
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM permissions WHERE user_id=? AND tool=?",
                    (str(user_id), str(tool)))
                return cur.rowcount > 0

    def list_permissions(self, user_id: str) -> dict[str, str]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT tool, level FROM permissions WHERE user_id=?",
                    (str(user_id),)).fetchall()
        return {r["tool"]: r["level"] for r in rows}

    # ── Conversation history (storage; no summariser wired yet) ──────────
    def record_conversation(self, user_id: str, messages: list,
                            summary: str = "") -> str:
        """Store one conversation. `messages` = [{role, text, ts}]."""
        cid = uuid.uuid4().hex[:16]
        now = time.time()
        started = messages[0].get("ts", now) if messages else now
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO conversations(id, user_id, started_at,"
                    " ended_at, summary, messages) VALUES(?,?,?,?,?,?)",
                    (cid, str(user_id), started, now, summary,
                     json.dumps(messages or [], ensure_ascii=False)))
        return cid

    def list_conversations(self, user_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT id, started_at, ended_at, summary, messages"
                    " FROM conversations WHERE user_id=?"
                    " ORDER BY started_at DESC LIMIT ?",
                    (str(user_id), int(limit))).fetchall()
        out = []
        for r in rows:
            try:
                msgs = json.loads(r["messages"])
            except Exception:
                msgs = []
            out.append({"id": r["id"], "started_at": r["started_at"],
                        "ended_at": r["ended_at"], "summary": r["summary"],
                        "messages": msgs})
        return out

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        with self._lock:
            self._require()
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM conversations WHERE id=? AND user_id=?",
                    (str(conversation_id), str(user_id)))
                return cur.rowcount > 0

    # ── Automation rules (storage; the rule *engine* is design-only) ─────
    def add_rule(self, user_id: str, name: str, trigger: dict,
                 action: dict, enabled: bool = True) -> AutomationRule:
        rid = uuid.uuid4().hex[:16]
        rule = AutomationRule(id=rid, user_id=str(user_id), name=name,
                              trigger=trigger or {}, action=action or {},
                              enabled=enabled, created_at=time.time())
        with self._lock:
            self._require()
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO automation_rules(id, user_id, name, trigger,"
                    " action, enabled, created_at) VALUES(?,?,?,?,?,?,?)",
                    (rule.id, rule.user_id, rule.name,
                     json.dumps(rule.trigger, ensure_ascii=False),
                     json.dumps(rule.action, ensure_ascii=False),
                     1 if rule.enabled else 0, rule.created_at))
        return rule

    def list_rules(self, user_id: str) -> list[AutomationRule]:
        with self._lock:
            self._require()
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM automation_rules WHERE user_id=?"
                    " ORDER BY created_at", (str(user_id),)).fetchall()
        return [self._row_to_rule(r) for r in rows]

    def set_rule_enabled(self, user_id: str, rule_id: str,
                         enabled: bool) -> bool:
        with self._lock:
            self._require()
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE automation_rules SET enabled=? WHERE id=? AND user_id=?",
                    (1 if enabled else 0, str(rule_id), str(user_id)))
                return cur.rowcount > 0

    def delete_rule(self, user_id: str, rule_id: str) -> bool:
        with self._lock:
            self._require()
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM automation_rules WHERE id=? AND user_id=?",
                    (str(rule_id), str(user_id)))
                return cur.rowcount > 0

    @staticmethod
    def _row_to_rule(row) -> AutomationRule:
        def _j(raw, default):
            try:
                v = json.loads(raw or "")
                return v if isinstance(v, dict) else default
            except Exception:
                return default
        return AutomationRule(
            id=row["id"], user_id=row["user_id"], name=row["name"],
            trigger=_j(row["trigger"], {}), action=_j(row["action"], {}),
            enabled=bool(row["enabled"]), created_at=row["created_at"])


def ensure_local_user(store: AccountStore) -> str:
    """The single-user desktop account. Idempotent: creates it once as an
    admin (this machine's owner), leaves an existing one untouched.

    Returns the user id ("local"). The dashboard's existing bearer-token
    auth becomes the single-user instance of the general session mechanism
    by issuing sessions for this user."""
    user = store.ensure_user("local", name="Local user", role=Role.ADMIN)
    store.update_user("local", last_seen=time.time())
    return user.id
