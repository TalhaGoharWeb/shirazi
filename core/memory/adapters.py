"""core/memory/adapters.py — bridge the legacy JSON memory into the layers (Phase 4).

The Phase 1–3 app keeps long-term memory in memory/long_term.json via
memory/memory_manager.py (categories: identity, preferences, projects,
relationships, wishes, notes + session summaries). That module is sound and
stays the writer. This adapter:

  * mirrors the legacy categories into the new "user" layer (read-only copy,
    so new code has one place to look);
  * moves session summaries into the "session" layer on demand;
  * keeps delete in sync: deleting a user-layer key also removes it from the
    legacy store, so the user-facing delete in the new overlay actually
    deletes.

One-way sync is deliberate: the legacy store remains the source of truth
until a phase migrates it fully. See docs/PHASE4.md.
"""

from __future__ import annotations

from typing import Any, Optional

from .store import MemoryStore

_LEGACY_CATEGORIES = ("identity", "preferences", "projects",
                      "relationships", "wishes", "notes")


def _legacy():
    from memory import memory_manager as mm
    return mm


def sync_legacy_to_user_layer(store: Optional[MemoryStore] = None) -> int:
    """Copy legacy memory_manager entries into the user layer. Returns the
    number of entries mirrored. Safe to call repeatedly (upserts)."""
    store = store or MemoryStore()
    mm = _legacy()
    try:
        memory = mm.load_memory()
    except Exception:
        return 0
    count = 0
    for category in _LEGACY_CATEGORIES:
        entries = (memory or {}).get(category) or {}
        if not isinstance(entries, dict):
            continue
        for key, entry in entries.items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            store.remember("user", f"{category}/{key}", value,
                           metadata={"source": "legacy", "category": category})
            count += 1
    return count


def push_session_summary(store: Optional[MemoryStore] = None,
                         summary: str = "", language: str = "") -> None:
    """Record a session summary in the session layer (in addition to the
    legacy pop_last_session flow, which is unchanged)."""
    store = store or MemoryStore()
    if summary:
        import time
        store.remember("session",
                       f"summary/{time.strftime('%Y-%m-%dT%H:%M:%S')}",
                       summary, metadata={"language": language})


def delete_user_memory(key: str, store: Optional[MemoryStore] = None) -> bool:
    """Delete a user-layer key from BOTH stores. Returns True if the new
    layer had it (legacy deletion is best-effort and also attempted)."""
    store = store or MemoryStore()
    removed = store.delete("user", key)
    # Also try the legacy store: keys are mirrored as "category/key".
    if "/" in key:
        category, _, subkey = key.partition("/")
        try:
            _legacy().forget(subkey, category=category)
        except Exception:
            pass
    return removed


def user_memory_for_prompt(store: Optional[MemoryStore] = None,
                           limit: int = 60) -> str:
    """The user layer rendered for the system prompt. Falls back to the
    legacy formatter when the layer is empty (pre-migration installs)."""
    store = store or MemoryStore()
    entries = store.list("user", limit=limit)
    if not entries:
        try:
            return _legacy().format_memory_for_prompt(_legacy().load_memory())
        except Exception:
            return ""
    lines = []
    for e in entries:
        lines.append(f"- {e['key']}: {e['value']}")
    return "USER MEMORY (long-term preferences):\n" + "\n".join(lines)
