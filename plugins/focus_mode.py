"""
SHIRAZI plugin: focus_mode.

Do-not-disturb macro for deep work: mutes system audio, kills common
distraction apps (configurable), and optionally starts a timed focus
session. Say "focus mode" to enter, "focus mode off" or "end focus" to leave.

Config namespace "focus_mode":
  block_apps: list of exe names to close on entry (default: [])
  minutes:    default session length (default 45)
"""

from __future__ import annotations

import sys as _sys

PLUGIN = {
    "name": "focus_mode",
    "description": (
        "Enter or leave focus (do-not-disturb) mode: mutes audio and closes "
        "distraction apps. Trigger on 'focus mode', 'do not disturb', "
        "'start focusing', 'end focus', 'focus mode off'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'on' to enter focus mode, 'off' to leave it.",
            },
            "minutes": {
                "type": "NUMBER",
                "description": "Focus session length in minutes (default 45).",
            },
        },
        "required": [],
    },
}

_DEFAULT_BLOCK = ["Discord.exe", "Telegram.exe", "Spotify.exe"]


def _mute(on: bool) -> str:
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        devices = AudioUtilities.GetSpeakers()
        itf = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(itf, POINTER(IAudioEndpointVolume))
        vol.SetMute(1 if on else 0, None)
        return "muted" if on else "unmuted"
    except Exception:
        return "audio control unavailable"


def _close_distractions(block: list) -> int:
    if not _sys.platform.startswith("win"):
        return 0
    closed = 0
    try:
        import subprocess
        for exe in block:
            name = str(exe).strip()
            if not name or not name.lower().endswith(".exe"):
                continue
            # Strict allowlist shape: plain exe filename, no paths/args.
            if any(c in name for c in ("\\", "/", " ", "&", "|", ";", "$")):
                continue
            r = subprocess.run(
                ["taskkill", "/f", "/im", name],
                capture_output=True, timeout=10)
            if r.returncode == 0:
                closed += 1
    except Exception:
        pass
    return closed


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "on").strip().lower()
    minutes = parameters.get("minutes")
    try:
        minutes = int(minutes) if minutes else 45
    except Exception:
        minutes = 45
    minutes = max(5, min(240, minutes))

    cfg = {}
    try:
        from memory.config_manager import get_plugin_config
        cfg = get_plugin_config("focus_mode")
    except Exception:
        pass
    block = cfg.get("block_apps", _DEFAULT_BLOCK)

    if action in ("off", "end", "stop", "leave"):
        audio = _mute(False)
        text = f"Focus mode off, sir. Audio {audio}."
    else:
        audio = _mute(True)
        closed = _close_distractions(block if isinstance(block, list) else [])
        text = (f"Focus mode engaged for {minutes} minutes, sir. "
                f"Audio {audio}; closed {closed} distraction app(s). "
                f"Say 'end focus' when you surface.")
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
