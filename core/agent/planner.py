"""core/agent/planner.py — the planner (Phase 6).

Turns an IntentResult + the tool registry snapshot into an explainable Plan:
an ordered list of PlanSteps, each carrying `why` — a plain-language reason
the user can read. The plan is data, not code: the executor walks it, and
the reasoning step may extend or replace it.

The planner is deterministic (rule-based). It consults the registry only to
verify a tool name exists; a tool missing from the registry degrades the
step to a SKIPPED note instead of crashing the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .intent import Intent, IntentResult


@dataclass
class PlanStep:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    why: str = ""                    # shown to the user — the explanation
    permission: str = ""             # filled by the executor from the registry
    skipped: str = ""                # set when the tool is not available

    def describe(self, i: int) -> str:
        perm = f" [{self.permission}]" if self.permission else ""
        if self.skipped:
            return f"{i}. {self.tool} — SKIPPED: {self.skipped}"
        return f"{i}. {self.tool}{perm} — {self.why or '(no reason given)'}"


@dataclass
class Plan:
    intent: Intent
    request: str
    steps: list[PlanStep] = field(default_factory=list)
    note: str = ""                   # shown when the plan is partial

    def __len__(self) -> int:
        return len(self.steps)

    def explain(self) -> str:
        """The explainable plan — what the user can see."""
        lines = [f"Plan for: {self.request!r} "
                 f"(intent: {self.intent.value}, {len(self.steps)} step(s))"]
        for i, step in enumerate(self.steps, 1):
            lines.append(step.describe(i))
        if self.note:
            lines.append(f"Note: {self.note}")
        return "\n".join(lines)

    def runnable(self) -> list[PlanStep]:
        return [s for s in self.steps if not s.skipped]


def build_plan(result: IntentResult,
               *, available: Optional[set[str]] = None) -> Plan:
    """Build the plan. `available` is the set of registered tool names;
    a planned tool that is missing becomes a SKIPPED step with a reason."""
    plan = Plan(intent=result.intent, request=result.raw)

    def _add(tool: str, args: dict, why: str) -> None:
        step = PlanStep(tool=tool, args=args, why=why)
        if available is not None and tool not in available:
            step.skipped = (f"'{tool}' is not registered — "
                            f"it may be unavailable on this machine.")
        plan.steps.append(step)

    s = result.slots
    intent = result.intent

    if intent is Intent.COMPOUND:
        for sub in result.sub_intents:
            sub_plan = build_plan(sub, available=available)
            plan.steps.extend(sub_plan.steps)
        if not plan.steps:
            plan.note = "I understood the parts but could not build steps for them."
        return plan

    # ── Browser ───────────────────────────────────────────────────────────
    if intent is Intent.BROWSER_OPEN:
        _add("browser_open", {"url": s.get("url", "")},
             f"Open {s.get('url', 'the page')} in the browser.")
    elif intent is Intent.WEB_SEARCH:
        _add("browser_search", {"query": s.get("query", ""), "mode": "search"},
             f"Search the web for '{s.get('query', '')}'.")
    elif intent is Intent.APP_OPEN:
        _add("app_open", {"name": s.get("app", "")},
             f"Launch {s.get('app', 'the app')}.")
    # ── Files ─────────────────────────────────────────────────────────────
    elif intent is Intent.FILE_FIND:
        args = {"path": s.get("path", "home"), "max_results": 20}
        if s.get("name"):
            args["name"] = s["name"]
        if s.get("extension"):
            args["extension"] = s["extension"]
        why = "Look for the file"
        if s.get("extension"):
            why += f" ({s['extension']})"
        why += f" in {args['path']}"
        if s.get("recency"):
            why += f" (recent: {s['recency']})"
        _add("search_files", args, why + ".")
    elif intent is Intent.FILE_READ:
        _add("read_file", {"path": "home", "name": s.get("name", "")},
             f"Read '{s.get('name', 'the file')}'.")
    elif intent is Intent.FILE_SUMMARIZE:
        _add("summarize_file", {"path": "home", "name": s.get("name", "")},
             f"Summarize '{s.get('name', 'the file')}'.")
    elif intent is Intent.FILE_CREATE:
        _add("create_file", {"path": "desktop", "name": s.get("name", "")},
             f"Create '{s.get('name', 'the file')}' on the desktop.")
    elif intent is Intent.FILE_DELETE:
        _add("delete_file", {"path": "home", "name": s.get("name", "")},
             f"Delete '{s.get('name', 'the file')}' (moves to trash; needs your confirmation).")
    elif intent is Intent.FILE_MOVE:
        action = s.get("action", "move")
        _add({"move": "move_file", "rename": "rename_file",
              "copy": "copy_file"}.get(action, "move_file"),
             {}, f"{action.capitalize()} the file (needs your confirmation).")
    elif intent is Intent.FILE_ORGANIZE:
        _add("file_organize", {"path": "desktop"},
             "Tidy the desktop into type folders (needs your confirmation).")
    elif intent is Intent.FOLDER_OPEN:
        folder = (s.get("folder") or "").strip() or "project folder"
        _add("open_file", {"path": "home", "name": folder},
             f"Open '{folder}' so you can see it.")
    # ── Screen / vision ───────────────────────────────────────────────────
    elif intent is Intent.SCREEN_SUMMARIZE:
        _add("vision_describe", {"question": "Summarize what is on the screen."},
             "Capture the screen and summarize what is on it.")
    elif intent is Intent.SCREEN_ASK:
        _add("vision_describe", {"question": s.get("question", "What is on the screen?")},
             "Capture the screen and answer the question from what is visible.")
    # ── Volume / media ────────────────────────────────────────────────────
    elif intent is Intent.VOLUME:
        op = s.get("op", "get")
        if op == "down":
            _add("volume_down", {}, "Lower the volume.")
        elif op == "up":
            _add("volume_up", {}, "Raise the volume.")
        elif op == "mute":
            _add("volume_mute", {}, "Mute the audio.")
        elif op == "unmute":
            _add("volume_unmute", {}, "Unmute the audio.")
        elif op == "set":
            _add("volume_set", {"value": s.get("value", 50)},
                 f"Set the volume to {s.get('value', 50)}%.")
        else:
            _add("volume_get", {}, "Check the current volume.")
    elif intent is Intent.MEDIA:
        _add("media_control", {"op": s.get("op", "play_pause")},
             "Control media playback.")
    # ── System ────────────────────────────────────────────────────────────
    elif intent is Intent.SYSTEM:
        _add("system_stats", {},
             "Read CPU, RAM, disk, battery, uptime and network.")
    # ── Computer ──────────────────────────────────────────────────────────
    elif intent is Intent.COMPUTER:
        if s.get("action") == "screenshot":
            _add("screenshot", {}, "Take a screenshot.")
        else:
            plan.note = ("Computer control needs a target — e.g. 'click at "
                         "500 300', 'type hello', 'press enter'.")
    elif intent is Intent.CLIPBOARD:
        if s.get("op") == "read":
            _add("clipboard_read", {}, "Read the clipboard.")
        else:
            _add("clipboard_write", {"text": ""},
                 "Write to the clipboard (needs the text to copy).")
    # ── Research / shell / memory / misc ──────────────────────────────────
    elif intent is Intent.RESEARCH:
        _add("research_topic", {"topic": s.get("topic", "")},
             f"Research '{s.get('topic', 'the topic')}' across web sources "
             "and build a cited report.")
    elif intent is Intent.SHELL:
        _add("shell_run", {"command": s.get("command", "")},
             "Run the shell command — this always needs your confirmation.")
    elif intent is Intent.MEMORY_SAVE:
        _add("memory_save", {"text": s.get("text", "")}, "Remember this.")
    elif intent is Intent.MEMORY_RECALL:
        _add("memory_recall", {"query": s.get("query", "")},
             "Search what I remember.")
    elif intent is Intent.REMINDER:
        plan.note = ("Reminders run through the existing `reminder` tool — "
                     "say 'remind me to … at …'.")
    elif intent is Intent.MESSAGE:
        plan.note = ("Sending messages runs through the existing `send_message` "
                     "tool and needs your confirmation.")
    elif intent is Intent.SETTING:
        plan.note = ("Settings (brightness, WiFi, dark mode) run through the "
                     "existing `computer_settings` tool.")
    elif intent is Intent.NOTIFY:
        _add("notify", {"text": s.get("text", "")},
             "Show a desktop notification.")
    elif intent is Intent.CONVERSATION:
        plan.note = "No tools needed — this is a conversational reply."
    else:
        plan.note = ("I could not understand that well enough to build a plan. "
                     "Try rephrasing, or ask what I can do.")

    return plan
