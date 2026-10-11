"""
SHIRAZI plugin: clipboard_manager.

Clipboard history: keeps the last 20 text clips in memory for the session
and can recall or re-copy them. Actions: 'list', 'get' (by index),
'clear'. Monitoring starts on first use.

Free, local-only — nothing leaves the machine.
"""

from __future__ import annotations

import threading as _th

PLUGIN = {
    "name": "clipboard_manager",
    "description": (
        "Clipboard history: list recent clips, recall one by number, or "
        "clear history. Trigger on 'clipboard history', 'what did I copy', "
        "'copy clip 3', 'clear clipboard history'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'list' | 'get' | 'clear' (default 'list').",
            },
            "index": {
                "type": "NUMBER",
                "description": "Clip number for 'get' (1 = most recent).",
            },
        },
        "required": [],
    },
}

_history: list[str] = []
_last: str = ""
_lock = _th.Lock()
_watching = False


def _read_clip() -> str:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        if not user32.OpenClipboard(None):
            return ""
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return ""
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if not h:
                return ""
            ptr = kernel32.GlobalLock(h)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr) or ""
            finally:
                kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def _write_clip(text: str) -> bool:
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        CF_UNICODETEXT = 13
        data = (text + "\x00").encode("utf-16-le")
        h = kernel32.GlobalAlloc(0x0042, len(data))
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        if not user32.OpenClipboard(None):
            kernel32.GlobalFree(h)
            return False
        try:
            user32.EmptyClipboard()
            return bool(user32.SetClipboardData(CF_UNICODETEXT, h))
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def _watcher():
    global _last
    import time
    while True:
        try:
            cur = _read_clip()
            if cur and cur != _last:
                _last = cur
                with _lock:
                    if not _history or _history[0] != cur:
                        _history.insert(0, cur[:2000])
                        del _history[20:]
        except Exception:
            pass
        time.sleep(1.0)


def _ensure_watch():
    global _watching
    if not _watching:
        _watching = True
        t = _th.Thread(target=_watcher, daemon=True)
        t.start()


def run(parameters: dict, player=None, session_memory=None) -> str:
    _ensure_watch()
    action = str(parameters.get("action") or "list").strip().lower()
    with _lock:
        hist = list(_history)
    if action == "clear":
        with _lock:
            _history.clear()
        text = "Clipboard history cleared, sir."
    elif action == "get":
        try:
            idx = int(parameters.get("index") or 1) - 1
        except Exception:
            idx = 0
        if 0 <= idx < len(hist):
            ok = _write_clip(hist[idx])
            preview = hist[idx][:60].replace("\n", " ")
            text = (f"Clip {idx + 1} copied back to clipboard: {preview}…"
                    if ok else "Could not write to the clipboard.")
        else:
            text = f"Sir, there are only {len(hist)} clips in history."
    else:
        if not hist:
            text = "Clipboard history is empty so far, sir."
        else:
            lines = [f"{i + 1}. {c[:60].replace(chr(10), ' ')}…"
                     for i, c in enumerate(hist[:8])]
            text = "Recent clips: " + " | ".join(lines)
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
