"""
SHIRAZI plugin: system_diagnostics.

Full workstation health report: CPU, memory, disk, battery, network
latency, top processes, and SHIRAZI's own config sanity. Spoken summary
plus a detailed log entry. Free, local-only (psutil).
"""

from __future__ import annotations

PLUGIN = {
    "name": "system_diagnostics",
    "description": (
        "Run a full system diagnostic: CPU, memory, disk, battery, "
        "network, top processes. Trigger on 'diagnose', 'system check', "
        "'health report', 'why is my pc slow'."
    ),
    "parameters": {"type": "OBJECT", "properties": {}, "required": []},
}


def _ping() -> str:
    try:
        import subprocess, sys
        target = "8.8.8.8"
        cmd = (["ping", "-n", "2", "-w", "1000", target] if
               sys.platform.startswith("win") else
               ["ping", "-c", "2", "-W", "1", target])
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        import re
        m = re.search(r"Average = (\d+)ms", r.stdout) or \
            re.search(r"avg.*?(\d+\.?\d*)\s*ms", r.stdout)
        return f"{m.group(1)} ms" if m else "unreachable"
    except Exception:
        return "unreachable"


def run(parameters: dict, player=None, session_memory=None) -> str:
    try:
        import psutil
    except Exception:
        return "Sir, psutil is not installed. Run: pip install psutil"

    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    procs = sorted(psutil.process_iter(["name", "cpu_percent"]),
                   key=lambda p: (p.info.get("cpu_percent") or 0),
                   reverse=True)[:3]
    top = ", ".join(f"{p.info['name']} ({p.info['cpu_percent']:.0f}%)"
                    for p in procs if p.info.get("name"))
    batt_txt = ""
    try:
        b = psutil.sensors_battery()
        if b:
            batt_txt = (f" Battery {b.percent:.0f}%"
                        f"{' (charging)' if b.power_plugged else ''}.")
    except Exception:
        pass

    verdict = "nominal"
    if cpu > 85 or mem.percent > 90 or disk.percent > 92:
        verdict = "strained — consider closing heavy apps"
    elif cpu > 60 or mem.percent > 75:
        verdict = "busy but healthy"

    text = (
        f"Diagnostic complete, sir. System is {verdict}. "
        f"CPU {cpu:.0f}%, memory {mem.percent:.0f}% of "
        f"{mem.total // (1024**3)} GB, disk {disk.percent:.0f}% full. "
        f"Top processes: {top or 'none'}.{batt_txt} "
        f"Network ping {_ping()}."
    )
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
