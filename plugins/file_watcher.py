"""
SHIRAZI plugin: file_watcher.

Watches the Downloads folder and auto-organizes new files into
Documents / Images / Archives / Media subfolders. Actions: 'on'
(start watching), 'off' (stop), 'organize' (one sweep now), 'status'.

Free, local-only. Never moves files outside the user's Downloads tree.
"""

from __future__ import annotations

import os as _os
import shutil as _sh
import threading as _th
import time as _time
from pathlib import Path as _Path

PLUGIN = {
    "name": "file_watcher",
    "description": (
        "Watch Downloads and auto-organize new files (documents, images, "
        "archives, media). Trigger on 'watch my downloads', 'organize "
        "downloads', 'stop watching downloads', 'file watcher status'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "'on' | 'off' | 'organize' | 'status' (default 'status').",
            },
        },
        "required": [],
    },
}

_CATS = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt",
                  ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".md"},
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
               ".ico", ".tiff"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Media": {".mp3", ".wav", ".mp4", ".mkv", ".avi", ".mov", ".flac"},
    "Programs": {".exe", ".msi"},
}

_watching = False
_moved = 0
_lock = _th.Lock()


def _downloads() -> _Path:
    return _Path(_os.path.expanduser("~")) / "Downloads"


def _organize_once() -> int:
    dl = _downloads()
    moved = 0
    if not dl.is_dir():
        return 0
    for item in dl.iterdir():
        if not item.is_file() or item.name.startswith("."):
            continue
        # Never touch files inside our own target folders.
        if item.parent.name in _CATS:
            continue
        ext = item.suffix.lower()
        cat = next((c for c, exts in _CATS.items() if ext in exts), None)
        if not cat:
            continue
        dest_dir = dl / cat
        try:
            dest_dir.mkdir(exist_ok=True)
            dest = dest_dir / item.name
            if dest.exists():
                stem, suffix = item.stem, item.suffix
                i = 1
                while (dest_dir / f"{stem} ({i}){suffix}").exists():
                    i += 1
                dest = dest_dir / f"{stem} ({i}){suffix}"
            _sh.move(str(item), str(dest))
            moved += 1
        except Exception:
            continue
    return moved


def _watcher():
    global _moved
    seen = set()
    dl = _downloads()
    while _watching:
        try:
            if dl.is_dir():
                current = {p.name for p in dl.iterdir() if p.is_file()}
                if seen and current - seen:
                    # New files arrived — settle, then organize once.
                    _time.sleep(3)
                    n = _organize_once()
                    with _lock:
                        _moved += n
                seen = current
        except Exception:
            pass
        _time.sleep(5)


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _watching
    action = str(parameters.get("action") or "status").strip().lower()
    if action == "on":
        if not _watching:
            _watching = True
            _th.Thread(target=_watcher, daemon=True).start()
            text = "File watcher on, sir. New downloads will be auto-organized."
        else:
            text = "File watcher is already running, sir."
    elif action == "off":
        _watching = False
        text = "File watcher off, sir."
    elif action == "organize":
        n = _organize_once()
        text = (f"Organized {n} file(s) in Downloads, sir."
                if n else "Downloads are already tidy, sir.")
    else:
        with _lock:
            moved = _moved
        state = "running" if _watching else "stopped"
        text = (f"File watcher is {state}; {moved} file(s) auto-organized "
                f"this session, sir.")
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
