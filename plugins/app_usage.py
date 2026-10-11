"""
SHIRAZI plugin: app_usage.

Productivity insight: which apps you actually used today. Polls the
foreground window title every few seconds while the plugin is active
(action=on), then action=report gives today's ranking with time per app.

Local-only, lightweight (psutil + win32gui on Windows). Nothing uploaded.
"""

from __future__ import annotations

import datetime as _dt
import threading as _th
import time as _time

PLUGIN = {
    "name": "app_usage",
    "description": (
        "Track and report app usage: which apps you used today and for "
        "how long. Trigger on 'track my apps', 'app usage', 'what did I "
        "use today', 'stop tracking apps'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'on' | 'off' | 'report' (default 'report').",
            },
        },
        "required": [],
    },
}

_tracking = False
_seconds: dict[str, float] = {}
_day = _dt.date.today().isoformat()
_lock = _th.Lock()


def _active_app() -> str:
    try:
        import sys
        if sys.platform.startswith("win"):
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            import psutil
            try:
                return psutil.Process(pid.value).name() or "unknown"
            except Exception:
                return "unknown"
        return "unknown"
    except Exception:
        return "unknown"


def _poller():
    global _day
    last_app, last_t = None, _time.time()
    while _tracking:
        try:
            today = _dt.date.today().isoformat()
            with _lock:
                if today != _day:
                    _day = today
                    _seconds.clear()
            app = _active_app()
            now = _time.time()
            if last_app and last_app != "unknown":
                with _lock:
                    _seconds[last_app] = _seconds.get(last_app, 0.0) + (
                        now - last_t)
            last_app, last_t = app, now
        except Exception:
            pass
        _time.sleep(5)


def _fmt(s: float) -> str:
    m = int(s // 60)
    return f"{m // 60}h {m % 60}m" if m >= 60 else f"{m}m"


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _tracking
    action = str(parameters.get("action") or "report").strip().lower()
    if action == "on":
        if not _tracking:
            _tracking = True
            _th.Thread(target=_poller, daemon=True).start()
            text = "App tracking on, sir. Ask for the report anytime."
        else:
            text = "App tracking is already on, sir."
    elif action == "off":
        _tracking = False
        text = "App tracking off, sir."
    else:
        with _lock:
            data = dict(_seconds)
        if not data:
            text = ("Sir, no usage data yet — say 'track my apps' first, "
                    "then ask later today.")
        else:
            ranked = sorted(data.items(), key=lambda kv: kv[1],
                            reverse=True)[:6]
            total = sum(data.values())
            lines = [f"{name}: {_fmt(sec)}" for name, sec in ranked]
            text = (f"Today's app usage ({_fmt(total)} tracked), sir: " +
                    "; ".join(lines) + ".")
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
