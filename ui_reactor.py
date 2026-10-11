"""ui_reactor.py — the SHIRAZI arc-reactor core (AURA-58 design).

Glassmorphism + retro-futuristic arc reactor. A frosted-glass dome sits over a
vacuum-tube phosphor core; a chunky riveted bezel, art-deco sunburst ticks and
nixie-tube telemetry give the 1980s-futurist warmth, while the hologram cyan
secondary keeps it SHIRAZI.

Paints the layered visualisation from the headless model in core/reactor.py.
All *numbers* (ring heights, spoke lengths, particle speed, sphere scale,
glow, sweep angle) come from ReactorParams; this file only turns them into
pixels. No audio analysis here — the caller feeds params.

Design layers (back → front):
  warm base · radial depth glow · synthwave grid floor · riveted bezel ·
  art-deco sunburst ticks · glass gyro rings · segmented iris · rosette ·
  vacuum-tube core · frosted glass dome · plasma arcs · waveform ring ·
  spokes · particles · pulses · highlight sweep · scanlines · glass telemetry
  panels (nixie digits) · HUD frame with corner screws · vignette.
"""

from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QPainter,
                         QPainterPath, QPen, QRadialGradient)
from PyQt6.QtWidgets import QWidget

from core.reactor import (PARTICLES, SPOKES, WAVEFORM_POINTS, ReactorParams,
                          compute, telemetry_rows)
from core.avatar_state import AvatarState


