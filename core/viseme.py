"""
Text → mouth shape, fused with the audio the avatar is actually speaking.

Why both sources
----------------
Formant analysis of the audio (see `_pcm_visemes` in main.py) gives excellent
*timing* and a decent read on vowels, but it is blind to exactly the consonants
lip-reading depends on. /m/, /b/ and /p/ are made with the lips pressed shut,
and nothing in the spectrum reliably says "the lips are closed" — a nasal /m/
and a nasal /n/ look nearly identical to a filter bank while looking completely
different on a face.

The transcript knows those consonants for certain. So the text supplies *which
shape*, the audio supplies *when* and *how strongly*, and the two are blended.
If the transcript is late or missing the mouth silently falls back to the
audio-only shape, which is what the previous version did on its own.

Language independence
---------------------
There is no per-language table here. Every character is reduced to one of the
26 bare Latin letters — by Unicode decomposition for accents, by transliteration
for Cyrillic and Greek — and articulation is looked up on that. So Turkish,
English, German, French, Spanish, Polish, Vietnamese, Russian, Ukrainian and
Greek all work from the same twenty-odd rules, and adding a language costs
nothing because there is nothing to add.

Scripts whose spelling does not reveal pronunciation (CJK, Arabic, Devanagari,
Hebrew, Thai) are detected by coverage and skipped, and the mouth runs on the
audio-only shape — which is itself language-independent, being physics. The
result is never wrong, only less detailed.
"""

from __future__ import annotations

import unicodedata
from collections import deque

# (openness 0..1, width -1..+1, closure 0..1)
#   closure forces the lips together regardless of loudness — it is the whole
#   reason the transcript is worth consulting.
VISEMES: dict[str, tuple[float, float, float]] = {
    "REST": (0.00, 0.00, 0.00),
    "AA": (0.92, -0.05, 0.00),   # a
    "E":  (0.52, 0.42, 0.00),    # e
    "I":  (0.20, 0.62, 0.00),    # i, ı
    "O":  (0.55, -0.52, 0.00),   # o, ö
    "U":  (0.26, -0.74, 0.00),   # u, ü, w
    "MBP": (0.00, 0.00, 1.00),   # m, b, p — lips pressed shut
    "FV": (0.10, 0.22, 0.55),    # f, v — lower lip to the teeth
    "S":  (0.16, 0.42, 0.00),    # s, ş, z, c, ç, j
    "L":  (0.36, 0.18, 0.00),    # l
    "TD": (0.28, 0.12, 0.00),    # t, d, n
    "K":  (0.30, -0.04, 0.00),   # k, g, ğ, h
    "R":  (0.28, -0.16, 0.00),   # r
}

# Relative duration of each class. Vowels carry the syllable; plosives are a tap.
_DUR = {"REST": 1.0, "AA": 1.15, "E": 1.05, "I": 1.0, "O": 1.1, "U": 1.05,
        "MBP": 0.5, "FV": 0.8, "S": 0.9, "L": 0.65, "TD": 0.5, "K": 0.55,
        "R": 0.55}

# Articulation is a property of the *sound*, not of a language, so the table is
# keyed on the 26 bare Latin letters and every script reaches it by reduction:
#   * diacritics are stripped by Unicode decomposition (é→e, ü→u, ế→e, ł→l …),
#     which covers every Latin-script language at once rather than one at a time;
#   * letters that do not decompose get a short explicit entry below;
#   * Cyrillic and Greek transliterate into the same 26 letters.
# Anything else — CJK, Arabic, Devanagari, Hebrew, Thai — is not derivable from
# its written form without a pronunciation dictionary, so those simply fall back
# to the audio-only mouth. That is a clean degradation, not a missing language.
_LETTER = {
    "a": "AA",
    "e": "E",
    "i": "I", "y": "I",
    "o": "O",
    "u": "U", "w": "U",
    "b": "MBP", "p": "MBP", "m": "MBP",
    "f": "FV", "v": "FV",
    "s": "S", "z": "S", "c": "S", "j": "S", "x": "S",
    "l": "L",
    "t": "TD", "d": "TD", "n": "TD",
    "k": "K", "g": "K", "h": "K", "q": "K",
    "r": "R",
}

