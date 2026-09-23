"""core/tools/ — the central tool registry (Phase 4).

The app already had three places that know about tools: main.py's 8 inline
tools (TOOL_DECLARATIONS), core/action_loader.py's ActionRegistry (16 actions),
and core/plugin_loader.py's PluginRegistry (N plugins). This package does NOT
rewrite any of them — it is an ADAPTER that registers all three into one
central registry so the permission engine, the system prompt, and diagnostics
see the same tool list the Live session is actually offered.

Each tool declares: name, category, permission level, description, schema.
"""

from .registry import ToolRegistry, ToolSpec, build_registry

__all__ = ["ToolRegistry", "ToolSpec", "build_registry"]
