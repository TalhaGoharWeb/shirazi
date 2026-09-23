"""i18n/ — Shirazi internationalization (Phase 4).

Four languages: English (en), Urdu (ur), Arabic (ar), Roman Urdu (ur-Latn).
Urdu and Arabic are right-to-left.

    from i18n import t, set_language, is_rtl
    set_language("ur")
    t("listening")          # "سن رہا ہے..."
    t("confirm_title", tool="browser")  # placeholders via {name}

Phase 4 delivers: the loader, the four catalogs, RTL helpers, and the
language plumbing (config "language" key). Existing UI strings are NOT
extracted here — that is Phase 5's job (see i18n/EXTRACTION_PLAN.md).
New code must not hard-code user-facing strings: use t().
"""

import json
import threading
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent

SUPPORTED = ("en", "ur", "ar", "ur-Latn")
RTL_LANGUAGES = ("ur", "ar")
DEFAULT = "en"

_lock = threading.RLock()
_current = DEFAULT
_catalogs: dict[str, dict] = {}


def _load(lang: str) -> dict:
    with _lock:
        if lang in _catalogs:
            return _catalogs[lang]
    try:
        data = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        strings = data.get("strings") or {}
    except Exception:
        strings = {}
    with _lock:
        _catalogs[lang] = strings
    return strings


def set_language(lang: str) -> str:
    """Set the active language; unknown codes fall back to English.
    Returns the effective language."""
    global _current
    lang = lang if lang in SUPPORTED else DEFAULT
    _load(lang)
    with _lock:
        _current = lang
    return lang


def current_language() -> str:
    with _lock:
        return _current


def is_rtl(lang: str | None = None) -> bool:
    """True for Urdu/Arabic. Qt: set layoutDirection accordingly."""
    code = lang or current_language()
    return code in RTL_LANGUAGES


def rtl_mark(text: str, lang: str | None = None) -> str:
    """Wrap text with Unicode bidi marks when the language is RTL, so mixed
    LTR/RTL strings (e.g. 'Shirazi نے file کھولی') render in the right order
    in labels, logs and the phone UI."""
    if not is_rtl(lang):
        return text
    return "\u2067" + text + "\u2069"  # RLI ... PDI


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate a key. Falls back: requested language → English → the key
    itself. `{placeholders}` are filled from kwargs; a missing placeholder
    never raises."""
    code = lang or current_language()
    text = _load(code).get(key)
    if text is None and code != DEFAULT:
        text = _load(DEFAULT).get(key)
    if text is None:
        return key
    try:
        return str(text).format(**kwargs)
    except Exception:
        return str(text)


def available() -> list[str]:
    return list(SUPPORTED)
