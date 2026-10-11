"""
SHIRAZI plugin: daily_briefing.

The classic Jarvis morning ritual — one spoken summary covering weather,
today's reminders, and system status. Inspired by the "daily briefing"
pattern found across Jarvis-class assistants; implemented clean-room for
SHIRAZI's plugin contract.

Free: weather via the no-key Open-Meteo API; reminders/system from local data.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import urllib.request as _urlreq

PLUGIN = {
    "name": "daily_briefing",
    "description": (
        "Give the morning briefing: weather, today's reminders and system "
        "status in one spoken summary. Trigger on phrases like 'brief me', "
        "'morning briefing', 'daily briefing', 'start my day'. Do NOT use "
        "for weather-only questions (use the weather tool instead)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "city": {
                "type": "STRING",
                "description": "City for the weather section (default: Lahore).",
            },
        },
        "required": [],
    },
}


def _weather(city: str) -> str:
    try:
        geo_q = _urlreq.quote(city)
        with _urlreq.urlopen(
            f"https://geocoding-api.open-meteo.com/v1/search?name={geo_q}&count=1",
            timeout=10,
        ) as r:
            g = _json.loads(r.read().decode("utf-8"))
        if not g.get("results"):
            return f"Weather for {city} is unavailable right now."
        loc = g["results"][0]
        lat, lon, name = loc["latitude"], loc["longitude"], loc.get("name", city)
        with _urlreq.urlopen(
            "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
            "&current=temperature_2m,weather_code&daily=temperature_2m_max,"
            "temperature_2m_min&timezone=auto" % (lat, lon),
            timeout=10,
        ) as r:
            w = _json.loads(r.read().decode("utf-8"))
        cur = w.get("current", {})
        daily = w.get("daily", {})
        hi = (daily.get("temperature_2m_max") or [None])[0]
        lo = (daily.get("temperature_2m_min") or [None])[0]
        code = cur.get("weather_code", -1)
        desc = {
            0: "clear skies", 1: "mostly clear", 2: "partly cloudy",
            3: "overcast", 45: "fog", 48: "icy fog", 51: "light drizzle",
            61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
            77: "snow grains", 80: "light showers", 95: "thunderstorms",
        }.get(code, "changing conditions")
        t = cur.get("temperature_2m")
        parts = [f"{name}: {desc}"]
        if t is not None:
            parts.append(f"currently {round(t)}°C")
        if hi is not None and lo is not None:
            parts.append(f"high {round(hi)}°, low {round(lo)}°")
        return ", ".join(parts) + "."
    except Exception:
        return f"Weather for {city} is unavailable right now."


def _reminders_today() -> str:
    try:
        from memory.config_manager import load_api_keys
        rems = load_api_keys().get("reminders", []) or []
        today = _dt.date.today().isoformat()
        hits = [r for r in rems
                if isinstance(r, dict) and str(r.get("date", ""))[:10] <= today
                and not r.get("done")]
        if not hits:
            return "No reminders due today."
        items = "; ".join(str(r.get("text", "?"))[:80] for r in hits[:5])
        more = f" and {len(hits) - 5} more" if len(hits) > 5 else ""
        return f"{len(hits)} reminder(s): {items}{more}."
    except Exception:
        return "Reminders unavailable."


def _system_status() -> str:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent
        return (f"System nominal: CPU {cpu:.0f}%, memory {mem:.0f}%, "
                f"disk {disk:.0f}%.")
    except Exception:
        return "System status unavailable."


def run(parameters: dict, player=None, session_memory=None) -> str:
    city = str(parameters.get("city") or "Lahore").strip() or "Lahore"
    now = _dt.datetime.now()
    greeting = ("Good morning" if now.hour < 12 else
                "Good afternoon" if now.hour < 17 else "Good evening")
    date_s = now.strftime("%A, %d %B %Y")
    parts = [
        f"{greeting}, sir. Briefing for {date_s}.",
        _weather(city),
        _reminders_today(),
        _system_status(),
    ]
    text = " ".join(parts)
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
