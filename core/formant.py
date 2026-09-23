"""core/formant.py — language-independent formant lip-sync (Phase 5).

Why formants
------------
Formant analysis of the audio being *played* gives excellent timing and a
decent read on vowels with no transcript, no forced alignment and no language
assumption: it works the same for English, Urdu and Arabic because it is
physics, not phonetics. (Moved out of main.py so it is unit-testable; main.py
now delegates to `analyze_frames`.)

The method: every ~20 ms, take the spectrum of a ~43 ms window and compare
band energies.

* Openness tracks the first formant — F1 climbs as the jaw drops, so /a/
  reads open and /i/, /u/ read closed.
* Width tracks the second formant — F2 is high for spread vowels (/i/, /e/)
  and low for rounded ones (/u/, /o/). A wide-open jaw physically cannot
  purse, so openness damps width (keeps /a/ from reading as rounded).
* Fricatives (/s/, /f/) are formed with a nearly closed mouth, so a strong
  high-frequency hiss band closes the jaw a little.

Output: one (level, openness, width) frame per hop. `level` is 0.0–1.0
loudness, `openness` 0.0–1.0 (jaw drop), `width` −1.0 (pursed) … +1.0
(spread).

Graceful degradation: anything unexpected (empty block, silence, an
exception) yields silence frames or [] — the mouth falls back to
loudness-only articulation rather than the caller handling an error.
"""

from __future__ import annotations

import numpy as np

# RMS below which 16-bit PCM is treated as room silence; above _LEVEL_FULL it
# reads as a full-height waveform. Tuned so ordinary speech lands mid-range
# and the bars still move for a quiet talker — language- and device-independent.
_LEVEL_FLOOR = 60.0
_LEVEL_FULL = 2600.0

_VIS_WIN = 1024        # ~43 ms analysis window at 24 kHz: enough for formants
_VIS_HOP = 480         # 20 ms between frames, i.e. 50 shapes a second


def pcm_level(samples) -> float:
    """Map a block of int16 PCM samples to a 0.0–1.0 loudness level.

    Returns 0.0 on empty/invalid input so it can never raise.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size == 0:
            return 0.0
        rms = float(np.sqrt(np.mean(x * x)))
    except Exception:
        return 0.0
    if rms <= _LEVEL_FLOOR:
        return 0.0
    return min(1.0, (rms - _LEVEL_FLOOR) / (_LEVEL_FULL - _LEVEL_FLOOR))


def analyze_frames(samples, sr: int = 24000, hop: int = _VIS_HOP):
    """Slice a PCM block into (level, openness, width) frames, one per hop.

    `sr` is the sample rate of `samples`; `hop` is in samples (480 at 24 kHz
    = 20 ms). Returns [] on anything unexpected — the caller falls back to
    loudness-only articulation.
    """
    try:
        x = np.asarray(samples, dtype=np.float32)
        if x.size < _VIS_WIN:
            return []
        win = np.hanning(_VIS_WIN).astype(np.float32)
        freqs = np.fft.rfftfreq(_VIS_WIN, 1.0 / sr)
        b_f1_lo = (freqs >= 150) & (freqs < 450)     # F1 of close vowels
        b_f1_hi = (freqs >= 450) & (freqs < 1100)    # F1 of open vowels
        b_f2_bk = (freqs >= 600) & (freqs < 1300)    # F2 of rounded vowels
        b_f2_fr = (freqs >= 1700) & (freqs < 3200)   # F2 of spread vowels
        b_hiss = (freqs >= 3800) & (freqs < 8000)    # fricatives

        # One frame per hop across the *whole* block. Stepping only while a
        # full window fits stopped VIS_WIN − hop samples short of the end, so
        # a 200 ms batch yielded 160 ms of schedule and the mouth ran out of
        # frames before the audio ran out of sound.
        out = []
        for start in range(0, x.size, hop):
            # The level gates closures, so it is measured over exactly this hop
            # and never looks ahead. The spectrum needs a longer window to
            # resolve formants and may be short-filled at the very end.
            level = pcm_level(x[start:start + hop])
            seg = x[start:start + _VIS_WIN]
            if seg.size < _VIS_WIN:
                seg = np.concatenate([seg, np.zeros(_VIS_WIN - seg.size,
                                                    dtype=np.float32)])
            if level <= 0.0:
                out.append((0.0, 0.0, 0.0))
                continue
            mag = np.abs(np.fft.rfft((seg - seg.mean()) * win))
            f1l, f1h = float(mag[b_f1_lo].sum()), float(mag[b_f1_hi].sum())
            f2b, f2f = float(mag[b_f2_bk].sum()), float(mag[b_f2_fr].sum())
            hiss = float(mag[b_hiss].sum())

            openness = f1h / (f1l + f1h + 1e-6)
            width = (f2f - f2b) / (f2f + f2b + 1e-6)
            # A wide-open jaw physically cannot purse, so openness damps width.
            # /a/ has a low enough F2 to read as "rounded" on the bands alone;
            # letting openness suppress the width term is what keeps an open
            # vowel from pursing.
            width *= (1.0 - openness) ** 0.8
            # Fricatives are formed with a nearly closed mouth.
            h = hiss / (f1l + f1h + f2b + f2f + hiss + 1e-6)
            openness *= 1.0 - 0.65 * min(1.0, h * 2.5)
            out.append((level,
                        float(min(1.0, max(0.0, openness))),
                        float(min(1.0, max(-1.0, width)))))
        return out
    except Exception:
        return []


def frame_rate_per_sec(sr: int = 24000, hop: int = _VIS_HOP) -> float:
    """Frames per second produced by analyze_frames at this rate/hop (~50)."""
    return sr / hop
