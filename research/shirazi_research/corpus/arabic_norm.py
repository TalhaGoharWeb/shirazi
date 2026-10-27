"""Shared Arabic text normalization for the Shirazi corpus layer.

Single source of truth: this module re-exports
:mod:`shirazi_research.textnorm` so the FTS5 index, the query path, the
retrieval reranker and the quote audit ALL normalize identically. Do not
add a second normalizer here.

Normalization (applied identically at index time and query time):
  NFKC, strip tashkeel/tatweel, أ/إ/آ/ٱ → ا, ؤ → و, ئ → ي, ى → ي, ة → ه,
  collapse whitespace.
"""

from __future__ import annotations

from shirazi_research.textnorm import (
    arabic_dominant,
    normalize_arabic,
    strip_tashkeel,
)

# Backwards-friendly alias used across the corpus layer.
is_arabic_dominant = arabic_dominant


def tokenize_arabic(text: str) -> list[str]:
    """Split normalized text into search tokens.

    Normalizes first (via the shared :func:`normalize_arabic`), then splits
    on whitespace and strips surrounding punctuation, keeping Python-side
    phrase construction consistent with the FTS5 ``unicode61`` tokenizer.
    """
    norm = normalize_arabic(text)
    tokens = []
    for tok in norm.split():
        tok = tok.strip("،؛؟«»\"'()[]{}:ـ.,!?-–—…")
        if tok:
            tokens.append(tok)
    return tokens


__all__ = [
    "normalize_arabic",
    "strip_tashkeel",
    "tokenize_arabic",
    "is_arabic_dominant",
    "arabic_dominant",
]
