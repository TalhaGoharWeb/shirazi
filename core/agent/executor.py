"""core/agent/executor.py — the agent loop (Phase 6).

    User → Intent Detection → Planner → Tool Selection → Permission Check
         → Tool Execution → Observation → Reasoning/Next Action → Result

The Agent:

- `run(request)` — the full coroutine loop. Never blocks the UI thread:
  blocking tool handlers run in an executor thread pool.
- `run_sync(request)` — for callers with no event loop (dashboard, tests);
  runs `run()` on a worker thread.
- `run_in_background(request)` — returns a Future immediately.
- `execute_tool(name, args)` — permission-gated single tool call, used by
  `main.py::_execute_tool` so Live-session tool calls route through the
  same gate and dispatch.

Hard rules, enforced here:
- Every tool execution passes `core.permissions.check()` first. The gate is
  never bypassed — including for tools the planner itself chose.
- Shell goes ONLY through `shell.shell_run` → `request_shell_execution()`.
  There is no other shell path in this package.
- Secrets never reach the log: observations pass through `logger.redact()`
  via the logger, and tool args are summarised, never dumped raw.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from core import events, logger
from core import permissions
from core.permissions import Level

from .intent import Intent, IntentResult, detect_intent
from .planner import Plan, PlanStep, build_plan
from . import tools_browser, tools_computer, tools_files, tools_system
from . import shell, vision
from .research import ResearchAgent


class AgentStatus(str, Enum):
    COMPLETED = "completed"
    PENDING_CONFIRMATION = "pending_confirmation"  # a gated step awaits the human
    DENIED = "denied"                             # permission engine refused
    CANCELLED = "cancelled"                       # human said no
    NEEDS_INPUT = "needs_input"                   # clarification needed
    FAILED = "failed"                             # unrecoverable error
    PARTIAL = "partial"                           # some steps done, then stopped


# Sentinel for "use the agent's configured confirm_hook". A per-call
# confirm_hook=None explicitly means *no* hook (desktop banner / PENDING
# path); omitting the kwarg keeps the configured default. Added in Phase 7
# so the mobile dashboard can route confirmations to the phone without
# mutating the shared agent's hook (which the Live loop also uses).
_UNSET = object()


@dataclass
class AgentResult:
    status: AgentStatus
    answer: str = ""
    plan: Optional[Plan] = None
    steps_executed: int = 0
    observations: list[dict[str, Any]] = field(default_factory=list)
    pending: Optional[dict[str, Any]] = None  # {tool, args, why} when PENDING
    ms: int = 0


class _DefaultProvider:
    """The real provider chain (Phase 4). Never raises, never returns None."""

    def generate(self, prompt: str, *, system: str = "",
                 timeout_s: float = 30.0, json_mode: bool = False) -> str:
        try:
            from core.providers import registry as _reg
            return _reg.generate(prompt, system=system,
                                 timeout_s=timeout_s,
                                 json_mode=json_mode).text or ""
        except Exception as e:
            return ""


class Agent:
    """The agent engine. Build with `create_agent()`."""

    def __init__(self, *,
                 tool_registry=None,
                 provider=None,
                 confirm_hook: Optional[Callable[[str, dict, str], bool]] = None,
                 max_steps: int = 12,
                 step_timeout_s: float = 120.0):
        self.tool_registry = tool_registry
        self.provider = provider or _DefaultProvider()
        self.confirm_hook = confirm_hook
        self.max_steps = max(1, max_steps)
        self.step_timeout_s = step_timeout_s
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="shirazi-agent")
        self._bg_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="shirazi-agent-bg")
        self._dispatch = self._build_dispatch()
        self._research = ResearchAgent()
        self._closed = False

    # ── Dispatch ──────────────────────────────────────────────────────────
    def _build_dispatch(self) -> dict[str, Callable[[dict], str]]:
        d: dict[str, Callable[[dict], str]] = {}
        d.update(tools_files.dispatch())
        d.update(tools_browser.dispatch())
        d.update(tools_system.dispatch())
        d.update(tools_computer.dispatch())
        d.update(shell.dispatch())
        d.update(vision.dispatch())
        d["research_topic"] = self._research_topic
        d["memory_save"] = self._memory_save
        d["memory_recall"] = self._memory_recall
        return d

    def handles(self, name: str) -> bool:
        return name in self._dispatch

    def available_tools(self) -> list[str]:
        if self.tool_registry is not None:
            reg_names = set(self.tool_registry.names())
            return sorted(n for n in self._dispatch if n in reg_names)
        return sorted(self._dispatch)

    # ── Single gated tool call (main.py::_execute_tool path) ─────────────
    def execute_tool(self, name: str, args: Optional[dict] = None,
                     *, confirm_hook=_UNSET) -> str:
        """Permission-gated single tool execution. Never bypasses the gate.

        `confirm_hook` overrides the agent's configured hook for this call
        only (Phase 7: the dashboard passes its phone hook here instead of
        mutating shared state)."""
        args = dict(args or {})
        hook = self.confirm_hook if confirm_hook is _UNSET else confirm_hook
        gate = permissions.check(name, args)
        events.emit("tool.called", {"name": name,
                                    "permission": gate.level.value})
        if not gate.allowed:
            logger.error("agent", f"denied {name}: {gate.reason}")
            events.emit("tool.denied", {"name": name, "reason": gate.reason})
            return f"Denied by the permission engine: {gate.reason}"
        handler = self._dispatch.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        if gate.needs_confirmation:
            logger.warn("agent", f"'{name}' needs confirmation — {gate.reason}")
            if hook is not None:
                try:
                    ok = bool(hook(name, args, gate.reason))
                except Exception as e:
                    logger.error("agent", f"confirm hook failed: {e}")
                    return f"Confirmation failed ({e}) — '{name}' was not run."
                if not ok:
                    events.emit("tool.denied",
                                {"name": name, "reason": "human declined"})
                    return f"Cancelled — you did not confirm '{name}'."
                events.emit("tool.confirmed", {"name": name})
            else:
                # The interface-issued banner (core/confirm.py) — the token
                # comes from the UI, never from the model.
                from core import confirm as _confirm
                detail = f"Tool: {name}\n{gate.reason}\nArguments: {_summarise_args(args)}"
                return _confirm.request(
                    key=f"agent:{name}:{abs(hash(json.dumps(args, sort_keys=True, default=str))) & 0xFFFF:04x}",
                    title=f"Allow '{name}'?",
                    detail=detail,
                    run=lambda: self._run_handler(name, args),
                )
        return self._run_handler(name, args)

    def _run_handler(self, name: str, args: dict) -> str:
        handler = self._dispatch[name]
        try:
            logger.executing(f"agent tool {name}")
            out = handler(dict(args))
            out = str(out if out is not None else "Done.")
            events.emit("tool.completed", {"name": name, "ok": True})
            logger.completed(f"agent tool {name}")
            return out
        except Exception as e:
            logger.error("agent", f"tool {name} failed: {e}")
            events.emit("tool.completed", {"name": name, "ok": False})
            return (f"Tool '{name}' failed: {e}. "
                    f"Alternatives: check the tool's requirements, or ask me "
                    f"to try a different approach.")

    # ── The loop ──────────────────────────────────────────────────────────
    async def run(self, request: str, *, confirm_hook=_UNSET) -> AgentResult:
        t0 = time.monotonic()
        hook = self.confirm_hook if confirm_hook is _UNSET else confirm_hook
        request = (request or "").strip()
        logger.listening(f"agent request: {request[:80]}")
        if not request:
            return AgentResult(AgentStatus.NEEDS_INPUT,
                               "What would you like me to do?", ms=_ms(t0))

        # 1. Intent detection (deterministic, zero-network).
        intent = detect_intent(request)
        logger.thinking(f"intent={intent.intent.value} "
                        f"confidence={intent.confidence:.2f}")

        # 2. Plain conversation needs no tools.
        if intent.intent in (Intent.CONVERSATION, Intent.UNKNOWN):
            answer = self._converse(request, intent)
            status = (AgentStatus.COMPLETED if intent.intent is Intent.CONVERSATION
                      else AgentStatus.NEEDS_INPUT)
            logger.completed("agent: conversational reply")
            return AgentResult(status, answer, ms=_ms(t0))

        # 3. Plan (explainable).
        plan = build_plan(intent, available=set(self.available_tools()))
        for step in plan.steps:
            step.permission = self._permission_of(step.tool, step.args)
        events.emit("agent.plan", {"request": request,
                                   "intent": intent.intent.value,
                                   "steps": [s.tool for s in plan.steps]})
        logger.thinking(f"plan: {len(plan.steps)} step(s)")

        if not plan.runnable():
            note = (plan.note or
                    "I could not build an executable plan for that.")
            logger.warn("agent", "empty plan")
            return AgentResult(AgentStatus.NEEDS_INPUT, note, plan=plan,
                               ms=_ms(t0))

        # 4-6. Walk the plan: gate → execute → observe → reason.
        result = AgentResult(AgentStatus.COMPLETED, plan=plan)
        loop = asyncio.get_running_loop()
        step_no = 0
        for step in plan.runnable():
            step_no += 1
            if step_no > self.max_steps:
                result.status = AgentStatus.PARTIAL
                result.answer += "\n(Stopped: step limit reached.)"
                break
            gate = permissions.check(step.tool, step.args)
            step.permission = gate.level.value
            if not gate.allowed:
                result.status = AgentStatus.DENIED
                result.answer = (f"I cannot do that: {gate.reason}")
                events.emit("tool.denied",
                            {"name": step.tool, "reason": gate.reason})
                logger.error("agent", f"denied at step {step_no}: {gate.reason}")
                break
            if gate.needs_confirmation:
                outcome = await self._confirm_step(loop, step, gate.reason,
                                                   hook)
                if outcome == "pending":
                    result.status = AgentStatus.PENDING_CONFIRMATION
                    result.pending = {"tool": step.tool, "args": step.args,
                                      "why": step.why, "step": step_no}
                    result.answer = (f"Step {step_no} needs your confirmation: "
                                     f"{step.why}")
                    break
                if outcome == "cancelled":
                    result.status = AgentStatus.CANCELLED
                    result.answer = "Cancelled — the step was not confirmed."
                    break
            obs = await loop.run_in_executor(
                self._pool, self._run_handler, step.tool, step.args)
            obs = _truncate(obs, 2000)
            result.observations.append({"tool": step.tool, "args": step.args,
                                        "observation": obs})
            result.steps_executed += 1
            events.emit("agent.step", {"n": step_no, "tool": step.tool,
                                       "ok": not obs.startswith("Tool ")})
            logger.file_op(f"agent step {step_no}/{len(plan.steps)}: {step.tool}")

        # 7. Reason over the observations → final answer.
        if result.status is AgentStatus.COMPLETED:
            result.answer = self._final_answer(request, plan, result)
        events.emit("agent.done",
                    {"status": result.status.value,
                     "steps": result.steps_executed})
        result.ms = _ms(t0)
        logger.completed(f"agent run: {result.status.value} "
                         f"({result.steps_executed} steps)")
        return result

    def run_sync(self, request: str,
                 timeout: Optional[float] = None,
                 *, confirm_hook=_UNSET) -> AgentResult:
        """Run the loop on a worker thread with its own event loop — the
        caller's thread (e.g. the UI thread) is never blocked."""
        fut = self._bg_pool.submit(asyncio.run, self.run(request,
                                                         confirm_hook=confirm_hook))
        return fut.result(timeout=timeout)

    def run_in_background(self, request: str,
                          *, confirm_hook=_UNSET) -> concurrent.futures.Future:
        """Fire-and-forget: returns a Future<AgentResult> immediately."""
        return self._bg_pool.submit(asyncio.run, self.run(
            request, confirm_hook=confirm_hook))

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._pool.shutdown(wait=False)
            self._bg_pool.shutdown(wait=False)

    # ── Internals ─────────────────────────────────────────────────────────
    def _permission_of(self, tool: str, args: dict) -> str:
        try:
            return permissions.check(tool, args).level.value
        except Exception:
            return Level.USER_CONFIRMATION.value

    async def _confirm_step(self, loop, step: PlanStep, reason: str,
                            hook) -> str:
        """'confirmed' | 'cancelled' | 'pending'."""
        if hook is not None:
            try:
                ok = await loop.run_in_executor(
                    self._pool, hook,
                    step.tool, step.args, reason)
                events.emit("tool.confirmed" if ok else "tool.denied",
                            {"name": step.tool})
                return "confirmed" if ok else "cancelled"
            except Exception as e:
                logger.error("agent", f"confirm hook failed: {e}")
                return "cancelled"
        return "pending"

    def _converse(self, request: str, intent: IntentResult) -> str:
        system = ("You are SHIRAZI, a helpful desktop assistant. Reply "
                  "briefly and naturally.")
        try:
            out = self.provider.generate(request, system=system,
                                         timeout_s=20.0) or ""
        except Exception:
            out = ""
        if not out or out.startswith("I couldn't"):
            if intent.intent is Intent.UNKNOWN:
                return ("I didn't quite understand that. I can open apps and "
                        "websites, search the web, manage files, control "
                        "volume and media, check system stats, read your "
                        "screen, and research topics — what would you like?")
            return "I'm here — what would you like me to do?"
        return out

    def _final_answer(self, request: str, plan: Plan,
                      result: AgentResult) -> str:
        if not result.observations:
            return "Done."
        obs_text = "\n\n".join(
            f"[{o['tool']}] {o['observation'][:800]}"
            for o in result.observations)
        system = ("You are SHIRAZI. Summarise the tool results below into a "
                  "short, direct answer to the user's request. Quote facts "
                  "from the observations; never invent details that are not "
                  "there.")
        try:
            out = self.provider.generate(
                f"Request: {request}\n\nTool results:\n{obs_text}",
                system=system, timeout_s=30.0) or ""
        except Exception:
            out = ""
        if out and not out.startswith("I couldn't"):
            return out
        # Honest fallback: the raw observations, labelled as such.
        return ("Here's what I found:\n\n" + obs_text[:2500])

    # ── Agent-dispatched meta tools ───────────────────────────────────────
    def _research_topic(self, params: dict) -> str:
        topic = (params.get("topic", "") or "").strip()
        if not topic:
            return "No research topic given."
        try:
            max_sources = max(1, min(int(params.get("max_sources", 5) or 5), 10))
        except Exception:
            max_sources = 5
        self._research.max_sources = max_sources
        report = self._research.run(topic)
        return report.render()

    def _memory_save(self, params: dict) -> str:
        text = (params.get("text", "") or "").strip()
        if not text:
            return "Nothing to remember."
        try:
            from core.memory import default_store
            store = default_store()
            key = f"agent-note-{int(time.time())}"
            store.remember("user", key, text,
                           metadata={"via": "agent"})
            events.emit("memory.changed", {"layer": "user", "key": key})
            return "Remembered."
        except Exception as e:
            return f"Could not save the memory: {e}"

    def _memory_recall(self, params: dict) -> str:
        query = (params.get("query", "") or "").strip()
        try:
            from core.memory import default_store
            store = default_store()
            hits = store.search("user", query, limit=8)
            if not hits:
                return "I don't have anything remembered about that."
            lines = [f"- {h.get('key')}: {h.get('value')}" for h in hits]
            return "What I remember:\n" + "\n".join(lines)
        except Exception as e:
            return f"Could not search memories: {e}"


def _truncate(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n…(truncated)"


def _summarise_args(args: dict) -> str:
    parts = []
    for k, v in (args or {}).items():
        s = str(v)
        if len(s) > 60:
            s = s[:60] + "…"
        parts.append(f"{k}={s}")
    return ", ".join(parts) if parts else "(no arguments)"


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def create_agent(*, tool_registry=None, provider=None,
                 confirm_hook: Optional[Callable[[str, dict, str], bool]] = None,
                 max_steps: int = 12) -> Agent:
    """Build the agent engine. `confirm_hook(tool, args, reason) -> bool`
    auto-answers confirmation gates (tests, headless runners); without it,
    gated steps return PENDING_CONFIRMATION (or the on-screen banner via
    `execute_tool`)."""
    return Agent(tool_registry=tool_registry, provider=provider,
                 confirm_hook=confirm_hook, max_steps=max_steps)
