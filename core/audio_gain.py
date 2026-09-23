"""core/audio_gain.py — software mic gain and master volume (Phase 5).

Pure, headless-testable DSP for the audio panel:

* Mic gain 50%–200% applied to PCM *input*, with soft clipping so a hot
  microphone at 200% saturates gracefully instead of wrapping into digital
  hash.
* Master speaker volume 0%–100% applied to playback buffers (a straight
  linear scale; 100% is bit-transparent).

Both operate on int16 PCM and return int16 PCM, so main.py can apply them
inline in the existing audio callbacks without changing dtypes anywhere.

Soft clipping: a tanh knee. Below ~0.7 of full scale it is near-linear (the
voice is untouched); above it the curve compresses toward full scale, so
overload sounds like saturation rather than a hard digital clip. Honest
note: this cannot recover a signal that already clipped at the ADC — it only
keeps the software gain stage from adding a second, nastier clip.
"""

from __future__ import annotations

import math

import numpy as np

MIN_MIC_GAIN = 50      # percent
MAX_MIC_GAIN = 200     # percent
DEFAULT_MIC_GAIN = 100
MIN_MASTER_VOL = 0     # percent
MAX_MASTER_VOL = 100   # percent
DEFAULT_MASTER_VOL = 100

_INT16_MAX = 32767.0


def clamp_gain(percent: float) -> float:
    """Clamp a mic-gain percentage into the supported 50–200 range."""
    try:
        p = float(percent)
    except (TypeError, ValueError):
        return float(DEFAULT_MIC_GAIN)
    return float(min(MAX_MIC_GAIN, max(MIN_MIC_GAIN, p)))


def clamp_volume(percent: float) -> float:
    """Clamp a master-volume percentage into 0–100."""
    try:
        p = float(percent)
    except (TypeError, ValueError):
        return float(DEFAULT_MASTER_VOL)
    return float(min(MAX_MASTER_VOL, max(MIN_MASTER_VOL, p)))


def percent_to_db(percent: float) -> float:
    """Gain percentage → dB relative to unity (100% = 0 dB)."""
    p = max(1e-6, float(percent))
    return 20.0 * math.log10(p / 100.0)


def _soft_clip(x: np.ndarray) -> np.ndarray:
    """tanh knee at ~0.7 of full scale; near-linear below it."""
    return np.tanh(x * 1.35) / np.tanh(1.35)


def apply_mic_gain(pcm, gain_percent: float):
    """Apply mic gain (50–200%) to int16 PCM with soft clipping.

    Returns int16 PCM. Never raises — on bad input the block passes through
    unchanged.
    """
    try:
        gain = clamp_gain(gain_percent) / 100.0
        x = np.asarray(pcm, dtype=np.int16)
        if x.size == 0:
            return x
        f = x.astype(np.float32) / _INT16_MAX * gain
        # Only engage the knee where the amplified signal would exceed ~0.7
        # of full scale — quiet passages stay bit-linear.
        over = np.abs(f) > 0.7
        if np.any(over):
            # Indexed assignment: only the overloaded samples pass through
            # the knee. (np.where with a masked 1-D slice against the full
            # array broadcast-failed when only some samples were over.)
            f[over] = _soft_clip(f[over])
        f = np.clip(f, -1.0, 1.0)
        return (f * _INT16_MAX).astype(np.int16)
    except Exception:
        return pcm


def apply_master_volume(pcm, volume_percent: float):
    """Scale playback int16 PCM by 0–100%. 100% is bit-transparent.

    Returns int16 PCM. Never raises.
    """
    try:
        vol = clamp_volume(volume_percent) / 100.0
        x = np.asarray(pcm, dtype=np.int16)
        if x.size == 0 or vol == 1.0:
            return x
        f = np.clip(x.astype(np.float32) / _INT16_MAX * vol, -1.0, 1.0)
        return (f * _INT16_MAX).astype(np.int16)
    except Exception:
        return pcm


def peak_db(pcm) -> float:
    """Peak level of an int16 block in dBFS (−inf for digital silence)."""
    try:
        x = np.asarray(pcm, dtype=np.float32)
        if x.size == 0:
            return float("-inf")
        peak = float(np.max(np.abs(x))) / _INT16_MAX
        if peak <= 0:
            return float("-inf")
        return 20.0 * math.log10(peak)
    except Exception:
        return float("-inf")


def is_clipping(pcm, threshold_db: float = -0.5) -> bool:
    """True if the block's peak is within `threshold_db` of full scale."""
    return peak_db(pcm) >= threshold_db
