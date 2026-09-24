"""core/agent/tools_computer.py — computer tools for the agent engine (Phase 6).

Fine-grained wrappers around the existing action modules:
    actions/computer_control.py  — mouse / keyboard / clipboard / screenshot
    actions/open_app.py          — launch applications by name
    actions/desktop.py           — window management

Permission levels (mission spec: "mouse move SAFE, destructive ops gated"):
    mouse_move / screenshot / window_list / notify  → SAFE
    clipboard_read                                  → READ_ONLY
    mouse_click / keyboard_type / keyboard_press / keyboard_hotkey /
    clipboard_write / window_focus / app_open       → USER_CONFIRMATION

Every gated tool parks behind the human gate through the executor — the
agent can never drive the desktop silently for anything but moving the
pointer, reading pixels/clipboard, listing windows, or notifying.
"""

from __future__ import annotations

from typing import Callable

from core import logger
from core.permissions import Level
from core.tools.registry import ToolSpec


def _cc(params: dict) -> str:
    """Run one computer_control action; honest when pyautogui is missing."""
    try:
        from actions import computer_control as _mod
    except Exception as e:
        return f"computer_control unavailable: {e}"
    try:
        return _mod.computer_control(params, None, None, None) or "Done."
    except Exception as e:
        return f"Computer control failed: {e}"


def mouse_move(params: dict) -> str:
    x = int(params.get("x", 0) or 0)
    y = int(params.get("y", 0) or 0)
    logger.executing(f"mouse_move {x},{y}")
    return _cc({"action": "move", "x": x, "y": y})


def mouse_click(params: dict) -> str:
    x = params.get("x")
    y = params.get("y")
    button = (params.get("button", "left") or "left").lower()
    logger.executing(f"mouse_click {x},{y} {button}")
    args = {"action": "double_click" if params.get("double") else "click",
            "button": button}
    if x is not None and y is not None:
        args["x"], args["y"] = int(x), int(y)
    return _cc(args)


def keyboard_type(params: dict) -> str:
    text = params.get("text", "") or ""
    if not text:
        return "No text given to type."
    logger.executing("keyboard_type (text redacted from log)")
    return _cc({"action": "type", "text": text})


def keyboard_press(params: dict) -> str:
    key = (params.get("key", "") or "").strip().lower()
    if not key:
        return "No key given to press."
    logger.executing(f"keyboard_press {key}")
    return _cc({"action": "press", "key": key})


def keyboard_hotkey(params: dict) -> str:
    keys = params.get("keys") or []
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.replace("+", " ").split() if k.strip()]
    if not keys:
        return "No keys given for the hotkey."
    logger.executing(f"keyboard_hotkey {'+'.join(keys)}")
    return _cc({"action": "hotkey", "keys": keys})


def scroll(params: dict) -> str:
    direction = (params.get("direction", "down") or "down").lower()
    amount = max(1, min(int(params.get("amount", 3) or 3), 20))
    logger.executing(f"scroll {direction} x{amount}")
    return _cc({"action": "scroll", "direction": direction, "amount": amount})


def screenshot(params: dict) -> str:
    logger.executing("screenshot")
    out = _cc({"action": "screenshot"})
    if out.startswith("Screenshot saved"):
        return out
    return out


def clipboard_read(params: dict) -> str:
    logger.executing("clipboard_read")
    return _cc({"action": "copy"})  # computer_control "copy" reads the clipboard


def clipboard_write(params: dict) -> str:
    text = params.get("text", "") or ""
    if not text:
        return "No text given for the clipboard."
    logger.executing("clipboard_write (text redacted from log)")
    return _cc({"action": "paste", "text": text})  # "paste" writes+inserts the clipboard


def window_list(params: dict) -> str:
    logger.executing("window_list")
    try:
        import pygetwindow as gw  # type: ignore
    except Exception:
        return ("pygetwindow is not installed, so I cannot list windows. "
                "Install it with: pip install pygetwindow")
    try:
        titles = [w.title for w in gw.getAllWindows()
                  if getattr(w, "title", "").strip()]
        if not titles:
            return "No visible windows found."
        return "Open windows:\n" + "\n".join(f"- {t}" for t in titles[:30])
    except Exception as e:
        return f"Window listing failed: {e}"


