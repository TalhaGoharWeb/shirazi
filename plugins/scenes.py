"""
SHIRAZI plugin: scenes.

Automation workflows / "scenes": one phrase runs a macro routine.
Built-ins: "goodnight" (mute + lock workstation), "work mode" (open
VS Code + browser + terminal), "movie mode" (mute notifications, dim
ambition: open media player). Users can add their own scenes in the
plugin config namespace "scenes" as {name: [steps]}.

A step is {"do": "<action>", ...params}. Supported actions:
  mute(bool on) · lock() · open_app(name) · close_app(exe) · say(text)
"""

from __future__ import annotations

import subprocess as _sp
import sys as _sys

PLUGIN = {
    "name": "scenes",
    "description": (
        "Run an automation scene/macro: 'goodnight', 'work mode', 'movie "
        "mode', or a user-defined scene. Trigger on 'goodnight', 'work "
        "mode', 'movie mode', 'run scene <name>'. Do NOT use for single "
        "app launches (use the app control tool instead)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "scene": {
                "type": "STRING",
                "description": "Scene name: goodnight | work_mode | movie_mode | custom name.",
            },
        },
        "required": ["scene"],
    },
}

_BUILTINS = {
    "goodnight": [
        {"do": "mute", "on": True},
        {"do": "say", "text": "Goodnight routine complete."},
        {"do": "lock"},
    ],
    "work_mode": [
        {"do": "open_app", "name": "code"},
        {"do": "open_app", "name": "chrome"},
        {"do": "say", "text": "Work mode engaged."},
    ],
    "movie_mode": [
        {"do": "mute", "on": False},
        {"do": "open_app", "name": "vlc"},
        {"do": "say", "text": "Movie mode. Enjoy, sir."},
    ],
}


def _mute(on: bool) -> str:
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        devices = AudioUtilities.GetSpeakers()
        itf = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(itf, POINTER(IAudioEndpointVolume))
        vol.SetMute(1 if on else 0, None)
        return "ok"
    except Exception:
        return "audio unavailable"


def _lock() -> str:
    try:
        if _sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return "workstation locked"
        return "lock is Windows-only"
    except Exception as e:
        return f"lock failed ({e})"


def _open_app(name: str) -> str:
    # Strict: bare command name only — no paths, flags or chaining.
    name = str(name or "").strip().split()[0] if str(name or "").strip() else ""
    if not name or any(c in name for c in ("\\", "/", "&", "|", ";", "$", ".")):
        return f"refused unsafe app name {name!r}"
    try:
        if _sys.platform.startswith("win"):
            _sp.Popen(["cmd", "/c", "start", "", name],
                       stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        else:
            _sp.Popen([name], stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        return f"{name} launched"
    except Exception as e:
        return f"{name} failed ({e})"


def _close_app(exe: str) -> str:
    exe = str(exe or "").strip()
    if not exe.lower().endswith(".exe") or any(
            c in exe for c in ("\\", "/", " ", "&", "|", ";", "$")):
        return f"refused unsafe exe name {exe!r}"
    try:
        r = _sp.run(["taskkill", "/f", "/im", exe],
                    capture_output=True, timeout=10)
        return f"{exe} closed" if r.returncode == 0 else f"{exe} not running"
    except Exception as e:
        return f"{exe} failed ({e})"


def run(parameters: dict, player=None, session_memory=None) -> str:
    scene = str(parameters.get("scene") or "").strip().lower().replace(" ", "_")
    steps = list(_BUILTINS.get(scene, []))
    if not steps:
        try:
            from memory.config_manager import get_plugin_config
            custom = get_plugin_config("scenes").get("custom", {})
            if isinstance(custom, dict):
                steps = custom.get(scene, [])
        except Exception:
            pass
    if not steps:
        names = sorted(set(_BUILTINS) | {"(your custom scenes)"})
        return f"Sir, unknown scene '{scene}'. Available: {', '.join(names)}."

    log, spoken = [], None
    for st in steps:
        if not isinstance(st, dict):
            continue
        do = str(st.get("do", "")).lower()
        if do == "mute":
            log.append("mute: " + _mute(bool(st.get("on", True))))
        elif do == "lock":
            log.append(_lock())
        elif do == "open_app":
            log.append(_open_app(st.get("name", "")))
        elif do == "close_app":
            log.append(_close_app(st.get("exe", "")))
        elif do == "say":
            spoken = str(st.get("text", ""))
        else:
            log.append(f"unknown step '{do}' skipped")
    text = (spoken or f"Scene '{scene}' complete, sir.") + \
        (" (" + "; ".join(log) + ")" if log else "")
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
