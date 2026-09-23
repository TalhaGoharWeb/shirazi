"""core/agent/intent.py — intent detection (Phase 6).

Rule-based, deterministic, zero-network: the first stage of the agent loop.
It turns a natural-language request into an Intent + extracted slots so the
planner can build an explainable plan. Anything ambiguous lands on
Intent.CONVERSATION (the model answers directly) or Intent.UNKNOWN (ask for
clarification) — never a guessed tool call.

Slots are plain dicts; the planner decides how to use them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Intent(str, Enum):
    BROWSER_OPEN = "browser_open"        # open a URL / website
    APP_OPEN = "app_open"                # launch an application
    WEB_SEARCH = "web_search"            # search the web
    COMPOUND = "compound"                # "open X and search for Y" — split into sub-intents
    FILE_FIND = "file_find"              # locate files by name/type/recency
    FILE_READ = "file_read"              # read a file's contents
    FILE_CREATE = "file_create"          # create/write a file
    FILE_DELETE = "file_delete"          # delete a file (gated)
    FILE_MOVE = "file_move"              # move/rename/copy a file (gated)
    FILE_ORGANIZE = "file_organize"      # organize a folder
    FOLDER_OPEN = "folder_open"          # show a folder to the user
    FILE_SUMMARIZE = "file_summarize"    # summarize a document
    SCREEN_SUMMARIZE = "screen_summarize"  # "summarize what's on my screen"
    SCREEN_ASK = "screen_ask"            # question about the screen
    VOLUME = "volume"                    # up / down / set / mute
    MEDIA = "media"                      # play / pause / next
    SYSTEM = "system"                    # cpu / ram / disk / battery / uptime / processes
    COMPUTER = "computer"                # mouse / keyboard / click / scroll / window
    CLIPBOARD = "clipboard"              # copy to / read clipboard
    RESEARCH = "research"                # deep research with citations
    SHELL = "shell"                      # run a shell command (always gated)
    MEMORY_SAVE = "memory_save"
    MEMORY_RECALL = "memory_recall"
    REMINDER = "reminder"
    MESSAGE = "message"                  # send a message (gated)
    SETTING = "setting"                  # brightness / wifi / dark mode
    NOTIFY = "notify"                    # show a notification
    CONVERSATION = "conversation"        # plain chat — no tools
    UNKNOWN = "unknown"                  # could not understand


@dataclass
class IntentResult:
    intent: Intent
    slots: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    raw: str = ""
    # For COMPOUND: the ordered sub-intent results, e.g. [APP_OPEN, WEB_SEARCH].
    sub_intents: list["IntentResult"] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.intent not in (Intent.UNKNOWN,)


# ── Lexicons ────────────────────────────────────────────────────────────────

_VOLUME_WORDS = ("volume", "sound", "audio", "loud", "louder", "quieter",
                 "mute", "unmute", "loudness")
_MEDIA_WORDS = ("play", "pause", "resume", "next track", "previous track",
                "skip", "stop music", "stop the music")
_SCREEN_WORDS = ("screen", "display", "monitor", "on screen", "my screen",
                 "desktop screen")
_SYSTEM_WORDS = ("cpu", "ram", "memory usage", "disk", "battery", "uptime",
                 "processes", "performance", "temperature", "how much memory",
                 "storage")
_RESEARCH_TRIGGERS = ("research", "deep dive", "investigate", "compare",
                      "pros and cons", "in detail", "comprehensive")
_SHELL_TRIGGERS = ("run command", "run the command", "execute command",
                   "shell command", "in the terminal", "via terminal")
_KNOWN_APPS = ("chrome", "firefox", "edge", "notepad", "calculator", "vscode",
               "vs code", "word", "excel", "spotify", "terminal", "cmd",
               "powershell", "explorer", "settings", "camera")
_URL_RE = re.compile(r"(https?://\S+|www\.\S+|[a-z0-9\-]+\.(com|org|net|io|gov|edu|pk|co|dev)\b\S*)", re.I)


def _strip(text: str) -> str:
    return " ".join(text.lower().strip().split())


def detect_intent(text: str) -> IntentResult:
    """Classify a request. Pure function: deterministic, no I/O, no model."""
    raw = text or ""
    t = _strip(raw)
    if not t:
        return IntentResult(Intent.UNKNOWN, {}, 0.0, raw)

    # Compound first: "open chrome and search for today's weather".
    compound = _detect_compound(t, raw)
    if compound is not None:
        return compound

    single = _detect_single(t, raw)
    return single


def _detect_compound(t: str, raw: str) -> Optional[IntentResult]:
    parts = re.split(r"\s+and\s+|\s+then\s+|;\s*", t)
    if len(parts) < 2:
        return None
    subs = [_detect_single(p, raw) for p in parts]
    # Only a real compound when at least two parts are actionable tools.
    actionable = [s for s in subs
                  if s.intent not in (Intent.CONVERSATION, Intent.UNKNOWN)]
    if len(actionable) >= 2:
        slots = {"parts": [s.intent.value for s in subs]}
        return IntentResult(Intent.COMPOUND, slots, 0.8, raw, sub_intents=subs)
    return None


def _detect_single(t: str, raw: str) -> IntentResult:
    # ── URLs / browsing ───────────────────────────────────────────────────
    url = _URL_RE.search(t)
    if re.search(r"\b(open|go to|visit|navigate to|browse to|load)\b", t) and url:
        return IntentResult(Intent.BROWSER_OPEN, {"url": url.group(0)}, 0.9, raw)
    # "Search my documents for this topic" — local files, not the web.
    if re.search(r"\b(search|find|look)\b", t) and \
            re.search(r"\b(documents?|my files|downloads?|my computer|this pc)\b", t) and \
            not re.search(r"\b(web|internet|online|google)\b", t):
        path = "downloads" if "download" in t else "documents"
        name = _after(t, ("search", "find", "look for", "look in"))
        name = re.sub(r"\b(my|the|documents?|files?|for|this|topic)\b", " ", name or "")
        return IntentResult(Intent.FILE_FIND,
                            {"name": " ".join(name.split()), "path": path},
                            0.8, raw)
    if re.search(r"\b(search|look up|google|find)\b", t) and re.search(
            r"\b(web|internet|online|google|for)\b", t):
        q = _after(t, ("search for", "search the web for", "look up", "google"))
        return IntentResult(Intent.WEB_SEARCH, {"query": q or t}, 0.85, raw)

    # ── Apps ──────────────────────────────────────────────────────────────
    m = re.search(r"\b(open|launch|start|run)\s+(?:the\s+|my\s+)?([a-z][a-z0-9 .+]{1,30})", t)
    if m:
        name = m.group(2).strip().rstrip(" .")
        if name in _KNOWN_APPS or re.search(r"\b(app|application|program)\b", t):
            return IntentResult(Intent.APP_OPEN, {"app": name}, 0.8, raw)

    # ── Files ─────────────────────────────────────────────────────────────
    if re.search(r"\b(find|locate|search for|where is|where are)\b", t) and \
            re.search(r"\b(file|files|pdf|document|documents|download|folder|photo|image|video)\b", t):
        ext = None
        m_ext = re.search(r"\b(pdf|docx?|xlsx?|pptx?|txt|csv|mp4|mp3|png|jpe?g)\b", t)
        if m_ext:
            ext = "." + m_ext.group(1)
        name = _after(t, ("find", "locate", "search for"))
        name = re.sub(r"\b(the|a|my|file|files|that|i|downloaded|yesterday|today)\b", " ", name or "")
        name = " ".join(name.split())
        path = "downloads" if "download" in t else ("documents" if "document" in t else "home")
        slots = {"name": name, "extension": ext, "path": path}
        if "yesterday" in t or "today" in t or "recent" in t:
            slots["recency"] = "yesterday" if "yesterday" in t else "recent"
        return IntentResult(Intent.FILE_FIND, slots, 0.8, raw)
    if re.search(r"\b(read|show me|display|open)\b", t) and \
            re.search(r"\b(file|document|pdf|text|contents)\b", t) and "screen" not in t:
        name = _after(t, ("read", "show me", "display", "open"))
        return IntentResult(Intent.FILE_READ, {"name": name}, 0.75, raw)
    if re.search(r"\b(summariz|summary of)\b", t) and \
            re.search(r"\b(file|document|pdf|doc)\b", t):
        return IntentResult(Intent.FILE_SUMMARIZE,
                            {"name": _after(t, ("summarize", "summary of"))}, 0.8, raw)
    if re.search(r"\b(create|write|make)\b", t) and \
            re.search(r"\b(file|note|document|script)\b", t):
        return IntentResult(Intent.FILE_CREATE,
                            {"name": _after(t, ("create", "write", "make"))}, 0.75, raw)
    if re.search(r"\b(delete|remove|trash)\b", t) and \
            re.search(r"\b(file|folder|document)\b", t):
        return IntentResult(Intent.FILE_DELETE,
                            {"name": _after(t, ("delete", "remove", "trash"))}, 0.8, raw)
    if re.search(r"\b(move|rename|copy)\b", t) and \
            re.search(r"\b(file|folder|document)\b", t):
        action = "move" if "move" in t else ("rename" if "rename" in t else "copy")
        return IntentResult(Intent.FILE_MOVE, {"action": action}, 0.8, raw)
    if re.search(r"\b(organiz|tidy|clean up)\b", t) and \
            re.search(r"\b(desktop|folder|downloads|files)\b", t):
        return IntentResult(Intent.FILE_ORGANIZE, {}, 0.8, raw)
    if re.search(r"\b(open|show)\b", t) and \
            re.search(r"\b(project folder|my folder|folder)\b", t) and "file" not in t:
        folder = _after(t, ("open", "show"))
        return IntentResult(Intent.FOLDER_OPEN, {"folder": folder}, 0.7, raw)

    # ── Screen / vision ───────────────────────────────────────────────────
    if any(w in t for w in ("what's on", "what is on", "whats on")) and \
            any(w in t for w in _SCREEN_WORDS):
        return IntentResult(Intent.SCREEN_SUMMARIZE, {}, 0.9, raw)
    if re.search(r"\bsummariz", t) and any(w in t for w in _SCREEN_WORDS):
        return IntentResult(Intent.SCREEN_SUMMARIZE, {}, 0.9, raw)
    if any(w in t for w in _SCREEN_WORDS) and \
            re.search(r"\b(see|look|describe|read|what|where)\b", t):
        return IntentResult(Intent.SCREEN_ASK, {"question": raw}, 0.75, raw)

    # ── Volume / media ────────────────────────────────────────────────────
    if any(w in t for w in _VOLUME_WORDS):
        if "mute" in t:
            return IntentResult(Intent.VOLUME, {"op": "mute"}, 0.9, raw)
        if "unmute" in t:
            return IntentResult(Intent.VOLUME, {"op": "unmute"}, 0.9, raw)
        m_num = re.search(r"(\d{1,3})\s*%?", t)
        if m_num and re.search(r"\b(set|to|at)\b", t):
            return IntentResult(Intent.VOLUME, {"op": "set",
                                                "value": int(m_num.group(1))}, 0.9, raw)
        if re.search(r"\b(down|lower|decrease|quieter|reduce)\b", t):
            return IntentResult(Intent.VOLUME, {"op": "down"}, 0.9, raw)
        if re.search(r"\b(up|raise|increase|louder|higher)\b", t):
            return IntentResult(Intent.VOLUME, {"op": "up"}, 0.9, raw)
        return IntentResult(Intent.VOLUME, {"op": "get"}, 0.7, raw)
    if any(w in t for w in _MEDIA_WORDS):
        op = "play_pause"
        if "next" in t or "skip" in t:
            op = "next"
        elif "previous" in t:
            op = "previous"
        return IntentResult(Intent.MEDIA, {"op": op}, 0.8, raw)

    # ── System ────────────────────────────────────────────────────────────
    if any(w in t for w in _SYSTEM_WORDS):
        return IntentResult(Intent.SYSTEM, {}, 0.8, raw)

    # ── Computer control ──────────────────────────────────────────────────
    if re.search(r"\b(click|move (the )?mouse|scroll|press|hotkey|keyboard|type)\b", t):
        return IntentResult(Intent.COMPUTER, {"raw": raw}, 0.7, raw)
    if re.search(r"\b(screenshot|capture)\b", t):
        return IntentResult(Intent.COMPUTER, {"action": "screenshot"}, 0.8, raw)
    if "clipboard" in t or re.search(r"\b(copy .+ to clipboard|what'?s in my clipboard)\b", t):
        op = "read" if re.search(r"\b(read|what|show|get)\b", t) else "write"
        return IntentResult(Intent.CLIPBOARD, {"op": op}, 0.75, raw)

    # ── Research / shell / memory / misc ──────────────────────────────────
    if any(w in t for w in _RESEARCH_TRIGGERS) or \
            re.search(r"\b(research|report on|write a report)\b", t):
        topic = _after(t, ("research", "report on", "write a report about",
                           "investigate", "deep dive into"))
        return IntentResult(Intent.RESEARCH, {"topic": topic or t}, 0.8, raw)
    if any(w in t for w in _SHELL_TRIGGERS):
        cmd = _after(t, _SHELL_TRIGGERS)
        return IntentResult(Intent.SHELL, {"command": cmd}, 0.8, raw)
    if re.search(r"\b(remember|don'?t forget|save this)\b", t):
        return IntentResult(Intent.MEMORY_SAVE, {"text": raw}, 0.8, raw)
    if re.search(r"\b(what do you remember|recall|do you remember)\b", t):
        return IntentResult(Intent.MEMORY_RECALL, {"query": t}, 0.8, raw)
    if re.search(r"\b(remind me|set (a )?reminder|alarm)\b", t):
        return IntentResult(Intent.REMINDER, {"text": raw}, 0.8, raw)
    if re.search(r"\b(send|message|whatsapp|telegram)\b", t) and \
            re.search(r"\b(to|message)\b", t):
        return IntentResult(Intent.MESSAGE, {"text": raw}, 0.7, raw)
    if re.search(r"\b(brightness|wifi|wi-fi|dark mode|night light)\b", t):
        return IntentResult(Intent.SETTING, {"text": raw}, 0.75, raw)
    if re.search(r"\b(notify|notification|alert me)\b", t):
        return IntentResult(Intent.NOTIFY, {"text": raw}, 0.7, raw)

    # ── Plain conversation ────────────────────────────────────────────────
    if re.search(r"^(hi|hello|hey|thanks|thank you|bye|goodbye|ok|okay|yes|no|sure)\b", t) \
            or len(t.split()) <= 3:
        return IntentResult(Intent.CONVERSATION, {}, 0.6, raw)

    return IntentResult(Intent.UNKNOWN, {}, 0.0, raw)


def _after(t: str, triggers: tuple[str, ...]) -> str:
    """Text after the first matching trigger phrase, filler words trimmed."""
    for trig in triggers:
        idx = t.find(trig)
        if idx != -1:
            rest = t[idx + len(trig):].strip(" :,-")
            rest = re.sub(r"^(the|a|an|my|this|that|for|me|to)\s+", "", rest)
            return rest.strip()
    return ""
