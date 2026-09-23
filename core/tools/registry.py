"""core/tools/registry.py — the central tool registry (Phase 4, adapter).

Build once at startup:

    from core.tools import build_registry
    registry = build_registry(
        inline=TOOL_DECLARATIONS,          # main.py's 8 inline tools
        actions=self._action_registry,     # ActionRegistry (16 actions)
        plugins=self._plugin_registry,     # PluginRegistry (N plugins)
    )

Every entry becomes a ToolSpec: name, category, permission level,
description, JSON schema, and where it came from ("inline" | "action" |
"plugin"). Permission levels default from core/permissions.py's mapping;
a config file (config/tool_permissions.json, optional) can override per
tool without code changes.

Fault-isolated by design, like the loaders it adapts: a bad declaration is
skipped and logged, never aborts the build.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.permissions import Level, default_level_for

_BASE = Path(__file__).resolve().parent.parent.parent
_OVERRIDES_PATH = _BASE / "config" / "tool_permissions.json"

# Categories every tool declares exactly one of.
CATEGORIES = (
    "computer", "browser", "files", "applications", "system",
    "media", "research", "memory", "communication", "utility",
)

# Best-known category per tool (adapter knowledge — no tool code changed).
_CATEGORY_HINTS: dict[str, str] = {
    # inline (main.py)
    "system_status": "system",
    "screen_process": "media",
    "close_camera": "media",
    "manage_monitor": "system",
    "shutdown_shirazi": "system",
    "shutdown_jarvis": "system",   # legacy alias — same tool
    "save_memory": "memory",
    "recall_memory": "memory",
    "undo": "utility",
    # actions
    "browser_control": "browser",
    "game_updater": "applications",
    "computer_settings": "system",
    "file_processor": "files",
    "file_controller": "files",
    "dev_agent": "computer",
    "code_helper": "computer",
    "computer_control": "computer",
    "desktop_control": "computer",
    "youtube_video": "research",
    "web_search": "research",
    "flight_finder": "research",
    "reminder": "utility",
    "send_message": "communication",
    "open_app": "applications",
    "screen_processor": "media",
    "weather_report": "research",
}


@dataclass
class ToolSpec:
    """One tool, fully described. What the permission engine and the system
    prompt both read."""
    name: str
    category: str                       # one of CATEGORIES
    permission: Level                   # SAFE | READ_ONLY | USER_CONFIRMATION | PRIVILEGED
    description: str = ""
    schema: dict = field(default_factory=dict)   # {"type": "object", "properties": {...}}
    source: str = "action"              # "inline" | "action" | "plugin"
    irreversible: bool = False          # needs undo or confirm regardless


class ToolRegistry:
    """Central, read-mostly registry. Thread-safe; built once, queried often."""

    def __init__(self):
        self._tools: dict[str, ToolSpec] = {}
        self._lock = threading.Lock()

    # ── Registration ──────────────────────────────────────────────────────────
    def register(self, spec: ToolSpec, *, override: bool = False) -> None:
        with self._lock:
            if spec.name in self._tools and not override:
                return  # first registration wins (inline < action < plugin order)
            self._tools[spec.name] = spec

    def unregister(self, name: str) -> None:
        with self._lock:
            self._tools.pop(name, None)

    # ── Queries ───────────────────────────────────────────────────────────────
    def get(self, name: str) -> Optional[ToolSpec]:
        with self._lock:
            return self._tools.get(name)

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._tools)

    def list_by_category(self, category: str) -> list[ToolSpec]:
        with self._lock:
            return [t for t in self._tools.values() if t.category == category]

    def list_by_permission(self, level: Level) -> list[ToolSpec]:
        with self._lock:
            return [t for t in self._tools.values() if t.permission == level]

    def describe_for_prompt(self) -> str:
        """Compact 'name — description' lines for the system prompt's
        capabilities block."""
        with self._lock:
            specs = sorted(self._tools.values(), key=lambda t: t.name)
        return "\n".join(f"- {s.name}: {s.description or '(no description)'}"
                         for s in specs)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_cat: dict[str, int] = {}
            by_perm: dict[str, int] = {}
            for t in self._tools.values():
                by_cat[t.category] = by_cat.get(t.category, 0) + 1
                by_perm[t.permission.value] = by_perm.get(t.permission.value, 0) + 1
            return {"total": len(self._tools), "by_category": by_cat,
                    "by_permission": by_perm}


# ── Adapters: turn the three existing shapes into ToolSpecs ───────────────────

def _load_overrides() -> dict[str, str]:
    """config/tool_permissions.json: {"tool_name": "USER_CONFIRMATION", ...}.
    Optional file; unknown names/levels are ignored with a log line."""
    try:
        data = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for name, level in (data or {}).items():
        try:
            Level(level)  # validates
            out[name] = level
        except ValueError:
            print(f"[SHIRAZI] ⚠️ tool_permissions.json: unknown level '{level}' for '{name}' — ignored")
    return out


def _spec_from_declaration(decl: Any, source: str,
                           overrides: dict[str, str]) -> Optional[ToolSpec]:
    """A Live function_declaration (dict or SDK object) → ToolSpec."""
    try:
        if isinstance(decl, dict):
            name = decl.get("name", "")
            desc = decl.get("description", "")
            schema = decl.get("parameters") or {}
        else:
            name = getattr(decl, "name", "")
            desc = getattr(decl, "description", "")
            schema = getattr(decl, "parameters", None) or {}
            if not isinstance(schema, dict):
                try:
                    schema = dict(schema)
                except Exception:
                    schema = {}
        name = str(name or "").strip()
        if not name:
            return None
        level = overrides.get(name)
        permission = Level(level) if level else default_level_for(name)
        return ToolSpec(
            name=name,
            category=_CATEGORY_HINTS.get(name, "utility"),
            permission=permission,
            description=str(desc or "")[:400],
            schema=schema if isinstance(schema, dict) else {},
            source=source,
        )
    except Exception as e:  # a bad declaration never aborts the build
        print(f"[SHIRAZI] ⚠️ tool registry: skipping bad declaration — {e}")
        return None


def build_registry(*, inline: Optional[list] = None,
                   actions=None, plugins=None) -> ToolRegistry:
    """Assemble the central registry from the three existing sources.

    Any of them may be None (e.g. in tests, or before the UI boots).
    Inline tools register first so `shutdown_jarvis` (legacy alias) keeps the
    same metadata as `shutdown_shirazi` via the hints table.
    """
    reg = ToolRegistry()
    overrides = _load_overrides()

    for decl in (inline or []):
        spec = _spec_from_declaration(decl, "inline", overrides)
        if spec:
            reg.register(spec)

    for loader, source in ((actions, "action"), (plugins, "plugin")):
        if loader is None:
            continue
        try:
            decls = loader.get_tool_declarations()
        except Exception as e:
            print(f"[SHIRAZI] ⚠️ tool registry: {source} loader failed — {e}")
            continue
        for decl in (decls or []):
            spec = _spec_from_declaration(decl, source, overrides)
            if spec:
                reg.register(spec)

    return reg
