"""core/agent/ — the SHIRAZI agent engine (Phase 6).

The full pipeline, in one place:

    User → Intent Detection → Planner → Tool Selection → Permission Check
         → Tool Execution → Observation → Reasoning/Next Action → Result

Usage:

    from core import agent
    eng = agent.create_agent(tool_registry=my_registry)
    result = eng.run_sync("Open Chrome and search for today's weather")

Every tool the engine owns is registered in the Phase-4 central tool
registry (`core/tools`) with a permission level, and every execution passes
the Phase-4 permission engine (`core/permissions.check`). Nothing bypasses
them — there is no other shell path but
`core.permissions.request_shell_execution()`.

The engine never blocks the UI thread: `Agent.run()` is a coroutine and
blocking tool handlers run in an executor; `run_sync()` / `run_in_background()`
marshall onto worker threads for callers that have no event loop.
"""

from .intent import Intent, IntentResult, detect_intent
from .planner import Plan, PlanStep, build_plan
from .executor import Agent, AgentResult, AgentStatus, create_agent
from .research import ResearchAgent, ResearchReport, Claim, Citation, LABELS
from .vision import Vision, vision_status
from .shell import shell_run
from .register import install as install_agent_tools, declarations as agent_declarations

__all__ = [
    "Intent", "IntentResult", "detect_intent",
    "Plan", "PlanStep", "build_plan",
    "Agent", "AgentResult", "AgentStatus", "create_agent",
    "ResearchAgent", "ResearchReport", "Claim", "Citation", "LABELS",
    "Vision", "vision_status",
    "shell_run",
    "install_agent_tools", "agent_declarations",
]
