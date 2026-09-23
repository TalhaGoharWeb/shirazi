"""core/agent/tools_system.py — system & media tools (Phase 6).

Reuses:
    core/telemetry.py            — throttled CPU/RAM/disk/battery/uptime/network
                                   with honest "n/a" when a sensor is missing.
    actions/system_monitor.py    — get_system_status() for the detailed view.
    actions/computer_settings.py — volume + media playback actions.
    core/devices.py (Phase 4)    — mic/speaker selection (device manager).

Permission levels:
    system_stats / processes / battery_status / network_status /
    volume_get                                             → SAFE (reads)
    volume_up / volume_down / volume_set / volume_mute /
    volume_unmute / media_control                         → USER_CONFIRMATION
        (they change audible state; reversible via undo where supported)
"""

from __future__ import annotations

from typing import Any, Callable

from core import logger
from core.permissions import Level
from core.tools.registry import ToolSpec


# ── System (reads — reuse telemetry + system_monitor) ───────────────────────

def _sampler():
    from core.telemetry import TelemetrySampler
    return TelemetrySampler(min_interval=0.25)


def system_stats(params: dict) -> str:
    """Full system snapshot: CPU, RAM, disk, battery, uptime, network."""
    logger.system("system_stats")
    lines: list[str] = []
    try:
        from actions import system_monitor as _sm
        st = _sm.get_system_status()
        lines.append(f"CPU: {st.get('cpu_percent')}%")
        lines.append(f"RAM: {st.get('ram_percent')}% "
                     f"({st.get('ram_used_gb')} / {st.get('ram_total_gb')} GB)")
        if st.get("cpu_temp_c"):
            lines.append(f"CPU temp: {st.get('cpu_temp_c')}°C")
        if st.get("gpu_percent") is not None:
            lines.append(f"GPU: {st.get('gpu_percent')}%")
        lines.append(f"Uptime: {st.get('uptime')}")
        lines.append(f"Processes: {st.get('process_count')}")
    except Exception as e:
        lines.append(f"(detailed monitor unavailable: {e})")
    try:
        from core.telemetry import format_uptime  # noqa: F401 — re-export guard
        s = _sampler().snapshot()
        if s.net_mbps is not None:
            lines.append(f"Network: {s.net_mbps:.1f} Mbps")
        if s.battery_percent is not None:
            lines.append(f"Battery: {s.battery_percent:.0f}%"
                         + (" (charging)" if s.battery_plugged else ""))
    except Exception as e:
        logger.warn("system_stats", f"telemetry failed: {e}")
    return "\n".join(lines) or "No system data available."


def processes(params: dict) -> str:
    """Top processes by CPU/RAM. Honest when psutil is missing."""
    logger.system("processes")
    try:
        import psutil  # type: ignore
    except Exception:
        return ("psutil is not installed, so I cannot list processes. "
                "Install it with: pip install psutil")
    try:
        count = max(1, min(int(params.get("count", 8) or 8), 25))
        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                with p.oneshot():
                    procs.append((p.cpu_percent(interval=0.0) or 0.0,
                                  p.memory_percent() or 0.0,
                                  p.info["pid"], p.info["name"] or "?"))
            except Exception:
                continue
        procs.sort(reverse=True)
        lines = [f"{'CPU%':>6} {'MEM%':>6}  PID      NAME"]
        for cpu, mem, pid, name in procs[:count]:
            lines.append(f"{cpu:>6.1f} {mem:>6.1f}  {pid:<8} {name[:40]}")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not list processes: {e}"


def battery_status(params: dict) -> str:
    logger.system("battery_status")
    try:
        s = _sampler().snapshot()
        if s.battery_percent is None:
            return "No battery sensor on this machine."
        state = "charging" if s.battery_plugged else "on battery"
        return f"Battery: {s.battery_percent:.0f}% ({state})"
    except Exception as e:
        return f"Could not read battery: {e}"


def network_status(params: dict) -> str:
    logger.system("network_status")
    try:
        s = _sampler().snapshot()
        if s.net_mbps is None:
            return "Network rate unavailable (n/a)."
        return f"Network: {s.net_mbps:.1f} Mbps"
    except Exception as e:
        return f"Could not read network stats: {e}"


# ── Volume / media (reuse actions/computer_settings) ────────────────────────

def _settings_action(action: str, value: Any = None) -> str:
    try:
        from actions import computer_settings as _cs
    except Exception as e:
        return f"computer_settings unavailable: {e}"
    params = {"action": action}
    if value is not None:
        params["value"] = value
    try:
        return _cs.computer_settings(params, None, None, None) or "Done."
    except Exception as e:
        return f"Volume control failed: {e}"


def volume_get(params: dict) -> str:
    logger.system("volume_get")
    return _settings_action("volume_get")


def volume_up(params: dict) -> str:
    logger.system("volume_up")
    return _settings_action("volume_up")


def volume_down(params: dict) -> str:
    logger.system("volume_down")
    return _settings_action("volume_down")


def volume_set(params: dict) -> str:
    value = max(0, min(100, int(params.get("value", 50) or 0)))
    logger.system(f"volume_set {value}%")
    return _settings_action("volume_set", value)


def volume_mute(params: dict) -> str:
    logger.system("volume_mute")
    return _settings_action("volume_mute")


def volume_unmute(params: dict) -> str:
    logger.system("volume_unmute")
    return _settings_action("volume_unmute")


def media_control(params: dict) -> str:
    op = (params.get("op", "play_pause") or "play_pause").lower()
    logger.system(f"media_control {op}")
    mapping = {"play_pause": "media_play_pause", "play": "media_play_pause",
               "pause": "media_play_pause", "next": "media_next",
               "previous": "media_previous"}
    action = mapping.get(op, "media_play_pause")
    return _settings_action(action)


# ── Registry specs ──────────────────────────────────────────────────────────

_SPECS: list[tuple[str, str, Level, dict, Callable]] = [
    ("system_stats",
     "CPU/RAM/disk/battery/uptime/network/processes snapshot. Read-only.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     system_stats),
    ("processes",
     "Top processes by CPU and memory. Honest 'unavailable' when psutil is missing.",
     Level.SAFE,
     {"type": "object", "properties": {"count": {"type": "INTEGER"}}},
     processes),
    ("battery_status",
     "Battery level and charging state; honest when there is no sensor.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     battery_status),
    ("network_status",
     "Current network throughput; honest 'n/a' when unavailable.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     network_status),
    ("volume_get",
     "Read the current volume. Read-only.",
     Level.SAFE,
     {"type": "object", "properties": {}},
     volume_get),
    ("volume_up",
     "Raise the volume. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {}},
     volume_up),
    ("volume_down",
     "Lower the volume. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {}},
     volume_down),
    ("volume_set",
     "Set the volume to a percentage (0-100). Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"value": {"type": "INTEGER"}}},
     volume_set),
    ("volume_mute",
     "Mute the audio. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {}},
     volume_mute),
    ("volume_unmute",
     "Unmute the audio. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {}},
     volume_unmute),
    ("media_control",
     "Play/pause/next/previous media playback. Needs confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"op": {"type": "STRING"}}},
     media_control),
]


def specs() -> list[ToolSpec]:
    return [ToolSpec(name=n, category="system" if not n.startswith(("volume", "media")) else "media",
                     permission=l, description=d, schema=s, source="agent")
            for n, d, l, s, _h in _SPECS]


def dispatch() -> dict[str, Callable]:
    return {n: h for n, _d, _l, _s, h in _SPECS}


def tool_names() -> list[str]:
    return [n for n, _d, _l, _s, _h in _SPECS]
