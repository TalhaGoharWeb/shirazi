"""core/memory/ — layered memory (Phase 4).

Four layers, one SQLite store (`config/shirazi_memory.db`, git-ignored):

    session   — the current conversation: turns, summaries, transient state.
    user      — long-term preferences: name, language, likes, facts the user
                asked to remember. Adapters keep the legacy
                memory/memory_manager.py JSON store readable here.
    tool      — configured devices and tools: preferred mic/speaker, enabled
                plugins, per-tool settings the user chose.
    research  — saved research: web_search results the user kept, documents
                and notes worth keeping across sessions.

Every entry: (layer, key, value, updated_at, metadata). User-facing inspect
and delete: `inspect()` returns everything in plain dicts; `delete()` /
`wipe()` remove it. Nothing here is ever sent anywhere — the store is local
only.
"""

from .store import MemoryStore, LAYERS

__all__ = ["MemoryStore", "LAYERS"]
