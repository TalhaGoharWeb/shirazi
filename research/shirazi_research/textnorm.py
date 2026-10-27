"""Shared Arabic text normalization.

Single source of truth for normalization used by retrieval, quote audit,
title matching and the FTS5 index (the corpus indexer must apply the same
function at index time — see corpus/arabic_norm.py which re-exports this).
"""
from __future__ import annotations

import re
import unicodedata

_TASHKEEL = re.compile(r"[\u064B-\u0652\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8]")
_ALEF = re.compile(r"[إأآٱ]")


def _hamza_fix(s: str) -> str:
    # ؤ->و, ئ->ي : better for search recall; ء stays
    return s.replace("ؤ", "و").replace("ئ", "ي")
_TATWEEL = "\u0640"


def strip_tashkeel(s: str) -> str:
    return _TASHKEEL.sub("", s)


def normalize_arabic(s: str) -> str:
    """Normalize for comparison/search: NFKC, no tashkeel/tatweel, unified alef/hamza/ya/ta."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = _TASHKEEL.sub("", s)
    s = s.replace(_TATWEEL, "")
    s = _ALEF.sub("ا", s)
    s = _hamza_fix(s)
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Urdu-specific letters: if present, the text is Urdu (not Arabic),
# even though both use the Arabic script block.
URDU_ONLY = set("ٹڈڑںھےۂ")


def has_urdu_chars(s: str) -> bool:
    return any(c in URDU_ONLY for c in s)


def arabic_dominant(s: str) -> bool:
    """True if the majority of letters are Arabic."""
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    ar = sum(1 for c in letters if "\u0600" <= c <= "\u06FF")
    return ar / len(letters) >= 0.5