def window_focus(params: dict) -> str:
    title = (params.get("title", "") or "").strip()
    if not title:
        return "No window title given."
    logger.executing(f"window_focus {title!r}")
    return _cc({"action": "focus_window", "title": title})


def app_open(params: dict) -> str:
    name = (params.get("name", "") or "").strip()
    if not name:
        return "No application name given."
    logger.executing(f"app_open {name!r}")
    try:
        from actions import open_app as _oa
        return _oa.open_app({"app_name": name}, None, None, None) or "Done."
    except Exception as e:
        return f"Could not open '{name}': {e}"


def notify(params: dict) -> str:
    text = (params.get("text", "") or "").strip()
    title = (params.get("title", "") or "SHIRAZI").strip()
    if not text:
        return "No notification text given."
    logger.executing(f"notify: {title}")
    try:
        import platform
        if platform.system() == "Windows":
            try:
                from win10toast import ToastNotifier  # type: ignore
                ToastNotifier().show_toast(title, text, duration=5, threaded=True)
                return "Notification shown."
            except Exception:
                pass
        # Cross-platform fallback: notify-send on Linux, osascript on macOS.
        import shutil
        import subprocess
        if platform.system() == "Darwin":
            subprocess.Popen(["osascript", "-e",
                              f'display notification "{text}" with title "{title}"'])
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", title, text])
        else:
            return f"(No notification backend — message was: {title}: {text})"
        return "Notification shown."
    except Exception as e:
        return f"Could not show notification: {e}"


_SPECS: list[tuple[str, str, Level, dict, Callable]] = [
    ("mouse_move",
     "Move the mouse pointer to x,y. SAFE — no clicks.",
     Level.SAFE,
     {"type": "object",
      "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"}}},
     mouse_move),
    ("mouse_click",
     "Click the mouse (optionally at x,y, left/right/double). Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object",
      "properties": {"x": {"type": "INTEGER"}, "y": {"type": "INTEGER"},
                     "button": {"type": "STRING"}, "double": {"type": "BOOLEAN"}}},
     mouse_click),
    ("keyboard_type",
     "Type text via the keyboard. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"text": {"type": "STRING"}}},
     keyboard_type),
    ("keyboard_press",
     "Press a single key (enter, tab, escape, ...). Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"key": {"type": "STRING"}}},
     keyboard_press),
    ("keyboard_hotkey",
     "Press a key combination, e.g. ['ctrl','c']. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"keys": {"type": "ARRAY", "items": {"type": "STRING"}}}},
     keyboard_hotkey),
    ("scroll",
     "Scroll up/down. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object",
      "properties": {"direction": {"type": "STRING"}, "amount": {"type": "INTEGER"}}},
     scroll),
    ("screenshot",
     "Capture the screen to a file. Read-only.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     screenshot),
    ("clipboard_read",
     "Read the clipboard contents. Read-only.",
     Level.READ_ONLY,
     {"type": "object", "properties": {}},
     clipboard_read),
    ("clipboard_write",
     "Write text to the clipboard. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"text": {"type": "STRING"}}},
     clipboard_write),
    ("window_list",
     "List open windows. Read-only.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     window_list),
    ("window_focus",
     "Bring a window to the front by title. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"title": {"type": "STRING"}}},
     window_focus),
    ("app_open",
     "Launch an application by name. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"name": {"type": "STRING"}}},
     app_open),
    ("notify",
     "Show a desktop notification. SAFE.",
     Level.SAFE,
     {"type": "object",
      "properties": {"title": {"type": "STRING"}, "text": {"type": "STRING"}}},
     notify),
]


def specs() -> list[ToolSpec]:
    return [ToolSpec(name=n, category="computer", permission=l, description=d,
                     schema=s, source="agent")
            for n, d, l, s, _h in _SPECS]


def dispatch() -> dict[str, Callable]:
    return {n: h for n, _d, _l, _s, h in _SPECS}


def tool_names() -> list[str]:
    return [n for n, _d, _l, _s, _h in _SPECS]
