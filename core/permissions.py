"""core/permissions.py — the generalized permission engine (Phase 4).

This generalizes the Phase-1 `core/confirm.py` gate. Before Phase 4, only
`computer_settings` irreversible actions passed a human gate; `dev_agent`
ran LLM-generated subprocesses, `computer_control` gave ungated mouse +
keyboard (Win+R → type → Enter is one hotkey+type away), and
`file_controller` deleted files with no confirm at all.

NOW: every tool call passes `check()` before it runs. The wiring point is
`ShiraziLive._execute_tool` (main.py), which consults the central tool
registry (core/tools) and this module before dispatching.

Levels:
    SAFE              — read-only or trivially reversible; runs freely.
    READ_ONLY         — reads data but not system state; runs freely, logged.
    USER_CONFIRMATION — parks behind the on-screen CONFIRM/CANCEL banner
                        (core/confirm.py). The token is issued by the
                        interface, never by the model.
    PRIVILEGED        — denied unless developer mode is on (config), and even
                        then gated by USER_CONFIRMATION.

Default mappings (mission spec):
    read system info        → SAFE            (system_status)
    open website            → SAFE            (browser navigate is benign;
                                               the *automation* is the risk —
                                               see below)
    move mouse / type keys  → USER_CONFIRMATION (computer_control — full
                                               desktop control is one
                                               Win+R away from a shell)
    delete files            → USER_CONFIRMATION (file_controller delete/move)
    arbitrary shell         → USER_CONFIRMATION — MANDATORY. No path may ever
                                               run an LLM-generated command
                                               string without a human gate.
    modifying security settings → PRIVILEGED
    installing software     → USER_CONFIRMATION

Safe mode (config "safe_mode": true): every USER_CONFIRMATION tool is treated
as denied-by-default until the user confirms per session... in practice it
forces the banner for all of them and denies PRIVILEGED outright.

THE HARD RULE
    Never allow the LLM to silently execute arbitrary commands. There is no
    `shell.execute(user_generated_string)` path in this codebase, and this
    module exists to keep it that way: `request_shell_execution()` is the ONLY
    sanctioned way to run an LLM-produced command, and it always goes through
    the human gate.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class Level(str, Enum):
    SAFE = "SAFE"
    READ_ONLY = "READ_ONLY"
    USER_CONFIRMATION = "USER_CONFIRMATION"
    PRIVILEGED = "PRIVILEGED"


# ── Default permission per tool ───────────────────────────────────────────────
# Unknown tools are not in this table: check() treats them as
# USER_CONFIRMATION (deny-by-default posture — fail closed).

_DEFAULT_LEVELS: dict[str, Level] = {
    # Inline tools (main.py)
    "system_status":   Level.SAFE,
    "screen_process":  Level.SAFE,
    "close_camera":    Level.SAFE,
    "manage_monitor":  Level.READ_ONLY,
    "shutdown_shirazi": Level.USER_CONFIRMATION,
    "shutdown_jarvis": Level.USER_CONFIRMATION,  # legacy alias, same risk
    "save_memory":     Level.SAFE,
    "recall_memory":   Level.READ_ONLY,
    "undo":            Level.SAFE,
    # Actions
    "browser_control": Level.USER_CONFIRMATION,  # navigate is SAFE; automation isn't
    "game_updater":    Level.USER_CONFIRMATION,  # installs/updates software
    "computer_settings": Level.USER_CONFIRMATION,  # volume/WiFi/shutdown — irreversible bits
    "file_processor":  Level.READ_ONLY,
    "file_controller": Level.USER_CONFIRMATION,  # move/copy/rename/delete
    "dev_agent":       Level.USER_CONFIRMATION,  # writes code AND runs subprocess
    "code_helper":     Level.READ_ONLY,
    "computer_control": Level.USER_CONFIRMATION,  # raw mouse/keyboard — Win+R risk
    "desktop_control": Level.USER_CONFIRMATION,  # window/app management
    "youtube_video":   Level.READ_ONLY,
    "web_search":      Level.READ_ONLY,
    "flight_finder":   Level.READ_ONLY,
    "reminder":        Level.USER_CONFIRMATION,  # creates persistent OS state
    "send_message":    Level.USER_CONFIRMATION,  # sends messages as the user
    "open_app":        Level.USER_CONFIRMATION,  # launches arbitrary apps
    "screen_processor": Level.SAFE,
    "weather_report":  Level.READ_ONLY,
    # ── Agent engine tools (Phase 6, core/agent/) — one level per operation.
    # Reads are free; anything that mutates state, drives the desktop, or
    # runs code is gated. Unknown tools stay USER_CONFIRMATION (fail closed).
    "search_files":    Level.READ_ONLY,
    "read_file":       Level.READ_ONLY,
    "create_file":     Level.USER_CONFIRMATION,
    "rename_file":     Level.USER_CONFIRMATION,
    "move_file":       Level.USER_CONFIRMATION,
    "copy_file":       Level.USER_CONFIRMATION,
    "delete_file":     Level.USER_CONFIRMATION,
    "open_file":       Level.USER_CONFIRMATION,  # may launch an executable
    "summarize_file":  Level.READ_ONLY,
    "file_organize":   Level.USER_CONFIRMATION,
    "browser_open":    Level.USER_CONFIRMATION,
    "browser_navigate": Level.USER_CONFIRMATION,
    "browser_click":   Level.USER_CONFIRMATION,  # automation risk
    "browser_type":    Level.USER_CONFIRMATION,  # automation risk
    "browser_extract": Level.READ_ONLY,
    "browser_search":  Level.READ_ONLY,
    "browser_download": Level.USER_CONFIRMATION,
    "system_stats":    Level.SAFE,
    "processes":       Level.SAFE,
    "battery_status":  Level.SAFE,
    "network_status":  Level.SAFE,
    "volume_get":      Level.SAFE,
    "volume_up":       Level.USER_CONFIRMATION,
    "volume_down":     Level.USER_CONFIRMATION,
    "volume_set":      Level.USER_CONFIRMATION,
    "volume_mute":     Level.USER_CONFIRMATION,
    "volume_unmute":   Level.USER_CONFIRMATION,
    "media_control":   Level.USER_CONFIRMATION,
    "mouse_move":      Level.SAFE,               # pointer move only, no clicks
    "mouse_click":     Level.USER_CONFIRMATION,
    "keyboard_type":   Level.USER_CONFIRMATION,
    "keyboard_press":  Level.USER_CONFIRMATION,
    "keyboard_hotkey": Level.USER_CONFIRMATION,
    "scroll":          Level.USER_CONFIRMATION,
    "screenshot":      Level.SAFE,
    "clipboard_read":  Level.READ_ONLY,
    "clipboard_write": Level.USER_CONFIRMATION,
    "window_list":     Level.SAFE,
    "window_focus":    Level.USER_CONFIRMATION,
    "app_open":        Level.USER_CONFIRMATION,
    "notify":          Level.SAFE,
    "shell_run":       Level.USER_CONFIRMATION,  # + request_shell_execution
    "vision_describe": Level.READ_ONLY,
    "research_topic":  Level.READ_ONLY,
    "memory_save":     Level.SAFE,
    "memory_recall":   Level.READ_ONLY,
}

# Actions *within* an otherwise-gated tool that are risky enough to deserve
# their own banner even when the tool call itself was already confirmed.
# (tool_name, argument-substring) → human reason.
_SENSITIVE_SUBACTIONS: list[tuple[str, str, str]] = [
    ("computer_settings", "shutdown", "shutting the machine down"),
    ("computer_settings", "restart", "restarting the machine"),
    ("computer_control", "hotkey", "pressing system hotkeys (e.g. Win+R)"),
    ("file_controller", "delete", "deleting files"),
]

# Config keys this module reads (from config/api_keys.json via settings).
_CFG_DEVELOPER_MODE = "developer_mode"
_CFG_SAFE_MODE = "safe_mode"

_lock = threading.Lock()
_config_snapshot: dict = {}


def configure(config: dict) -> None:
    """Hand this module the user's config (developer_mode / safe_mode flags).
    Called once at startup; safe to call again after a settings change."""
    global _config_snapshot
    with _lock:
        _config_snapshot = dict(config or {})


def _flags() -> tuple[bool, bool]:
    with _lock:
        cfg = _config_snapshot
    return bool(cfg.get(_CFG_DEVELOPER_MODE, False)), bool(cfg.get(_CFG_SAFE_MODE, False))


def default_level_for(tool_name: str) -> Level:
    """The shipped default for a tool. Unknown tools → USER_CONFIRMATION
    (deny-by-default: a new tool the engine never saw is never free)."""
    return _DEFAULT_LEVELS.get(tool_name, Level.USER_CONFIRMATION)


def known_tools() -> list[str]:
    """Every tool with a shipped default level (public for the per-user
    policy layer in core/accounts/permissions.py)."""
    return sorted(_DEFAULT_LEVELS)


@dataclass
class CheckResult:
    allowed: bool
    needs_confirmation: bool
    level: Level
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


def check(tool_name: str, parameters: Optional[dict] = None) -> CheckResult:
    """The single gate every tool call passes before dispatch.

    Returns whether it may run, and whether a human must confirm first.
    `parameters` lets sensitive sub-actions (shutdown inside
    computer_settings, delete inside file_controller) raise the level for
    that specific call.
    """
    developer_mode, safe_mode = _flags()
    level = default_level_for(tool_name)

    params = parameters or {}
    sub_reason = ""
    for tname, marker, why in _SENSITIVE_SUBACTIONS:
        if tname == tool_name and _params_mention(params, marker):
            level = max(level, Level.USER_CONFIRMATION,
                        key=lambda l: _RANK[l])
            sub_reason = f" ({why})"
            break

    if level is Level.PRIVILEGED:
        if not developer_mode:
            return CheckResult(False, False, level,
                               f"'{tool_name}' is PRIVILEGED and developer mode is off — denied.")
        # Developer mode on: still a human gate, never silent.
        return CheckResult(True, True, level,
                           f"'{tool_name}' is PRIVILEGED (developer mode on) — human confirmation required.")

    if level is Level.USER_CONFIRMATION:
        return CheckResult(True, True, level,
                           f"'{tool_name}' needs your confirmation{sub_reason}.")

    # SAFE / READ_ONLY
    if safe_mode and level is Level.READ_ONLY:
        return CheckResult(True, True, level,
                           f"safe mode: '{tool_name}' reads data — confirming anyway.")
    return CheckResult(True, False, level, "")


def _params_mention(params: dict, marker: str) -> bool:
    needle = marker.lower()
    for value in params.values():
        if isinstance(value, str) and needle in value.lower():
            return True
    return False


_RANK = {Level.SAFE: 0, Level.READ_ONLY: 1,
         Level.USER_CONFIRMATION: 2, Level.PRIVILEGED: 3}


# ── The arbitrary-shell gate ──────────────────────────────────────────────────
# MANDATORY: any LLM-generated command string reaches the OS only through
# request_shell_execution(). It parks the exact command behind the
# interface-issued confirm banner (core/confirm.py). No banner binding (headless
# / early boot) → refused, never run.

def request_shell_execution(command: str, run: Callable[[], str],
                            *, context: str = "") -> str:
    """Run an LLM-produced shell command ONLY with human confirmation.

    `command` is shown verbatim on the banner so the user sees exactly what
    would run. `run` is the zero-arg callable that actually executes it.
    Returns the sentence for the model (pending banner) or the refusal.
    """
    from core import confirm as _confirm

    command = (command or "").strip()
    if not command:
        return "No command to run — nothing was done."

    title = "Run shell command?"
    detail = f"$ {command}"
    if context:
        detail += f"\n\n{context}"

    return _confirm.request(
        key=f"shell:{hash(command) & 0xFFFF:04x}",
        title=title,
        detail=detail,
        run=run,
    )


def shell_policy_note() -> str:
    """One paragraph for the system prompt: what the model may promise."""
    return ("You never run shell commands silently. If a task needs a command "
            "run, say what it is and wait: the user confirms it on screen "
            "before anything executes.")
