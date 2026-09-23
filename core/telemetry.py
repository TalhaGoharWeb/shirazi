"""core/telemetry.py — system telemetry for the HUD panel (Phase 5).

A throttled, headless-testable sampler: CPU, RAM, DISK, BATTERY, UPTIME,
NETWORK. The Qt layer polls `snapshot()` on a slow QTimer (~1 Hz) and
animates the bars toward the sampled values, so the panel never overloads the
machine it is measuring. Sampling here is guarded — every probe is wrapped so
a missing sensor (no battery on a desktop, no psutil counter) degrades to
None, never an exception.

FREE-FIRST: psutil only. No NVML/WMI probing here — GPU/temperature stay in
ui.py's _SysMetrics, which already caches those handles.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

try:
    import psutil as _psutil
except Exception:  # pragma: no cover — psutil is a hard runtime dep
    _psutil = None


@dataclass
class TelemetrySample:
    cpu_percent: float | None = None
    ram_percent: float | None = None
    disk_percent: float | None = None
    battery_percent: float | None = None
    battery_plugged: bool | None = None
    uptime_seconds: float = 0.0
    net_mbps: float | None = None

    def as_rows(self) -> list[tuple[str, float | None, str]]:
        """(label, 0..100 value or None, display string) for the panel."""
        return [
            ("CPU",     self.cpu_percent,     _pct(self.cpu_percent)),
            ("RAM",     self.ram_percent,     _pct(self.ram_percent)),
            ("DISK",    self.disk_percent,    _pct(self.disk_percent)),
            ("BATTERY", self.battery_percent, _battery_str(self)),
            ("UPTIME",  None,                 format_uptime(self.uptime_seconds)),
            ("NETWORK", self.net_mbps,        _net_str(self.net_mbps)),
        ]


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:.0f}%"


def _battery_str(s: TelemetrySample) -> str:
    if s.battery_percent is None:
        return "—"
    plug = " ⚡" if s.battery_plugged else ""
    return f"{s.battery_percent:.0f}%{plug}"


def _net_str(mbps: float | None) -> str:
    if mbps is None:
        return "—"
    return f"{mbps:.1f} Mb/s" if mbps < 1000 else f"{mbps / 1000:.2f} Gb/s"


def format_uptime(seconds: float) -> str:
    """'3d 4h 12m' — days only when non-zero, always minutes."""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, _s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h or d:
        parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


class TelemetrySampler:
    """Throttled sampler. `snapshot()` returns the cached sample unless
    `min_interval` seconds have passed since the last real probe, so a fast
    UI timer cannot turn into a fast psutil loop."""

    def __init__(self, min_interval: float = 2.0):
        self._min_interval = max(0.25, float(min_interval))
        self._last: TelemetrySample = TelemetrySample()
        self._last_t = 0.0
        self._net_prev = None
        self._net_prev_t = 0.0
        self.probe_count = 0  # diagnostics / tests

    def snapshot(self) -> TelemetrySample:
        now = time.monotonic()
        if now - self._last_t < self._min_interval:
            return self._last
        self._last_t = now
        self.probe_count += 1
        self._last = self._probe(now)
        return self._last

    # ── probes (each guarded) ──────────────────────────────────────────────

    def _probe(self, now: float) -> TelemetrySample:
        s = TelemetrySample()
        if _psutil is None:
            return s
        try:
            s.cpu_percent = float(_psutil.cpu_percent(interval=None))
        except Exception:
            pass
        try:
            s.ram_percent = float(_psutil.virtual_memory().percent)
        except Exception:
            pass
        try:
            s.disk_percent = float(_psutil.disk_usage("/").percent)
        except Exception:
            try:
                import os
                s.disk_percent = float(
                    _psutil.disk_usage(os.path.expanduser("~")).percent)
            except Exception:
                pass
        try:
            batt = _psutil.sensors_battery()
            if batt is not None:
                s.battery_percent = float(batt.percent)
                s.battery_plugged = bool(batt.power_plugged)
        except Exception:
            pass
        try:
            s.uptime_seconds = time.time() - _psutil.boot_time()
        except Exception:
            pass
        s.net_mbps = self._net_rate(now)
        return s

    def _net_rate(self, now: float) -> float | None:
        """Combined up+down throughput in Mb/s since the previous probe."""
        if _psutil is None:
            return None
        try:
            cur = _psutil.net_io_counters()
            total = cur.bytes_sent + cur.bytes_recv
            if self._net_prev is not None and now > self._net_prev_t:
                dt = now - self._net_prev_t
                rate = max(0.0, (total - self._net_prev) / dt) * 8 / 1e6
            else:
                rate = 0.0
            self._net_prev, self._net_prev_t = total, now
            return rate
        except Exception:
            return None


def clamp_bar(value: float | None) -> float:
    """Clamp a 0..100 value for bar painting; None → 0.0."""
    if value is None:
        return 0.0
    return float(min(100.0, max(0.0, value)))
