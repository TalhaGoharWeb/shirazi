"""core/reactor.py — the reactor core's *model* (Phase 5).

This module contains no Qt and no drawing. It turns (audio, AI state, time)
into the parameters a renderer needs: a 72-point waveform ring, 12 energy
spokes, particle speeds, sphere scale, glow, sweep angle, iris, and the
telemetry readouts. The Qt widget (ui_reactor.py) paints from these numbers,
so the whole visualisation is unit-testable headless.

Design contract:
  * `compute()` is a pure function of its inputs — same inputs, same params.
  * Audio drives amplitude: waveform ring height, spoke length, sphere scale,
    particle speed and glow intensity all scale with the live level.
  * AI state biases the visualisation: THINKING quickens the sweep,
    SPEAKING widens the iris, ERROR warms the palette weight, OFFLINE damps
    everything to a slow breath.
  * Time only enters as an angle/phase — the renderer owns the clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from core.avatar_state import AvatarState

# Rendered element counts — these are the mission spec's numbers.
WAVEFORM_POINTS = 72
SPOKES = 12
PARTICLES = 90


class RenderMode(str, Enum):
    REALISTIC_3D = "realistic"
    HOLOGRAM     = "hologram"
    REACTOR      = "reactor"


@dataclass
class ReactorParams:
    """Everything a renderer needs for one frame of the reactor core."""
    waveform_ring: list = field(default_factory=list)  # 72 floats, 0..1
    spokes: list = field(default_factory=list)         # 12 floats, 0..1
    particle_speed: float = 0.0                        # rad/s scale factor
    particle_phase: float = 0.0                        # radians, = f(t)
    sphere_scale: float = 1.0                          # 1.0 = rest size
    glow: float = 0.5                                  # 0..1
    sweep_angle: float = 0.0                           # radians, = f(t)
    iris: float = 0.5                                  # segmented iris 0..1
    chromatic: float = 0.4                             # vignette strength 0..1
    arc_integrity: float = 1.0                         # 0..1 telemetry
    flux: float = 0.0                                  # 0..1 telemetry
    mode_label: str = "IDLE"
    audio_label: str = "0.0"
    latency_ms: float = 0.0


# Per-state bias. sweep_mult speeds the scanning sweep, iris_bias opens the
# segmented iris, glow_bias lifts the whole core, particle_mult scales the
# orbiting particles, damp scales everything toward the rest pose.
_STATE_BIAS = {
    AvatarState.IDLE:      {"sweep_mult": 1.0, "iris_bias": 0.00,
                            "glow_bias": 0.00, "particle_mult": 0.6, "damp": 0.55},
    AvatarState.LISTENING: {"sweep_mult": 1.6, "iris_bias": 0.15,
                            "glow_bias": 0.10, "particle_mult": 1.0, "damp": 0.85},
    AvatarState.THINKING:  {"sweep_mult": 3.2, "iris_bias": 0.30,
                            "glow_bias": 0.05, "particle_mult": 1.4, "damp": 0.80},
    AvatarState.SPEAKING:  {"sweep_mult": 1.2, "iris_bias": 0.35,
                            "glow_bias": 0.20, "particle_mult": 1.1, "damp": 1.00},
    AvatarState.EXECUTING: {"sweep_mult": 2.4, "iris_bias": 0.20,
                            "glow_bias": 0.10, "particle_mult": 1.2, "damp": 0.90},
    AvatarState.SUCCESS:   {"sweep_mult": 0.8, "iris_bias": 0.50,
                            "glow_bias": 0.30, "particle_mult": 0.8, "damp": 1.00},
    AvatarState.ERROR:     {"sweep_mult": 0.4, "iris_bias": -0.10,
                            "glow_bias": 0.00, "particle_mult": 0.3, "damp": 0.45},
    AvatarState.OFFLINE:   {"sweep_mult": 0.25, "iris_bias": -0.20,
                            "glow_bias": -0.25, "particle_mult": 0.15, "damp": 0.25},
}

_SWEEP_BASE_HZ = 0.35   # one scan every ~3 s at rest


def _band_energies(pcm, sr: int, n: int = WAVEFORM_POINTS) -> list[float]:
    """Log-spaced spectral band energies, normalised 0..1, for the ring."""
    try:
        x = np.asarray(pcm, dtype=np.float32)
        if x.size < 64:
            return [0.0] * n
        mag = np.abs(np.fft.rfft((x - x.mean())
                                 * np.hanning(x.size).astype(np.float32)))
        freqs = np.fft.rfftfreq(x.size, 1.0 / sr)
        lo, hi = 60.0, min(12000.0, sr / 2)
        edges = np.logspace(np.log10(lo), np.log10(hi), n + 1)
        out = []
        for i in range(n):
            m = (freqs >= edges[i]) & (freqs < edges[i + 1])
            out.append(float(mag[m].sum()) if m.any() else 0.0)
        peak = max(out) or 1.0
        return [min(1.0, v / peak) for v in out]
    except Exception:
        return [0.0] * n


def compute(level: float,
            pcm=None,
            sr: int = 24000,
            state: AvatarState = AvatarState.IDLE,
            t: float = 0.0,
            latency_ms: float = 0.0,
            smoothed: dict | None = None) -> ReactorParams:
    """Compute one frame of reactor parameters.

    * `level` — 0.0–1.0 live audio level (mic while listening, output while
      speaking). Drives ring height, spoke length, sphere scale, glow.
    * `pcm` — optional recent audio block for the spectral ring; without it
      the ring falls back to a level-modulated standing wave (never empty).
    * `state` — the avatar state; biases sweep/iris/glow/particles.
    * `t` — seconds; only feeds angles/phases.
    * `smoothed` — optional dict the caller keeps across frames for temporal
      smoothing of flux/level ("flux", "level" keys); mutated in place.
    """
    level = float(min(1.0, max(0.0, level or 0.0)))
    bias = _STATE_BIAS.get(state, _STATE_BIAS[AvatarState.IDLE])
    damp = bias["damp"]

    ring = _band_energies(pcm, sr) if pcm is not None else None
    if not ring or max(ring) <= 0.0:
        # Standing wave fallback: the ring never goes dead even with no audio
        # block — it breathes with the level instead.
        ring = [0.5 + 0.5 * math.sin(2 * math.pi * (i / WAVEFORM_POINTS)
                                    + t * 1.7) for i in range(WAVEFORM_POINTS)]
        ring = [r * (0.15 + 0.85 * level * damp + 0.10) for r in ring]
    else:
        ring = [min(1.0, r * (0.25 + 0.75 * damp) + 0.06 * level) for r in ring]

    # 12 spokes sample the ring every 6th point, lengthened by the live level.
    spokes = [min(1.0, ring[(i * 6) % WAVEFORM_POINTS] * 0.6
                  + level * 0.4 * damp + 0.05)
              for i in range(SPOKES)]

    if smoothed is not None:
        prev = float(smoothed.get("flux", 0.0))
        flux = prev + (level - prev) * 0.25
        smoothed["flux"] = flux
        pl = float(smoothed.get("level", 0.0))
        slevel = pl + (level - pl) * 0.35
        smoothed["level"] = slevel
    else:
        flux = level
        slevel = level

    params = ReactorParams(
        waveform_ring=ring,
        spokes=spokes,
        particle_speed=(0.25 + 2.2 * slevel) * bias["particle_mult"] * damp,
        particle_phase=t * (0.25 + 2.2 * slevel) * bias["particle_mult"],
        sphere_scale=1.0 + 0.22 * slevel * damp,
        glow=float(min(1.0, max(0.0, 0.35 + 0.55 * slevel * damp
                                + bias["glow_bias"]))),
        sweep_angle=(t * 2 * math.pi * _SWEEP_BASE_HZ
                     * bias["sweep_mult"]) % (2 * math.pi),
        iris=float(min(1.0, max(0.05, 0.45 + 0.35 * slevel * damp
                                + bias["iris_bias"]))),
        chromatic=float(min(1.0, 0.30 + 0.35 * slevel * damp)),
        arc_integrity=1.0 if state is not AvatarState.OFFLINE else 0.35,
        flux=float(min(1.0, max(0.0, flux))),
        mode_label=state.value,
        audio_label=f"{slevel:.1f}",
        latency_ms=float(max(0.0, latency_ms)),
    )
    return params


def telemetry_rows(params: ReactorParams) -> list[tuple[str, str]]:
    """The six HUD telemetry readouts as (label, value) pairs."""
    return [
        ("SHIRAZI CORE", params.mode_label),
        ("ARC INTEGRITY", f"{params.arc_integrity * 100:.0f}%"),
        ("FLUX", f"{params.flux * 100:.0f}%"),
        ("MODE", "VOICE" ),
        ("AUDIO", params.audio_label),
        ("LATENCY", f"{params.latency_ms:.0f} ms"),
    ]