# Letters with no Unicode decomposition into a Latin base.
_UNDECOMPOSED = {
    "ı": "i", "ø": "o", "đ": "d", "ħ": "h", "ŀ": "l", "ŧ": "t",
    "ß": "s", "æ": "a", "œ": "o", "þ": "t", "ð": "d", "ŋ": "n",
    "ł": "l",
}

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "j", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "s", "ч": "s", "ш": "s", "щ": "s", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "u", "я": "a",
    "і": "i", "ї": "i", "є": "e", "ґ": "g", "ў": "u",
}

_GREEK = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "t", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "s",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "i",
    "φ": "f", "χ": "h", "ψ": "s", "ω": "o",
}


# ── Arabic script (Phase 5 improvement) ──────────────────────────────────────
#
# Urdu and Arabic are written in the Arabic script, which the old coverage rule
# punted on entirely ("CJK, Arabic, Devanagari, Hebrew, Thai → audio-only").
# That was honest but left the mouth under-articulated for two of Shirazi's
# three core languages. The improvement below is deliberately narrow:
#
#   * It is per-LETTER articulation, not a pronunciation dictionary. Each
#     letter maps to the Latin sound made at the same place of articulation
#     (م is a bilabial nasal exactly like m → MBP; ف is labiodental like f).
#     That is linguistics, not vocabulary, so it stays language-independent.
#   * Urdu-specific letters are included (پ چ ٹ ڈ ڑ ژ گ ں ھ ے ہ ئ ؤ).
#   * The vowel diacritics (harakat: fatha/kasra/damma) map to a/i/u.
#
# Honest limitations (documented, not hidden):
#   * Short vowels are usually UNWRITTEN in Arabic/Urdu prose, so a word like
#     "كتب" reads k-t-b with no vowel shapes between — the mouth still relies
#     on the audio stream (core/formant.py) for the vowels, and the text
#     supplies the consonant closures (the MBP/FV/TD shapes audio alone can
#     never see). The two sources were designed to fuse exactly this way.
#   * Dialect variation is approximated: ج is /dʒ/ in most Urdu/Arabic but /g/
#     in Egyptian Arabic — it maps to S (the common case).
#   * ء (hamza, glottal stop) reads as a brief REST — a catch in the voice,
#     which is what a closed glottis looks like.
_ARABIC = {
    # Long vowels / matres lectionis
    "ا": "a", "آ": "a", "أ": "a", "إ": "i", "ٱ": "a",
    "و": "u", "ؤ": "u",
    "ی": "i", "ي": "i", "ى": "a", "ئ": "i", "ے": "e",
    # Bilabials → MBP (lips pressed shut — the shape audio cannot see)
    "ب": "b", "پ": "p", "م": "m",
    # Labiodental → FV
    "ف": "f",
    # Dentals/alveolars → TD / S / L / R by manner
    "ت": "t", "ٹ": "t", "ط": "t",
    "د": "d", "ڈ": "d", "ض": "d",
    "ث": "s", "س": "s", "ص": "s",
    "ذ": "z", "ز": "z", "ظ": "z", "ژ": "j",
    "ن": "n", "ں": "n",
    "ل": "l",
    "ر": "r", "ڑ": "r",
    # Sibilants/affricates → S
    "ج": "j", "چ": "s", "ش": "s",
    # Velars/glottals → K
    "ک": "k", "ك": "k", "گ": "g", "ق": "k",
    "خ": "h", "ح": "h", "ہ": "h", "ه": "h", "ھ": "h",
    # Pharyngeals — ع is a voiced pharyngeal approximant; nearest mouth
    # shape is an open vowel, غ is a voiced velar fricative → K
    "ع": "a", "غ": "g",
    # Hamza — glottal stop: a catch, rendered as a beat of REST
    "ء": "",
    # Harakat (short-vowel diacritics) — when present they are exact
    "َ": "a", "ِ": "i", "ُ": "u",
    "ً": "n", "ٍ": "n", "ٌ": "n",
    "ْ": "", "ّ": "", "ٰ": "a", "ٓ": "a", "ٔ": "",
    # Urdu/Persian extras
    "ۂ": "h", "ۃ": "t",
}

