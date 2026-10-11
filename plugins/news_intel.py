"""
SHIRAZI plugin: news_intel.

Spoken news briefing from free RSS feeds — no API key needed.
Topics: world | technology | pakistan | business | science.
Keeps it to 5 headlines so it stays a briefing, not a broadcast.
"""

from __future__ import annotations

import html as _html
import re as _re
import urllib.request as _urlreq
import xml.etree.ElementTree as _ET

PLUGIN = {
    "name": "news_intel",
    "description": (
        "Read the latest news headlines as a short spoken briefing. "
        "Trigger on 'news', 'headlines', 'tech news', 'Pakistan news'. "
        "Optional topic: world | technology | pakistan | business | science."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "topic": {
                "type": "STRING",
                "description": "world | technology | pakistan | business | science (default world).",
            },
        },
        "required": [],
    },
}

_FEEDS = {
    "world": "http://feeds.bbci.co.uk/news/world/rss.xml",
    "technology": "http://feeds.bbci.co.uk/news/technology/rss.xml",
    "business": "http://feeds.bbci.co.uk/news/business/rss.xml",
    "science": "http://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "pakistan": "https://www.dawn.com/feed",
}

_UA = {"User-Agent": "SHIRAZI-news-intel/1.0"}


def _clean(s: str) -> str:
    s = _html.unescape(s or "")
    s = _re.sub(r"<[^>]+>", "", s)
    return _re.sub(r"\s+", " ", s).strip()


def run(parameters: dict, player=None, session_memory=None) -> str:
    topic = str(parameters.get("topic") or "world").strip().lower()
    url = _FEEDS.get(topic, _FEEDS["world"])
    try:
        req = _urlreq.Request(url, headers=_UA)
        with _urlreq.urlopen(req, timeout=12) as r:
            data = r.read()
        root = _ET.fromstring(data)
        items = root.findall(".//item")[:5]
        heads = [_clean(it.findtext("title")) for it in items]
        heads = [h for h in heads if h]
        if not heads:
            raise ValueError("empty feed")
        text = (f"Top {topic} headlines, sir: " +
                " … ".join(heads) + ".")
    except Exception:
        text = f"Sir, the {topic} news feed is unreachable right now."
    if player:
        try:
            player.write_log(f"SHIRAZI: {text}")
        except Exception:
            pass
    return text
