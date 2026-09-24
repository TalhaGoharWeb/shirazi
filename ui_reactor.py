"""ui_reactor.py — the SHIRAZI reactor core renderer (Phase 5).

Paints the layered 3D-style reactor visualisation from the headless model in
core/reactor.py. All *numbers* (ring heights, spoke lengths, particle speed,
sphere scale, glow, sweep angle) come from ReactorParams; this file only turns
them into pixels. No audio analysis here — the caller feeds params.

Elements (mission spec §5):
  radial depth glow · chromatic vignette · HUD frame · corner brackets ·
  perspective elliptical rings · gyroscope rings · segmented iris ·
  cyan energy arcs · hexagons · Islamic geometric rosette · 12 energy spokes ·
  72-point waveform ring · orbiting particles · central energy sphere ·
  lens flare · scanning sweep · telemetry HUD · state indicators.

Design language: dark glassmorphism, subtle neon cyan, emerald + restrained
gold accents, an Islamic geometric rosette as the one cultural signature.
Deliberately NOT a gaming UI: thin lines, low-alpha fills, glow concentrated
in the central sphere — everything else stays quiet.
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
    """The reactor core centrepiece. Feed it params at ~30 Hz."""

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
        self._accent = QColor("#00d4ff")   # neon cyan
        self._emerald = QColor("#00d68f")  # emerald
        self._gold = QColor("#c6a03c")     # restrained gold
        self._amber = QColor("#ff9a3c")

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(33)                # ~30 Hz

    # ── inputs ────────────────────────────────────────────────────────────

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
        acc = self._accent

        state_col = self._state_colour()

        # 1. radial depth glow — the room the core sits in
        glow_r = r * 2.6
        g = QRadialGradient(cx, cy, glow_r)
        g.setColorAt(0.00, _c(acc, 26 + 40 * prm.glow))
        g.setColorAt(0.45, _c(acc, 10 + 16 * prm.glow))
        g.setColorAt(1.00, _c(acc, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QRectF(cx - glow_r, cy - glow_r, glow_r * 2, glow_r * 2))

        # 2. chromatic vignette — faint warm/cool split at the edges
        vg = QRadialGradient(cx, cy, min(W, H) * 0.62)
        vg.setColorAt(0.70, QColor(0, 0, 0, 0))
        vg.setColorAt(1.00, QColor(2, 10, 16, 150))
        p.setBrush(QBrush(vg))
        p.drawRect(QRectF(0, 0, W, H))

        # 3. HUD frame + 4. corner brackets — thin, quiet
        p.setPen(QPen(_c(acc, 46), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(6, 6, W - 12, H - 12))
        bl = 16
        p.setPen(QPen(_c(acc, 150), 1.6))
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            x0, y0 = (6 if sx > 0 else W - 6), (6 if sy > 0 else H - 6)
            path = QPainterPath(QPointF(x0 + sx * bl, y0))
            path.lineTo(QPointF(x0, y0))
            path.lineTo(QPointF(x0, y0 + sy * bl))
            p.drawPath(path)

        # 5. perspective elliptical rings — the "3D floor" under the core
        p.setPen(QPen(_c(acc, 40), 1.0))
        for i, f in enumerate((1.55, 1.85, 2.15)):
            er = r * f
            p.drawEllipse(QRectF(cx - er, cy + r * 0.55 - er * 0.30,
                                 er * 2, er * 0.60))

        # 6. gyroscope rings — two tilted rings turning slowly
        t = time.monotonic() - self._t0
        p.save()
        p.translate(cx, cy)
        for i, (tilt, speed, alpha) in enumerate(
                ((0.5, 0.22, 60), (-0.35, -0.15, 44))):
            p.save()
            p.rotate(math.degrees(t * speed + i * 1.2 + tilt))
            p.scale(1.0, 0.42)
            p.setPen(QPen(_c(acc, alpha), 1.1))
            p.drawEllipse(QRectF(-r * 1.35, -r * 1.35, r * 2.7, r * 2.7))
            p.restore()
        p.restore()

        # 7. segmented iris — opens with SPEAKING, closes OFFLINE
        segs = 24
        iris_r = r * (0.52 + 0.30 * prm.iris)
        p.setPen(QPen(_c(state_col, 170), 2.0))
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

        # 8. cyan energy arcs — three sweeping arcs on the iris radius
        p.setPen(QPen(_c(acc, 120), 1.4))
        for k in range(3):
            a = t * (0.9 + 0.3 * k) + k * 2.1
            rr = iris_r * (1.06 + 0.05 * k)
            p.drawArc(QRectF(cx - rr, cy - rr, rr * 2, rr * 2),
                      int(-math.degrees(a) * 16), int(52 * 16))

        # 9. hexagons — two faint hex rings framing the core
        p.setPen(QPen(_c(acc, 52), 1.0))
        for hr, rot in ((r * 1.02, t * 0.10), (r * 1.18, -t * 0.07)):
            pts = [QPointF(cx + hr * math.cos(rot + i * math.pi / 3),
                           cy + hr * math.sin(rot + i * math.pi / 3))
                   for i in range(6)]
            p.drawPolygon(pts)

        # 10. Islamic geometric rosette — the one cultural signature.
        #     An 8-point star (khatam) drawn faintly behind the sphere.
        self._paint_rosette(p, cx, cy, r * 0.86, _c(self._gold, 40))

        # 11. 12 energy spokes — length driven by audio
        p.setPen(QPen(_c(acc, 110), 1.6))
        for i, s in enumerate(prm.spokes[:SPOKES]):
            a = 2 * math.pi * i / SPOKES - math.pi / 2
            r0, r1 = r * 1.24, r * (1.24 + 0.42 * s)
            p.drawLine(QPointF(cx + r0 * math.cos(a), cy + r0 * math.sin(a)),
                       QPointF(cx + r1 * math.cos(a), cy + r1 * math.sin(a)))
            # spoke tip dot
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(_c(acc, 90 + 120 * s)))
            p.drawEllipse(QPointF(cx + r1 * math.cos(a),
                                  cy + r1 * math.sin(a)), 2.0, 2.0)
            p.setPen(QPen(_c(acc, 110), 1.6))

        # 12. 72-point waveform ring — the real spectral shape
        ring_r = r * 1.52
        p.setPen(QPen(_c(state_col, 150), 1.3))
        # The path is closed: it must NEVER be filled — an earlier build
        # left the spoke-tip brush active here and the whole ring painted
        # as one giant solid disc over the core.
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

        # 13. orbiting particles — speed driven by audio
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(PARTICLES):
            orbit = r * (1.62 + 0.28 * ((i * 37) % 10) / 10)
            a = prm.particle_phase * (1 + 0.2 * ((i * 13) % 5) / 5) \
                + i * 2 * math.pi / PARTICLES
            px, py = cx + orbit * math.cos(a), cy + orbit * math.sin(a) * 0.82
            al = 40 + 90 * ((i * 7) % 10) / 10 * (0.4 + 0.6 * prm.glow)
            p.setBrush(QBrush(_c(acc if i % 5 else self._gold, al)))
            d = 1.6 + 1.4 * ((i * 11) % 10) / 10
            p.drawEllipse(QPointF(px, py), d, d)

        # 14. central energy sphere — scale + glow driven by audio
        sr_ = r * 0.42 * prm.sphere_scale
        # NOTE: the focal point must stay INSIDE the gradient circle. An
        # earlier 6-arg QRadialGradient put the focal point outside the
        # circle and Qt painted nothing at all — the sphere was invisible.
        sg = QRadialGradient(cx, cy, sr_, cx - sr_ * 0.3, cy - sr_ * 0.35)
        sg.setColorAt(0.0, _c(QColor("#eaffff"), 200))
        sg.setColorAt(0.35, _c(acc, 170))
        sg.setColorAt(1.0, _c(acc, 30))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sg))
        p.drawEllipse(QRectF(cx - sr_, cy - sr_, sr_ * 2, sr_ * 2))
        # sphere rim
        p.setPen(QPen(_c(acc, 190), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(cx - sr_, cy - sr_, sr_ * 2, sr_ * 2))

        # 14b. plasma arcs — jagged energy discharges between the iris
        # and the sphere. Each arc is a seeded random walk, drawn twice:
        # a wide faint pass for glow, then a thin hot core.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(3):
            aa = t * (0.7 + 0.23 * k) + k * 2.4
            r0, r1 = sr_ * 1.05, r * (0.62 + 0.10 * prm.iris)
            segs = 14
            pts = []
            rnd = random.Random(1000 + k * 77 + int(t * 3 + k * 9) % 5)
            for i in range(segs + 1):
                f = i / segs
                rr = r0 + (r1 - r0) * f
                wob = (rnd.random() - 0.5) * r * 0.09 * math.sin(f * math.pi)
                ang = aa + wob / max(rr, 1.0)
                pts.append(QPointF(cx + rr * math.cos(ang),
                                   cy + rr * math.sin(ang)))
            path = QPainterPath()
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            hot = 0.35 + 0.65 * prm.glow
            p.setPen(QPen(_c(QColor("#d8f6ff"), 46 * hot), 3.2))
            p.drawPath(path)
            p.setPen(QPen(_c(QColor("#eafcff"), 150 * hot), 1.1))
            p.drawPath(path)

        # 14c. core bloom — a second, wider soft halo so the sphere feels
        # like it is radiating, not just filled.
        bloom = QRadialGradient(cx, cy, sr_ * 2.6)
        bloom.setColorAt(0.0, _c(acc, 44 + 50 * prm.glow))
        bloom.setColorAt(1.0, _c(acc, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(bloom))
        p.drawEllipse(QRectF(cx - sr_ * 2.6, cy - sr_ * 2.6,
                             sr_ * 5.2, sr_ * 5.2))

        # 14d. energy pulses — bright packets orbiting the tick ring.
        p.setPen(Qt.PenStyle.NoPen)
        for k in range(3):
            pa = -t * (0.9 + 0.2 * k) + k * 2.094
            px, py = cx + r * 1.35 * math.cos(pa), cy + r * 1.35 * math.sin(pa)
            pg = QRadialGradient(px, py, 1.0, px, py, 7.0)
            pg.setColorAt(0.0, _c(QColor("#ffffff"), 230))
            pg.setColorAt(0.4, _c(acc, 150))
            pg.setColorAt(1.0, _c(acc, 0))
            p.setBrush(QBrush(pg))
            p.drawEllipse(QRectF(px - 7, py - 7, 14, 14))

        # 14e. sphere highlight sweep — a bright arc circling the rim.
        p.setPen(QPen(_c(QColor("#ffffff"), 120 + 80 * prm.glow), 2.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        sa0 = math.degrees(t * 1.4)
        p.drawArc(QRectF(cx - sr_ * 1.02, cy - sr_ * 1.02,
                         sr_ * 2.04, sr_ * 2.04),
                  int(sa0 * 16), int(46 * 16))

        # 15. lens flare — one restrained horizontal streak
        if prm.glow > 0.45:
            fl = QConicalGradient(cx, cy, math.degrees(t * 0.5))
            fl.setColorAt(0.0, _c(QColor("#ffffff"), 0))
            fl.setColorAt(0.5, _c(QColor("#bff3ff"),
                                  26 * (prm.glow - 0.45) / 0.55))
            fl.setColorAt(1.0, _c(QColor("#ffffff"), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(fl))
            p.drawEllipse(QRectF(cx - sr_ * 2.6, cy - sr_ * 0.5,
                                 sr_ * 5.2, sr_ * 1.0))

        # 16. scanning sweep — a rotating radar line
        sa = prm.sweep_angle
        p.setPen(QPen(_c(self._emerald, 130), 1.6))
        p.drawLine(QPointF(cx, cy),
                   QPointF(cx + r * 1.7 * math.cos(sa),
                           cy + r * 1.7 * math.sin(sa)))
        # sweep trail
        trail = QConicalGradient(cx, cy, -math.degrees(sa))
        trail.setColorAt(0.0, _c(self._emerald, 40))
        trail.setColorAt(0.12, _c(self._emerald, 0))
        trail.setColorAt(1.0, _c(self._emerald, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(trail))
        p.drawEllipse(QRectF(cx - r * 1.7, cy - r * 1.7, r * 3.4, r * 3.4))

        # 17. telemetry HUD + state indicators — bottom strip
        self._paint_telemetry(p, W, H, prm, state_col)

    # ── helpers ───────────────────────────────────────────────────────────

    def _state_colour(self) -> QColor:
        return {
            AvatarState.IDLE:      self._accent,
            AvatarState.LISTENING: self._emerald,
            AvatarState.THINKING:  QColor("#b48cff"),
            AvatarState.SPEAKING:  self._accent,
            AvatarState.EXECUTING: self._gold,
            AvatarState.SUCCESS:   self._emerald,
            AvatarState.ERROR:      self._amber,
            AvatarState.OFFLINE:   QColor("#5a6b74"),
        }.get(self._state, self._accent)

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
                         prm: ReactorParams, state_col: QColor) -> None:
        rows = telemetry_rows(prm)
        p.setFont(QFont("Courier New", 7))
        y = H - 8 - 13 * len(rows)
        x = 16
        for label, value in rows:
            p.setPen(_c(QColor("#3a8a9a"), 200))
            p.drawText(QRectF(x, y, 130, 13), Qt.AlignmentFlag.AlignLeft,
                       label)
            p.setPen(_c(state_col, 220))
            p.drawText(QRectF(x + 132, y, 120, 13),
                       Qt.AlignmentFlag.AlignLeft, str(value))
            y += 13
        # state indicator dot, top-right
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(_c(state_col, 230)))
        p.drawEllipse(QPointF(W - 22, 22), 5, 5)
        p.setPen(_c(state_col, 120))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(W - 30, 14, 16, 16))