def _c(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlpha(int(min(255, max(0, alpha))))
    return c


class ReactorWidget(QWidget):
    """The arc-reactor centrepiece. Feed it params at ~30 Hz."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(280, 280)
        self._params = ReactorParams()
        self._smoothed: dict = {}
        self._t0 = time.monotonic()
        self._level = 0.0          # live audio level 0..1 (set_level)
        self._pcm = None           # optional recent audio block for the ring
        self._sr = 24000
        self._state = AvatarState.IDLE
        self._latency_ms = 0.0
        # ── AURA-58 retro-phosphor palette ──
        self._amber   = QColor("#ffb347")   # phosphor amber — primary
        self._amber_d = QColor("#ff8c1a")   # deep amber
        self._cyan    = QColor("#37e6ff")   # hologram cyan — secondary
        self._gold    = QColor("#c6a03c")   # restrained gold
        self._emerald = QColor("#00d68f")   # emerald
        self._red     = QColor("#ff4438")   # alarm red
        self._violet  = QColor("#b48cff")   # thinking violet

        self._theme = "aura"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(33)                # ~30 Hz

    # ── inputs ────────────────────────────────────────────────────────────

    THEMES = {
        # aura: amber retro-phosphor (default) · nebula: cyan hologram
        "aura":   {"amber": "#ffb347", "amber_d": "#ff8c1a", "cyan": "#37e6ff"},
        "nebula": {"amber": "#37e6ff", "amber_d": "#1a9ec4", "cyan": "#ffb347"},
    }

    def set_theme(self, theme: str) -> None:
        """Switch the reactor palette live: 'aura' ↔ 'nebula'."""
        theme = theme if theme in self.THEMES else "aura"
        pal = self.THEMES[theme]
        self._theme = theme
        self._amber = QColor(pal["amber"])
        self._amber_d = QColor(pal["amber_d"])
        self._cyan = QColor(pal["cyan"])
        try:
            self.update()
        except Exception:
            pass

    def set_level(self, level: float) -> None:
        """Thread-safe: live audio level 0..1 (a plain float store is atomic)."""
        try:
            self._level = float(min(1.0, max(0.0, level or 0.0)))
        except Exception:
            pass

    def set_audio_block(self, pcm, sr: int = 24000) -> None:
        self._pcm, self._sr = pcm, sr

    def set_state(self, state) -> None:
        try:
            self._state = state if isinstance(state, AvatarState) \
                else AvatarState(str(state))
        except Exception:
            self._state = AvatarState.IDLE

    def set_latency_ms(self, ms: float) -> None:
        try:
            self._latency_ms = max(0.0, float(ms))
        except Exception:
            pass

    def _tick(self):
        t = time.monotonic() - self._t0
        self._params = compute(self._level, pcm=self._pcm, sr=self._sr,
                               state=self._state, t=t,
                               latency_ms=self._latency_ms,
                               smoothed=self._smoothed)
        self._pcm = None                   # consumed; fresh block each tick
        if self.isVisible():
            self.update()

    # ── paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        W, H = float(self.width()), float(self.height())
        cx, cy = W / 2, H / 2 - 8
        r = min(W, H) * 0.30
        prm = self._params
        amb = self._amber
        cyn = self._cyan
        state_col = self._state_colour()
        t = time.monotonic() - self._t0
        glow = prm.glow

        # ── 1. warm retro base ──
        # A deep warm-black gradient: the phosphor-terminal room the reactor
        # lives in. Cooler at the edges so the amber core feels hot.
        maxwh = max(W, H) * 0.75
        base = QRadialGradient(cx, cy, maxwh, cx, cy, maxwh)
        base.setColorAt(0.0, QColor(24, 14, 6))
        base.setColorAt(0.55, QColor(13, 8, 4))
        base.setColorAt(1.0, QColor(5, 3, 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(base))
        p.drawRect(QRectF(0, 0, W, H))

        # ── 2. radial depth glow — breathing with the audio ──
        glow_r = r * 2.7
        g = QRadialGradient(cx, cy, glow_r, cx, cy, glow_r)
        g.setColorAt(0.00, _c(amb, 30 + 52 * glow))
        g.setColorAt(0.40, _c(self._amber_d, 12 + 22 * glow))
        g.setColorAt(0.75, _c(cyn, 6 + 10 * glow))
        g.setColorAt(1.00, _c(amb, 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

        # ── 3. synthwave grid floor — retro perspective at the bottom ──
        hz = cy + r * 1.05
        p.setPen(QPen(_c(amb, 26), 1.0))
        for i in range(1, 7):
            yy = hz + (H - hz) * (i / 7) ** 1.7
            p.drawLine(QPointF(0, yy), QPointF(W, yy))
        p.setPen(QPen(_c(cyn, 20), 1.0))
        for k in range(-6, 7):
            p.drawLine(QPointF(cx + k * r * 0.42, hz),
                       QPointF(cx + k * r * 1.15, H))

        # ── 4. chunky riveted bezel — the machined housing ──
        bez_r = r * 1.78
        bw = r * 0.11
        bez = QConicalGradient(cx, cy, math.degrees(t * 0.02))
        bez.setColorAt(0.00, QColor(104, 72, 40))
        bez.setColorAt(0.25, QColor(52, 36, 20))
        bez.setColorAt(0.50, QColor(118, 82, 44))
        bez.setColorAt(0.75, QColor(44, 30, 17))
        bez.setColorAt(1.00, QColor(104, 72, 40))
        p.setPen(QPen(QBrush(bez), bw))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - bez_r, cy - bez_r, bez_r * 2, bez_r * 2))
        # inner rim light — separates the bronze from the dark interior
        p.setPen(QPen(_c(self._amber, 60), 1.0))
        p.drawEllipse(QRectF(cx - bez_r + bw / 2 + 1, cy - bez_r + bw / 2 + 1,
                             (bez_r - bw / 2 - 1) * 2,
                             (bez_r - bw / 2 - 1) * 2))
        # lit upper rim, shadowed lower rim of the band
        p.setPen(QPen(_c(QColor(255, 220, 160), 70), 1.2))
        p.drawArc(QRectF(cx - bez_r - bw / 2, cy - bez_r - bw / 2,
                         (bez_r + bw / 2) * 2, (bez_r + bw / 2) * 2),
                  100 * 16, 140 * 16)
        p.setPen(QPen(_c(QColor(0, 0, 0), 120), 1.2))
        p.drawArc(QRectF(cx - bez_r + bw / 2, cy - bez_r + bw / 2,
                         (bez_r - bw / 2) * 2, (bez_r - bw / 2) * 2),
                  280 * 16, 140 * 16)
        # rivets — 8 bolts around the bezel
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            a = i * math.pi / 4
            bx, by = cx + bez_r * math.cos(a), cy + bez_r * math.sin(a)
            rg = QRadialGradient(bx - 1, by - 1, 0.5, bx - 1, by - 1, 3.4)
            rg.setColorAt(0.0, QColor(255, 235, 200, 220))
            rg.setColorAt(0.5, QColor(120, 88, 50, 220))
            rg.setColorAt(1.0, QColor(30, 20, 12, 220))
            p.setBrush(QBrush(rg))
            p.drawEllipse(QPointF(bx, by), 3.4, 3.4)

        # ── 5. art-deco sunburst ticks — the retro dial ──
        sun_r = r * 1.60
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 6 == 0:          # cardinal: long + wide, amber
                r0, r1, wd, col, al = 0.86, 0.985, 2.4, amb, 200
            elif i % 3 == 0:        # major: medium, warm gold
                r0, r1, wd, col, al = 0.90, 0.985, 1.6, self._gold, 150
            else:                   # minor: short hairline
                r0, r1, wd, col, al = 0.945, 0.985, 1.0, amb, 70
            p.setPen(QPen(_c(col, al), wd))
            p.drawLine(QPointF(cx + ca * sun_r * r0, cy + sa * sun_r * r0),
                       QPointF(cx + ca * sun_r * r1, cy + sa * sun_r * r1))

        # ── 6. glass gyro rings — frosted, slowly turning ──
        p.save()
        p.translate(cx, cy)
        for tilt, speed, alpha in ((0.5, 0.22, 60), (-0.35, -0.15, 44)):
            p.save()
            p.rotate(math.degrees(t * speed + tilt))
            p.scale(1.0, 0.42)
            rr = r * 1.35
            p.setPen(Qt.PenStyle.NoPen)
            gg = QRadialGradient(0, 0, rr - 4, 0, 0, rr + 4)
            gg.setColorAt(0.0, _c(cyn, 0))
            gg.setColorAt(0.85, _c(cyn, alpha * 0.5))
            gg.setColorAt(1.0, _c(QColor(255, 255, 255), alpha * 0.9))
            p.setBrush(QBrush(gg))
            ring_path = QPainterPath()
            ring_path.addEllipse(QRectF(-rr - 4, -rr - 4,
                                        (rr + 4) * 2, (rr + 4) * 2))
            ring_path.addEllipse(QRectF(-rr + 4, -rr + 4,
                                        (rr - 4) * 2, (rr - 4) * 2))
            p.drawPath(ring_path)
            p.restore()
        p.restore()

        # ── 7. segmented iris — opens with SPEAKING ──
        segs = 24
        iris_r = r * (0.52 + 0.30 * prm.iris)
        p.setPen(QPen(_c(state_col, 170), 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(segs):
            a0 = 2 * math.pi * i / segs + t * 0.25
            a1 = a0 + (2 * math.pi / segs) * (0.30 + 0.55 * prm.iris)
            path = QPainterPath()
            path.arcMoveTo(QRectF(cx - iris_r, cy - iris_r,
                                  iris_r * 2, iris_r * 2),
                           -math.degrees(a0))
            path.arcTo(QRectF(cx - iris_r, cy - iris_r,
                              iris_r * 2, iris_r * 2),
                       -math.degrees(a0), -math.degrees(a1 - a0))
            p.drawPath(path)

        # ── 8. rosette — the quiet cultural signature, behind the core ──
        self._paint_rosette(p, cx, cy, r * 0.86, _c(self._gold, 46))

        # ── 9. vacuum-tube core — the phosphor heart ──
        # White-hot filament → amber → deep orange, with mains flicker.
        sr_ = r * 0.42 * prm.sphere_scale
        flick = 0.92 + 0.08 * math.sin(t * 47.0) * math.sin(t * 31.0)
        tube = QRadialGradient(cx, cy, sr_, cx - sr_ * 0.25, cy - sr_ * 0.30)
        tube.setColorAt(0.00, _c(QColor(255, 252, 240), 235 * flick))
        tube.setColorAt(0.25, _c(QColor(255, 214, 140), 220 * flick))
        tube.setColorAt(0.55, _c(amb, 200))
        tube.setColorAt(0.85, _c(self._amber_d, 120))
        tube.setColorAt(1.00, _c(self._amber_d, 25))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(tube))
        p.drawEllipse(QRectF(cx - sr_, cy - sr_, sr_ * 2, sr_ * 2))
        # filament coil — three bright turns across the heart
        p.setPen(QPen(_c(QColor(255, 246, 220), 170 * flick), 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, fy in enumerate((-0.16, 0.0, 0.16)):
            p.drawArc(QRectF(cx - sr_ * 0.52, cy + sr_ * fy - sr_ * 0.30,
                             sr_ * 1.04, sr_ * 0.60),
                      int((200 + k * 24 + t * 30) * 16), int(140 * 16))
        # tube envelope rim
        p.setPen(QPen(_c(amb, 190), 1.5))
        p.drawEllipse(QRectF(cx - sr_, cy - sr_, sr_ * 2, sr_ * 2))

        # ── 10. frosted glass dome over the core ──
        # The glassmorphism hero: a translucent dome with a top sheen,
        # a specular crescent, and a soft inner shadow at its foot.
        dr = sr_ * 1.55
        dome = QRadialGradient(cx, cy - dr * 0.35, dr * 0.1,
                               cx, cy - dr * 0.35, dr)
        dome.setColorAt(0.00, _c(QColor(255, 244, 225), 64))
        dome.setColorAt(0.45, _c(QColor(255, 236, 205), 30))
        dome.setColorAt(0.80, _c(QColor(200, 220, 235), 16))
        dome.setColorAt(1.00, _c(QColor(160, 190, 215), 34))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dome))
        p.drawEllipse(QRectF(cx - dr, cy - dr, dr * 2, dr * 2))
        # specular crescent — the light catching the glass
        p.setPen(QPen(_c(QColor(255, 252, 245), 110), 2.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(cx - dr * 0.92, cy - dr * 0.92,
                         dr * 1.84, dr * 1.84), 108 * 16, 64 * 16)
        p.setPen(QPen(_c(QColor(255, 252, 245), 40), 5.0))
        p.drawArc(QRectF(cx - dr * 0.92, cy - dr * 0.92,
                         dr * 1.84, dr * 1.84), 118 * 16, 44 * 16)
        # dome foot shadow + thin bright rim (the glass edge)
        p.setPen(QPen(_c(QColor(0, 0, 0), 90), 2.0))
        p.drawArc(QRectF(cx - dr, cy - dr, dr * 2, dr * 2), 235 * 16, 70 * 16)
        p.setPen(QPen(_c(QColor(255, 240, 215), 90), 1.0))
        p.drawEllipse(QRectF(cx - dr, cy - dr, dr * 2, dr * 2))

        # ── 11. plasma arcs — jagged discharges over the glass ──
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):
            aa = t * (0.7 + 0.23 * k) + k * 2.4
            r0, r1 = sr_ * 1.05, r * (0.62 + 0.10 * prm.iris)
            nsegs = 14
            pts = []
            rnd = random.Random(1000 + k * 77 + int(t * 3 + k * 9) % 5)
            for i in range(nsegs + 1):
                f = i / nsegs
                rr = r0 + (r1 - r0) * f
                wob = (rnd.random() - 0.5) * r * 0.09 * math.sin(f * math.pi)
                ang = aa + wob / max(rr, 1.0)
                pts.append(QPointF(cx + rr * math.cos(ang),
                                   cy + rr * math.sin(ang)))
            path = QPainterPath()
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            hot = 0.35 + 0.65 * glow
            p.setPen(QPen(_c(QColor(255, 240, 215), 46 * hot), 3.2))
            p.drawPath(path)
            p.setPen(QPen(_c(QColor(255, 252, 240), 150 * hot), 1.1))
            p.drawPath(path)

        # ── 12. waveform ring — the real spectral shape, phosphor amber ──
        ring_r = r * 1.52
        p.setPen(QPen(_c(state_col, 150), 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        for i, v in enumerate(prm.waveform_ring[:WAVEFORM_POINTS]):
            a = 2 * math.pi * i / WAVEFORM_POINTS - math.pi / 2
            rr = ring_r + v * r * 0.30
            pt = QPointF(cx + rr * math.cos(a), cy + rr * math.sin(a))
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        p.drawPath(path)

        # ── 13. energy spokes — length driven by audio ──
        p.setPen(QPen(_c(amb, 110), 1.6))
        for i, s in enumerate(prm.spokes[:SPOKES]):
            a = 2 * math.pi * i / SPOKES - math.pi / 2
            r0, r1 = r * 1.24, r * (1.24 + 0.42 * s)
            p.drawLine(QPointF(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                       QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_c(amb, 90 + 120 * s)))
            p.drawEllipse(QPointF(cx + r1 * math.cos(a),
                                  cy + r1 * math.sin(a)), 2.0, 2.0)
            p.setPen(QPen(_c(amb, 110), 1.6))

        # ── 14. orbiting particles + energy pulses ──
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(PARTICLES):
            orbit = r * (1.62 + 0.28 * ((i * 37) % 10) / 10)
            a = prm.particle_phase * (1 + 0.2 * ((i * 13) % 5) / 5) \
                + i * 2 * math.pi / PARTICLES
            px, py = cx + orbit * math.cos(a), cy + orbit * math.sin(a) * 0.82
            al = 40 + 90 * ((i * 7) % 10) / 10 * (0.4 + 0.6 * glow)
            p.setBrush(QBrush(_c(cyn if i % 5 else self._gold, al)))
            d = 1.6 + 1.4 * ((i * 11) % 10) / 10
            p.drawEllipse(QPointF(px, py), d, d)
        for k in range(3):
            pa = -t * (0.9 + 0.2 * k) + k * 2.094
            px, py = cx + r * 1.35 * math.cos(pa), cy + r * 1.35 * math.sin(pa)
            pg = QRadialGradient(px, py, 1.0, px, py, 7.0)
            pg.setColorAt(0.0, _c(QColor(255, 255, 255), 230))
            pg.setColorAt(0.4, _c(amb, 150))
            pg.setColorAt(1.0, _c(amb, 0))
            p.setBrush(QBrush(pg))
            p.drawEllipse(QRectF(px - 7, py - 7, 14, 14))

        # ── 15. highlight sweep circling the tube rim ──
        p.setPen(QPen(_c(QColor(255, 250, 235), 120 + 80 * glow), 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        sa0 = math.degrees(t * 1.4)
        p.drawArc(QRectF(cx - sr_ * 1.02, cy - sr_ * 1.02,
                         sr_ * 2.04, sr_ * 2.04),
                  int(sa0 * 16), int(46 * 16))

        # ── 16. scanning sweep — radar line + trail ──
        sa = prm.sweep_angle
        p.setPen(QPen(_c(self._emerald, 130), 1.6))
        p.drawLine(QPointF(cx, cy),
                   QPointF(cx + r * 1.7 * math.cos(sa),
                           cy + r * 1.7 * math.sin(sa)))
        trail = QConicalGradient(cx, cy, -math.degrees(sa))
        trail.setColorAt(0.0, _c(self._emerald, 40))
        trail.setColorAt(0.12, _c(self._emerald, 0))
        trail.setColorAt(1.0, _c(self._emerald, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(trail))
        p.drawEllipse(QRectF(cx - r * 1.7, cy - r * 1.7, r * 3.4, r * 3.4))

        # ── 17. CRT scanlines — the retro overlay ──
        # Batched into few drawLine calls; cheap even at 30 Hz.
        p.setPen(QPen(QColor(0, 0, 0, 26), 1.0))
        y = 0.0
        while y < H:
            p.drawLine(QPointF(0, y), QPointF(W, y))
            y += 3.0
        # drifting shimmer band
        shimmer_y = (t * 40) % (H + 80) - 40
        shg = QRadialGradient(cx, shimmer_y, 60, cx, shimmer_y, 60)
        shg.setColorAt(0.0, _c(QColor(255, 240, 210), 14))
        shg.setColorAt(1.0, _c(QColor(255, 240, 210), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(shg))
        p.drawRect(QRectF(0, shimmer_y - 60, W, 120))

        # ── 18. glass telemetry panels + nixie digits ──
        self._paint_telemetry(p, W, H, prm, state_col)

        # ── 19. HUD frame with corner screws ──
        p.setPen(QPen(_c(amb, 46), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(6, 6, W - 12, H - 12))
        bl = 18
        p.setPen(QPen(_c(amb, 150), 1.8))
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            x0, y0 = (6 if sx > 0 else W - 6), (6 if sy > 0 else H - 6)
            path = QPainterPath(QPointF(x0 + sx * bl, y0))
            path.lineTo(QPointF(x0, y0))
            path.lineTo(QPointF(x0, y0 + sy * bl))
            p.drawPath(path)
            sg = QRadialGradient(x0 - 1, y0 - 1, 0.5, x0 - 1, y0 - 1, 3.0)
            sg.setColorAt(0.0, QColor(255, 235, 200, 200))
            sg.setColorAt(1.0, QColor(40, 26, 14, 200))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(sg))
            p.drawEllipse(QPointF(x0, y0), 3.0, 3.0)

        # ── 20. vignette ──
        vmin = min(W, H) * 0.62
        vg = QRadialGradient(cx, cy, vmin, cx, cy, vmin)
        vg.setColorAt(0.70, QColor(0, 0, 0, 0))
        vg.setColorAt(1.00, QColor(4, 2, 1, 150))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(vg))
        p.drawRect(QRectF(0, 0, W, H))

    # ── helpers ───────────────────────────────────────────────────────────

    def _state_colour(self) -> QColor:
        return {
            AvatarState.IDLE:      self._amber,
            AvatarState.LISTENING: self._emerald,
            AvatarState.THINKING:  self._violet,
            AvatarState.SPEAKING:  self._amber,
            AvatarState.EXECUTING: self._gold,
            AvatarState.SUCCESS:   self._emerald,
            AvatarState.ERROR:      self._red,
            AvatarState.OFFLINE:   QColor("#5a6b74"),
        }.get(self._state, self._amber)

    def _glass_panel(self, p: QPainter, rect: QRectF, radius: float = 10.0):
        """Frosted-glass panel: soft shadow, translucent fill, lit top edge."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(0, 0, 0, 90)))
        p.drawRoundedRect(QRectF(rect.x() + 2, rect.y() + 3,
                                 rect.width(), rect.height()),
                          radius, radius)
        fill = QRadialGradient(rect.center().x(), rect.top(), 8,
                               rect.center().x(), rect.top(), rect.height())
        fill.setColorAt(0.0, QColor(255, 236, 205, 46))
        fill.setColorAt(0.5, QColor(255, 236, 205, 22))
        fill.setColorAt(1.0, QColor(120, 140, 160, 18))
        p.setBrush(QBrush(fill))
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor(255, 240, 215, 60), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, radius, radius)
        p.setPen(QPen(QColor(255, 250, 235, 110), 1.4))
        p.drawLine(QPointF(rect.x() + radius, rect.y() + 0.5),
                   QPointF(rect.x() + rect.width() - radius, rect.y() + 0.5))

    def _nixie(self, p: QPainter, rect: QRectF, text: str,
               size: int = 11, align=Qt.AlignmentFlag.AlignRight):
        """Nixie-tube digit: amber text with a warm halo bloom."""
        f = QFont("Courier New", size, QFont.Weight.Bold)
        p.setFont(f)
        p.setPen(QPen(QColor(255, 150, 40, 60)))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            p.drawText(QRectF(rect.x() + dx, rect.y() + dy,
                              rect.width(), rect.height()), align, text)
        p.setPen(QPen(QColor(255, 214, 150, 235)))
        p.drawText(rect, align, text)

    def _paint_rosette(self, p: QPainter, cx: float, cy: float, r: float,
                       color: QColor) -> None:
        """An 8-point geometric star (khatam): two squares at 45°, plus the
        inner octagon — the quiet cultural signature of the interface."""
        p.setPen(QPen(color, 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rot in (0.0, math.pi / 4):
            pts = [QPointF(cx + r * math.cos(rot + i * math.pi / 2),
                           cy + r * math.sin(rot + i * math.pi / 2))
                   for i in range(4)]
            p.drawPolygon(pts)
        # inner octagon
        pts = [QPointF(cx + r * 0.55 * math.cos(i * math.pi / 4),
                       cy + r * 0.55 * math.sin(i * math.pi / 4))
               for i in range(8)]
        p.drawPolygon(pts)
        p.drawEllipse(QRectF(cx - r * 0.30, cy - r * 0.30, r * 0.6, r * 0.6))

    def _paint_telemetry(self, p: QPainter, W: float, H: float,
                         prm: ReactorParams, state_col: QColor):
        rows = telemetry_rows(prm)
        # left glass panel — telemetry with nixie digits
        pw, ph = 218, 20 + 15 * len(rows)
        panel = QRectF(14, H - ph - 14, pw, ph)
        self._glass_panel(p, panel)
        y = panel.y() + 8
        p.setFont(QFont("Courier New", 7))
        for label, value in rows:
            p.setPen(_c(QColor(190, 150, 100), 200))
            p.drawText(QRectF(panel.x() + 10, y, 110, 13),
                       Qt.AlignmentFlag.AlignLeft, label)
            self._nixie(p, QRectF(panel.x() + 118, y - 2, pw - 128, 17),
                        str(value), size=9)
            y += 15
        # right glass panel — state readout
        sp = QRectF(W - 150, 14, 136, 44)
        self._glass_panel(p, sp, radius=8.0)
        p.setFont(QFont("Courier New", 7))
        p.setPen(_c(QColor(190, 150, 100), 200))
        p.drawText(QRectF(sp.x() + 10, sp.y() + 7, 116, 12),
                   Qt.AlignmentFlag.AlignLeft, "STATE")
        self._nixie(p, QRectF(sp.x() + 10, sp.y() + 18, 116, 18),
                    self._state.name, size=11,
                    align=Qt.AlignmentFlag.AlignLeft)
        # state lamp — glowing dot under the panel
        lamp = QRadialGradient(W - 24, 66, 1.0, W - 24, 66, 9.0)
        lamp.setColorAt(0.0, _c(QColor(255, 255, 255), 220))
        lamp.setColorAt(0.35, _c(state_col, 200))
        lamp.setColorAt(1.0, _c(state_col, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(lamp))
        p.drawEllipse(QRectF(W - 33, 57, 18, 18))
