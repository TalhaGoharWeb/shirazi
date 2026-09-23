"""core/agent/register.py — register agent tools with the Phase-4 registry.

    from core import agent
    from core.tools import build_registry
    registry = build_registry(inline=..., actions=..., plugins=...)
    agent.install_agent_tools(registry)

All agent tools then appear in `registry.describe_for_prompt()` (capabilities
block) and `agent.agent_declarations()` produces Live function declarations
for `main.py::_build_config()`, so the model can call them through the
normal `_execute_tool` path — which still passes the permission engine.
"""

from __future__ import annotations

from typing import Any

from core import logger
from core.permissions import Level
from core.tools.registry import ToolRegistry, ToolSpec

from . import tools_browser, tools_computer, tools_files, tools_system
from . import shell, vision


def all_specs() -> list[ToolSpec]:
    out: list[ToolSpec] = []
    out.extend(tools_files.specs())
    out.extend(tools_browser.specs())
    out.extend(tools_system.specs())
    out.extend(tools_computer.specs())
    out.append(shell.spec())
    out.append(vision.spec())
    # research_topic: the executor dispatches it to the ResearchAgent.
    out.append(ToolSpec(
        name="research_topic", category="research",
        permission=Level.READ_ONLY,
        description=("Deep research on a topic: collects web sources and "
                     "builds a cited report with FACT/SOURCE/INFERENCE/"
                     "UNCERTAINTY labels. Read-only."),
        schema={"type": "object",
                "properties": {"topic": {"type": "STRING"},
                               "max_sources": {"type": "INTEGER"}}},
        source="agent"))
    # vision + agent-level helpers dispatched by the executor:
    out.append(ToolSpec(
        name="memory_save", category="memory",
        permission=Level.SAFE,
        description="Remember a fact for later. Handled by the agent executor.",
        schema={"type": "object", "properties": {"text": {"type": "STRING"}}},
        source="agent"))
    out.append(ToolSpec(
        name="memory_recall", category="memory",
        permission=Level.READ_ONLY,
        description="Search remembered facts. Handled by the agent executor.",
        schema={"type": "object", "properties": {"query": {"type": "STRING"}}},
        source="agent"))
    return out


def install(registry: ToolRegistry) -> int:
    """Register every agent tool. Returns the number registered."""
    n = 0
    for spec in all_specs():
        registry.register(spec)
        n += 1
    logger.info(f"agent tools registered: {n}")
    return n


def declarations() -> list[dict[str, Any]]:
    """Live function_declarations (dicts) for main.py::_build_config()."""
    decls = []
    for spec in all_specs():
        decls.append({
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.schema or {"type": "object", "properties": {}},
        })
    return decls


def names() -> list[str]:
    return [s.name for s in all_specs()]
