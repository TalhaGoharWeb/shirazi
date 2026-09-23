"""core/memory/store.py — SQLite-backed layered memory store (Phase 4).

Thread-safe. One file: config/shirazi_memory.db (git-ignored — personal data,
never committed). Schema is created on first use; a missing/corrupt db is
rebuilt, never fatal.

User-facing inspect + delete:
    store.inspect()            -> {"session": [...], "user": [...], ...}
    store.delete(layer, key)    -> True/False
    store.wipe(layer)           -> count removed
    store.export_json()         -> everything, for backup
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_BASE = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _BASE / "config" / "shirazi_memory.db"

LAYERS = ("session", "user", "tool", "research")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    layer      TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    metadata   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (layer, key)
);
CREATE INDEX IF NOT EXISTS idx_memory_layer ON memory(layer);
"""


class MemoryStore:
    def __init__(self, db_path: Optional[Path] = None):
        self._path = Path(db_path) if db_path else _DB_PATH
        self._lock = threading.RLock()
        self._init()

    # ── Setup ────────────────────────────────────────────────────────────────
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as conn:
                    conn.executescript(_SCHEMA)
            except Exception as e:
                print(f"[SHIRAZI] ⚠️ memory store unavailable ({e}) — "
                      f"memory will be session-only")

    def _check_layer(self, layer: str) -> None:
        if layer not in LAYERS:
            raise ValueError(f"unknown memory layer '{layer}' "
                             f"(expected one of {LAYERS})")

    # ── Write ────────────────────────────────────────────────────────────────
    def remember(self, layer: str, key: str, value: Any,
                 metadata: Optional[dict] = None) -> None:
        """Store a value. `value` may be any JSON-serializable object."""
        self._check_layer(layer)
        key = str(key or "").strip()
        if not key:
            raise ValueError("memory key must not be empty")
        blob = json.dumps(value, ensure_ascii=False)
        meta = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO memory(layer, key, value, updated_at, metadata)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(layer, key) DO UPDATE SET"
                " value=excluded.value, updated_at=excluded.updated_at,"
                " metadata=excluded.metadata",
                (layer, key, blob, time.time(), meta))

    # ── Read ─────────────────────────────────────────────────────────────────
    def recall(self, layer: str, key: str, default: Any = None) -> Any:
        self._check_layer(layer)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM memory WHERE layer=? AND key=?",
                (layer, str(key))).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def search(self, layer: str, query: str, limit: int = 20) -> list[dict]:
        """Substring search over keys and values (JSON-aware)."""
        self._check_layer(layer)
        q = f"%{query}%" if query else "%"
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT layer, key, value, updated_at, metadata FROM memory"
                " WHERE layer=? AND (key LIKE ? OR value LIKE ?)"
                " ORDER BY updated_at DESC LIMIT ?",
                (layer, q, q, int(limit))).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def list(self, layer: str, limit: int = 200) -> list[dict]:
        self._check_layer(layer)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT layer, key, value, updated_at, metadata FROM memory"
                " WHERE layer=? ORDER BY updated_at DESC LIMIT ?",
                (layer, int(limit))).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Inspect / delete (user-facing) ───────────────────────────────────────
    def inspect(self) -> dict[str, list[dict]]:
        """Everything stored, by layer. Powers the memory overlay UI."""
        return {layer: self.list(layer) for layer in LAYERS}

    def delete(self, layer: str, key: str) -> bool:
        """Remove one entry. Returns True if something was removed."""
        self._check_layer(layer)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM memory WHERE layer=? AND key=?",
                (layer, str(key)))
            return cur.rowcount > 0

    def wipe(self, layer: str) -> int:
        """Remove every entry in a layer. Returns the count removed."""
        self._check_layer(layer)
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM memory WHERE layer=?", (layer,))
            return cur.rowcount

    def counts(self) -> dict[str, int]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT layer, COUNT(*) AS n FROM memory GROUP BY layer"
            ).fetchall()
        out = {layer: 0 for layer in LAYERS}
        for r in rows:
            if r["layer"] in out:
                out[r["layer"]] = r["n"]
        return out

    def export_json(self) -> dict:
        """Full backup as plain dicts (for export / migration)."""
        return self.inspect()

    # ── Internals ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        try:
            value = json.loads(row["value"])
        except Exception:
            value = row["value"]
        try:
            metadata = json.loads(row["metadata"])
        except Exception:
            metadata = {}
        return {
            "layer": row["layer"],
            "key": row["key"],
            "value": value,
            "updated_at": row["updated_at"],
            "metadata": metadata,
        }


# A process-wide default store, for the tools and UI that just need "memory".
_default: Optional[MemoryStore] = None
_default_lock = threading.Lock()


def default_store() -> MemoryStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = MemoryStore()
        return _default