# Below this share of mappable letters the text is in a script we cannot read
# phonetically, and forcing shapes onto it would be worse than not trying.
_MIN_COVERAGE = 0.55

# English spellings that do not survive letter-by-letter reading.
_DIGRAPH = {
    "sh": "S", "ch": "S", "ts": "S",
    "th": "TD", "ck": "K", "ng": "K", "gh": "K",
    "ph": "FV",
    "oo": "U", "ou": "O", "ow": "O", "wh": "U",
    "ee": "I", "ea": "I", "ie": "I",
    "qu": "K",
}

_PAUSE = set(".,;:!?…\n")


def to_latin(ch: str) -> str:
    """Reduce any character to a bare Latin letter, or "" if it has none.

    This is what makes the mouth language-agnostic: one reduction step replaces
    a per-language spelling table.
    """
    c = ch.lower()
    if "a" <= c <= "z":
        return c
    if c in _UNDECOMPOSED:
        return _UNDECOMPOSED[c]
    if c in _CYRILLIC:
        return _CYRILLIC[c]
    if c in _GREEK:
        return _GREEK[c]
    if c in _ARABIC:
        return _ARABIC[c]
    # Strip combining marks: é→e, ü→u, ş→s, ğ→g, ế→e, ñ→n, å→a …
    base = "".join(k for k in unicodedata.normalize("NFD", c)
                   if not unicodedata.combining(k))
    if len(base) == 1 and "a" <= base <= "z":
        return base
    if base and base != c:                     # e.g. ﬁ → fi, take the first
        return to_latin(base[0])
    return ""


def coverage(text: str) -> float:
    """Fraction of the letters in `text` we can reduce to a Latin sound."""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if to_latin(c)) / len(letters)


def text_to_visemes(text: str) -> list[tuple[str, float]]:
    """Split a line of speech into (viseme, duration-weight) pairs.

    Returns [] for scripts whose written form does not reveal pronunciation, so
    the caller falls back to the audio-only mouth instead of miming nonsense.
    """
    s = (text or "").lower()
    if coverage(s) < _MIN_COVERAGE:
        return []

    out: list[tuple[str, float]] = []
    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        if ch in _PAUSE:
            out.append(("REST", 1.4))
            i += 1
            continue
        if ch.isspace():
            # A word gap is a beat, not a closed mouth — closing between every
            # word makes the avatar look like it is chewing.
            if out and out[-1][0] != "REST":
                out.append((out[-1][0], 0.35))
            i += 1
            continue

        # Digraphs are an orthographic quirk of Latin spelling; check them on
        # the reduced letters so "SCH"/"Sch" and accented forms match too.
        two = to_latin(ch) + (to_latin(s[i + 1]) if i + 1 < n else "")
        if len(two) == 2 and two in _DIGRAPH:
            v = _DIGRAPH[two]
            i += 2
        else:
            base = to_latin(ch)
            i += 1
            if not base:
                continue
            v = _LETTER.get(base)
            if v is None:
                continue
        # A doubled letter is one sound in every orthography we handle here.
        if out and out[-1][0] == v:
            continue
        out.append((v, _DUR[v]))
    return out


