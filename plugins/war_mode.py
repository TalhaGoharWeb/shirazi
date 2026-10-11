"""
SHIRAZI plugin: war_mode.

Tactical alert mode: switches the arc reactor to a crimson-red alert
palette, raises process priority, and announces readiness. Say
"war mode" to engage, "war mode off" / "stand down" to return to the
default amber theme.

This is the SHIRAZI-native take on the "war mode" pattern: instead of a
separate UI, it re-skins the AURA-58 reactor live.
"""

from __future__ import annotations

PLUGIN = {
    "name": "war_mode",
    "description": (
        "Engage or stand down tactical alert mode: crimson reactor theme, "
        "high process priority. Trigger on 'war mode', 'red alert', "
        "'battle stations', 'stand down', 'war mode off'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'on' to engage, 'off' to stand down.",
            },
        },
        "required": [],
    },
}


def _priority(high: bool) -> str:
    try:
        import psutil, os
        p = psutil.Process(os.getpid())
        if high:
            try:
                p.nice(psutil.HIGH_PRIORITY_CLASS)   # Windows
            except Exception:
                p.nice(-10)                          # POSIX
            return "priority raised"
        try:
            p.nice(psutil.NORMAL_PRIORITY_CLASS)
        except Exception:
            p.nice(0)
        return "priority normal"
    except Exception:
        return "priority control unavailable"


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = str(parameters.get("action") or "on").strip().lower()
    engage = action not in ("off", "stand", "down", "stand_down", "stop")

    theme_result = ""
    try:
        if player is not None and hasattr(player, "hud"):
            hud = player.hud
            if engage:
                # Crimson alert: temporarily override the reactor palette.
                view = getattr(hud, "_reactor_view", None)
                if view is not None:
                    from PyQt6.QtGui import QColor
                    view._amber = QColor("#ff4438")
                    view._amber_d = QColor("#b31a12")
                    view._cyan = QColor("#ffb347")
                    try:
                        view.update()
                    except Exception:
                        pass
                    theme_result = "reactor is crimson"
                else:
                    theme_result = "reactor view not ready"
            else:
                hud.set_reactor_theme(getattr(hud, "reactor_theme", "aura"))
                theme_result = "reactor theme restored"
    except Exception as e:
        theme_result = f"theme switch failed ({e})"

    prio = _priority(engage)
    text = ("WAR MODE ENGAGED, sir. " if engage else
            "Standing down, sir. ") + f"{theme_result}; {prio}."
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
