"""
SHIRAZI plugin: ghost_protocol.

One-command privacy lockdown: minimize every window, mute system audio, and
wipe the clipboard. The "someone walked in" button. Inspired by the ghost/
stealth pattern in Jarvis-class assistants; clean-room implementation.

Windows-first (uses ctypes for Win32 calls); degrades gracefully elsewhere.
"""

from __future__ import annotations

import sys as _sys

PLUGIN = {
    "name": "ghost_protocol",
    "description": (
        "Instant privacy lockdown: minimize all windows, mute audio, clear "
        "the clipboard. Trigger on 'ghost protocol', 'privacy mode', "
        "'hide everything', 'someone is coming'. Do NOT use for normal "
        "window management."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


def _windows_lockdown() -> list[str]:
    done = []
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # Minimize all windows (Win+M equivalent via keybd + ShowWindow loop).
        user32.keybd_event(0x5B, 0, 0, 0)          # LWIN down
        user32.keybd_event(ord("M"), 0, 0, 0)      # M down
        user32.keybd_event(ord("M"), 0, 2, 0)      # M up
        user32.keybd_event(0x5B, 0, 2, 0)          # LWIN up
        done.append("windows minimized")
    except Exception as e:
        done.append(f"window minimize failed ({e})")
    try:
        # Mute via pycaw if present; otherwise nircmd-style fallback is skipped.
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import cast, POINTER
        devices = AudioUtilities.GetSpeakers()
        itf = devices.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(itf, POINTER(IAudioEndpointVolume))
        vol.SetMute(1, None)
        done.append("audio muted")
    except Exception:
        done.append("audio mute skipped (pycaw unavailable)")
    try:
        import ctypes
        ctypes.windll.user32.OpenClipboard(None)
        ctypes.windll.user32.EmptyClipboard()
        ctypes.windll.user32.CloseClipboard()
        done.append("clipboard wiped")
    except Exception:
        done.append("clipboard wipe skipped")
    return done


def run(parameters: dict, player=None, session_memory=None) -> str:
    if _sys.platform.startswith("win"):
        done = _windows_lockdown()
    else:
        done = ["lockdown is Windows-only in this build"]
    text = "Ghost protocol engaged, sir. " + "; ".join(done) + "."
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