class VisemeStream:
    """Fuses the transcript's shape sequence onto the audio's timing.

    Thread note: `feed_text` runs on the receive coroutine and `frames` on the
    playback coroutine. Both live in the same asyncio loop, and `deque` append
    and popleft are atomic, so no lock is needed.
    """

    # Seconds a phoneme occupies at a normal speaking rate. The clock adapts
    # between these when the queue runs long (the model is talking fast) or
    # short (it is trailing off).
    _MIN_STEP = 0.045
    _MAX_STEP = 0.105

    def __init__(self) -> None:
        self._q: deque[tuple[str, float]] = deque()
        self._cur = ("REST", 1.0)
        self._carry = 0.0

    def reset(self) -> None:
        self._q.clear()
        self._cur = ("REST", 1.0)
        self._carry = 0.0

    def feed_text(self, text: str) -> None:
        for item in text_to_visemes(text):
            self._q.append(item)
        # Never let a stalled turn pile up an unbounded backlog.
        while len(self._q) > 600:
            self._q.popleft()

    @property
    def pending(self) -> int:
        return len(self._q)

    def _step_seconds(self) -> float:
        # A long backlog means speech is outrunning the clock; shorten the step
        # so the mouth catches up instead of drifting further behind the voice.
        backlog = min(1.0, len(self._q) / 45.0)
        return self._MAX_STEP - (self._MAX_STEP - self._MIN_STEP) * backlog

    def frames(self, audio, hop: float):
        """Blend audio frames [(level, openness, width)] with the text queue."""
        out = []
        for level, a_open, a_wide in audio:
            if level <= 0.0:
                # Silence: let the queue wait rather than burning through it
                # during a pause, or the mouth ends up ahead of the voice.
                out.append((0.0, 0.0, 0.0))
                continue

            self._carry += hop / max(1e-3, self._step_seconds() * self._cur[1])
            while self._carry >= 1.0 and self._q:
                self._cur = self._q.popleft()
                self._carry -= 1.0
            if self._carry >= 1.0:
                self._carry = 1.0          # queue empty — hold the last shape

            t_open, t_wide, closure = VISEMES.get(self._cur[0], VISEMES["REST"])
            if self._q or self._cur[0] != "REST":
                # Text leads the shape; the audio keeps it honest so a bad
                # transcript alignment still tracks the real voice.
                o = 0.72 * t_open + 0.28 * a_open
                w = 0.78 * t_wide + 0.22 * a_wide
            else:
                o, w = a_open, a_wide
                closure = 0.0
            o *= 1.0 - closure
            out.append((level, max(0.0, min(1.0, o)), max(-1.0, min(1.0, w))))
        return out

# ── Formant → expressive viseme (Phase 5) ───────────────────────────────────
#
# core/formant.py yields continuous (openness, width) per 20 ms from the audio
# spectrum. This maps that point to the nearest *expressive* VISEMES key so
# renderers that want a labelled shape (the HUD mouth, debug overlays) get one.
# Distance is weighted: openness (jaw/F1) dominates because it is the more
# reliable cue; width (F2) breaks ties between e.g. E and I.
#
# Special cases:
#   * level ≈ 0 → REST (silence is a shape, not a failure).
#   * closure heuristic: voiced energy with near-zero openness means the lips
#     are pressed together but the audio cannot say why — report MBP (the
#     bilabial closure) rather than a vowel, because a closed mouth is the
#     only honest reading of "sound with no jaw opening".

def formant_to_viseme(openness: float, width: float, level: float = 1.0) -> str:
    """Map a (openness 0..1, width −1..+1) formant frame to a VISEMES key."""
    try:
        o = float(min(1.0, max(0.0, openness)))
        w = float(min(1.0, max(-1.0, width)))
        lvl = float(level)
    except (TypeError, ValueError):
        return "REST"
    if lvl <= 0.0:
        return "REST"
    if o < 0.08:
        # Sound with (almost) no jaw opening: lips pressed shut.
        return "MBP"
    best, best_d = "REST", float("inf")
    for key, (ko, kw, closure) in VISEMES.items():
        if key == "REST" or closure > 0.5:
            continue
        d = (1.6 * (o - ko)) ** 2 + (0.9 * (w - kw)) ** 2
        if d < best_d:
            best, best_d = key, d
    return best
