"""core/logger.py — structured observability for Shirazi (Phase 4).

One logger, fixed tags, zero secret leakage:

    from core import logger
    logger.listening()                 # [SHIRAZI] 🎤 Listening...
    logger.thinking("provider=gemini") # [SHIRAZI] 🧠 Thinking... provider=gemini
    logger.error("tools", "boom")      # [SHIRAZI] ❌ Error [tools] boom

Tags (mission spec):
    🎤 Listening...   🔊 Speaking...   🧠 Thinking...   🌐 Searching...
    🖥️ Executing...   📁 File operation...   ⚙️ System...   🔌 Device...
    ✅ Completed      ⚠️ Warning       ❌ Error

REDACTION (hard rule): every message passes through `redact()` before it is
emitted. Anything that looks like an API key, password, token or auth secret
is replaced with [REDACTED]. Never log env dumps or credential values — this
module cannot stop a caller from printing them directly, but nothing that
goes through here leaks.
"""

from __future__ import annotations

import re
import sys
import threading
import time
from typing import Any, Optional

_PREFIX = "[SHIRAZI]"

_lock = threading.Lock()
_handlers: list = []          # extra sinks: fn(tag, message)
_level = "INFO"
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}

# ── Redaction ─────────────────────────────────────────────────────────────────
# Key=value shapes first (keep the key, kill the value), then bare
# high-entropy tokens that appear near secret-ish words.
_REDACT_PATTERNS: list[tuple[re.Pattern, str]] = [
    # api_key="...", 'token': '...', password=..., etc. — value fully replaced.
    (re.compile(r'''(?i)\b(api[_-]?key|apikey|auth[_-]?token|access[_-]?token|'''
                r'''refresh[_-]?token|token|bearer|client[_-]?secret|password|passwd|'''
                r'''pwd|secret|private[_-]?key|session[_-]?key)\b['"]?\s*'''
                r'''[:=]\s*['"]?([^'"\s,}]{3,})['"]?'''),
     r"\1=[REDACTED]"),
    # Authorization: Bearer <token>
    (re.compile(r'''(?i)\b(authorization\s*:\s*bearer\s+)([A-Za-z0-9\-._~+/=]{8,})'''),
     r"\1[REDACTED]"),
    # sk-... style keys and long hex/base64 blobs standing alone
    (re.compile(r'''\bsk-[A-Za-z0-9\-_]{8,}\b'''), "[REDACTED]"),
    (re.compile(r'''\bAIza[A-Za-z0-9\-_]{20,}\b'''), "[REDACTED]"),
]

# A bare "key=..." fragment we did NOT recognize as a secret label still gets
# masked when the value looks like a credential (long, high-entropy).
_SUSPICIOUS_VALUE = re.compile(r"^[A-Za-z0-9\-_+/=]{24,}$")


def redact(text: Any) -> str:
    """Scrub secrets from anything about to be logged. Never raises."""
    try:
        out = str(text)
    except Exception:
        return "[unprintable]"
    for pattern, repl in _REDACT_PATTERNS:
        try:
            out = pattern.sub(repl, out)
        except Exception:
            pass
    # Last resort: mask suspicious lone tokens (but keep short readable words).
    def _mask(m: re.Match) -> str:
        token = m.group(0)
        if _SUSPICIOUS_VALUE.match(token) and len(set(token)) > 8:
            return "[REDACTED]"
        return token
    try:
        out = re.sub(r"\b[A-Za-z0-9\-_+/=]{32,}\b", _mask, out)
    except Exception:
        pass
    return out


# ── Emission ──────────────────────────────────────────────────────────────────
def _emit(tag: str, message: str = "", *, level: str = "INFO") -> None:
    if _LEVELS.get(level, 20) < _LEVELS.get(_level, 20):
        return
    line = f"{_PREFIX} {tag}" + (f" {redact(message)}" if message else "")
    with _lock:
        print(line, file=sys.stderr if level == "ERROR" else sys.stdout,
              flush=True)
        for fn in list(_handlers):
            try:
                fn(tag, redact(message))
            except Exception:
                pass


def add_handler(fn) -> None:
    """Add a sink fn(tag, redacted_message) — e.g. the HUD log widget."""
    with _lock:
        _handlers.append(fn)


def set_level(name: str) -> None:
    global _level
    if name in _LEVELS:
        _level = name


# ── The tag API (mission spec, verbatim) ──────────────────────────────────────
def listening(msg: str = "") -> None:   _emit("🎤 Listening...", msg)
def speaking(msg: str = "") -> None:    _emit("🔊 Speaking...", msg)
def thinking(msg: str = "") -> None:    _emit("🧠 Thinking...", msg)
def searching(msg: str = "") -> None:   _emit("🌐 Searching...", msg)
def executing(msg: str = "") -> None:   _emit("🖥️ Executing...", msg)
def file_op(msg: str = "") -> None:      _emit("📁 File operation...", msg)
def system(msg: str = "") -> None:       _emit("⚙️ System...", msg)
def device(msg: str = "") -> None:       _emit("🔌 Device...", msg)
def completed(msg: str = "") -> None:    _emit("✅ Completed", msg)
def warn(where: str, msg: str = "") -> None:
    _emit("⚠️ Warning", f"[{where}] {msg}" if where else msg, level="WARNING")
def error(where: str, msg: str = "") -> None:
    _emit("❌ Error", f"[{where}] {msg}" if where else msg, level="ERROR")
def info(msg: str = "") -> None:         _emit("ℹ️", msg)
def debug(msg: str = "") -> None:        _emit("🔍", msg, level="DEBUG")


def timed(tag_fn, what: str):
    """Context manager: logs start/done with elapsed ms.

        with logger.timed(logger.thinking, "web_search"):
            ...
    """
    class _T:
        def __enter__(self):
            self._t0 = time.monotonic()
            tag_fn(f"{what} — started")
            return self
        def __exit__(self, *exc):
            ms = int((time.monotonic() - self._t0) * 1000)
            (error if exc[0] else completed)(f"{what} — {ms}ms")
            return False
    return _T()
