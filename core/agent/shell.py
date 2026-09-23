"""core/agent/shell.py — the ONLY path for shell execution (Phase 6).

HARD RULE (non-negotiable): the LLM never silently executes arbitrary shell.
Every shell command reaches the OS through
`core.permissions.request_shell_execution()`, which parks the exact command
behind the interface-issued confirm banner. No banner binding (headless or
early boot) → refused, never run.

This module deliberately contains no other way to run a command: the
executor calls `shell_run()` and nothing else. Static check for reviewers
(run from the repo root):

    grep -rn "subprocess" core/agent/   # → only shell.py, one call site
"""

from __future__ import annotations

import subprocess
from typing import Callable

from core import logger
from core.permissions import Level, request_shell_execution
from core.tools.registry import ToolSpec

_TIMEOUT_S = 60
_MAX_OUT = 6000


def _run_command(command: str) -> str:
    """The actual execution, invoked ONLY after the human confirms."""
    logger.executing(f"shell (confirmed): {command[:60]}")
    try:
        proc = subprocess.run(
            command,
            shell=True,  # noqa: S602 — the command was human-confirmed verbatim
            capture_output=True, text=True, timeout=_TIMEOUT_S,
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        out = out.strip()[:_MAX_OUT]
        status = f"exit={proc.returncode}"
        return f"$ {command}\n[{status}]\n{out or '(no output)'}"
    except subprocess.TimeoutExpired:
        return f"$ {command}\n[TIMEOUT after {_TIMEOUT_S}s — process killed]"
    except Exception as e:
        return f"Shell execution failed: {e}"


def shell_run(params: dict) -> str:
    """Route an LLM-produced command through the human gate. Never runs
    silently; never bypasses `request_shell_execution()`."""
    command = (params.get("command", "") or "").strip()
    context = (params.get("context", "") or "").strip()
    if not command:
        return "No command given."
    logger.warn("agent", f"shell requested: {command[:80]} — parking behind the confirm banner")
    return request_shell_execution(
        command,
        lambda: _run_command(command),
        context=context or "Requested by the SHIRAZI agent loop.",
    )


def spec() -> ToolSpec:
    return ToolSpec(
        name="shell_run",
        category="computer",
        permission=Level.USER_CONFIRMATION,
        description=("Run a shell command. ALWAYS parks behind the user's "
                     "confirm banner — never silent."),
        schema={"type": "object",
                "properties": {"command": {"type": "STRING"},
                               "context": {"type": "STRING"}}},
        source="agent",
    )


def dispatch() -> dict[str, Callable]:
    return {"shell_run": shell_run}
