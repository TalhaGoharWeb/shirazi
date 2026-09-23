"""core/agent/tools_browser.py — browser automation tools (Phase 6).

Preferred engine: **Playwright** (free, open-source, per the mission spec).
Playwright is an OPTIONAL dependency: when it is not installed the tools
exist, report honest unavailability, and give install instructions instead
of crashing.

Honest degradation map (no faking):
    browser_open      — Playwright; fallback: static HTTP fetch of the page
                        (labelled as such — no JS, no clicking).
    browser_search    — delegates to the existing `web_search` action
                        (ddgs-based, keyless); needs no browser at all.
    browser_navigate  — Playwright only; honest "unavailable" otherwise.
    browser_click     — Playwright only; honest "unavailable" otherwise.
    browser_type      — Playwright only; honest "unavailable" otherwise.
    browser_extract   — Playwright; fallback: static fetch + text strip
                        (labelled as static).
    browser_download  — Playwright; fallback: direct urllib download
                        (labelled — no JS-gated downloads).

Permission levels:
    browser_search / browser_extract → READ_ONLY
    browser_open / browser_navigate / browser_download → USER_CONFIRMATION
        (navigating and downloading change what the machine holds/does)
    browser_click / browser_type → USER_CONFIRMATION (automation risk,
        same posture as the existing browser_control tool)
"""

from __future__ import annotations

import re
import urllib.request
from html.parser import HTMLParser
from typing import Any, Callable, Optional

from core import logger
from core.permissions import Level
from core.tools.registry import ToolSpec

_INSTALL_HINT = ("Playwright is not installed. Install it with:\n"
                 "  pip install playwright\n"
                 "  playwright install chromium")


def _playwright():
    """Import playwright lazily; (module | None). Never raises."""
    try:
        from playwright import sync_api  # type: ignore
        return sync_api
    except Exception:
        return None


def _normalise_url(url: str) -> str:
    url = (url or "").strip()
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        url = "https://" + url
    return url


# ── Static-fetch fallback (honest: labelled, no JS) ─────────────────────────

class _TextStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self._buf: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        self._skip = tag in ("script", "style", "noscript")

    def handle_endtag(self, tag):
        self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "li", "tr"):
            self._buf.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._buf.append(data)

    def text(self) -> str:
        raw = "".join(self._buf)
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", raw)).strip()


def _static_fetch(url: str, timeout: float = 20.0) -> str:
    req = urllib.request.Request(
        _normalise_url(url),
        headers={"User-Agent": "Mozilla/5.0 (SHIRAZI agent; static fetch)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-requested URL
        raw = resp.read(2_000_000)
    return raw.decode("utf-8", errors="ignore")


def _playwright_run(url: str, script: Callable, timeout_s: int = 30) -> Any:
    """Run `script(page)` in a fresh headless Chromium context. Raises on any
    failure — callers translate to honest messages."""
    sync_api = _playwright()
    if sync_api is None:
        raise RuntimeError(_INSTALL_HINT)
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_default_timeout(timeout_s * 1000)
            page.goto(_normalise_url(url))
            return script(page)
        finally:
            browser.close()


# ── Tool handlers ───────────────────────────────────────────────────────────

def browser_open(params: dict) -> str:
    url = _normalise_url(params.get("url", "") or "")
    if not url:
        return "No URL given."
    logger.executing(f"browser_open {url}")
    if _playwright() is None:
        try:
            html = _static_fetch(url)
            text = _TextStripper()
            text.feed(html)
            body = text.text()[:4000]
            return (f"(Static fetch — Playwright is not installed, so this is "
                    f"the raw page without JavaScript.)\n\n{body}")
        except Exception as e:
            return (f"Could not fetch {url}: {e}\n\n{_INSTALL_HINT}")
    try:
        title = _playwright_run(url, lambda page: page.title())
        return f"Opened {url} — page title: {title!r}"
    except Exception as e:
        return f"Could not open {url} in the browser: {e}"


def browser_navigate(params: dict) -> str:
    url = _normalise_url(params.get("url", "") or "")
    if not url:
        return "No URL given."
    if _playwright() is None:
        return f"Browser navigation needs Playwright.\n{_INSTALL_HINT}"
    try:
        title = _playwright_run(url, lambda page: page.title())
        return f"Navigated to {url} — page title: {title!r}"
    except Exception as e:
        return f"Navigation to {url} failed: {e}"


def browser_click(params: dict) -> str:
    if _playwright() is None:
        return f"Clicking needs a real browser session (Playwright).\n{_INSTALL_HINT}"
    url = _normalise_url(params.get("url", "") or "")
    selector = (params.get("selector", "") or "").strip()
    text = (params.get("text", "") or "").strip()
    if not url:
        return "No URL given."
    if not selector and not text:
        return "Give a CSS `selector` or visible `text` to click."
    def _do(page):
        if selector:
            page.click(selector)
        else:
            page.get_by_text(text, exact=False).first.click()
        page.wait_for_timeout(1500)
        return page.title()
    try:
        title = _playwright_run(url, _do)
        return f"Clicked on {url} — now showing: {title!r}"
    except Exception as e:
        return f"Click failed on {url}: {e}"


def browser_type(params: dict) -> str:
    if _playwright() is None:
        return f"Typing needs a real browser session (Playwright).\n{_INSTALL_HINT}"
    url = _normalise_url(params.get("url", "") or "")
    selector = (params.get("selector", "") or "").strip()
    text = params.get("text", "") or ""
    submit = bool(params.get("submit", False))
    if not url:
        return "No URL given."
    if not selector:
        return "Give a CSS `selector` for the input field."
    def _do(page):
        page.fill(selector, text)
        if submit:
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)
        return page.title()
    try:
        title = _playwright_run(url, _do)
        return f"Typed into {url} — now showing: {title!r}"
    except Exception as e:
        return f"Typing failed on {url}: {e}"


def browser_extract(params: dict) -> str:
    url = _normalise_url(params.get("url", "") or "")
    if not url:
        return "No URL given."
    logger.searching(f"browser_extract {url}")
    if _playwright() is None:
        try:
            html = _static_fetch(url)
            stripper = _TextStripper()
            stripper.feed(html)
            body = stripper.text()[:6000]
            return (f"(Static extraction — Playwright is not installed, so "
                    f"JavaScript-rendered content is missing.)\n\n{body}")
        except Exception as e:
            return f"Could not extract {url}: {e}\n\n{_INSTALL_HINT}"
    try:
        def _do(page):
            return page.evaluate("() => document.body.innerText")
        body = _playwright_run(url, _do)
        return (body or "")[:6000] or "(The page has no readable text.)"
    except Exception as e:
        return f"Extraction from {url} failed: {e}"


def browser_search(params: dict) -> str:
    query = (params.get("query", "") or "").strip()
    if not query:
        return "No search query given."
    logger.searching(f"browser_search {query!r}")
    try:
        from actions import web_search as _ws
        out = _ws.web_search({"query": query,
                              "mode": (params.get("mode", "search") or "search")},
                             None, None, None)
        return out or "No results."
    except Exception as e:
        return f"Web search failed: {e}"


def browser_download(params: dict) -> str:
    url = _normalise_url(params.get("url", "") or "")
    if not url:
        return "No URL given."
    dest = (params.get("destination", "") or "").strip()
    logger.executing(f"browser_download {url}")
    try:
        from actions import file_controller as _fc
        folder = _fc._resolve_path(dest or "downloads")
        if not _fc._is_safe_path(folder):
            return f"Access denied: {folder}"
        folder.mkdir(parents=True, exist_ok=True)
        name = url.rstrip("/").split("/")[-1].split("?")[0] or "download"
        target = folder / name
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (SHIRAZI agent)"})
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(target, "wb") as fh:  # noqa: S310 — user-confirmed download URL
            fh.write(resp.read(200_000_000))
        note = "" if _playwright() else " (direct download — Playwright not installed)"
        return f"Downloaded to {target}{note}"
    except Exception as e:
        return f"Download failed: {e}"


# ── Registry specs ──────────────────────────────────────────────────────────

_SPECS: list[tuple[str, str, Level, dict, Callable]] = [
    ("browser_open",
     "Open a URL in the browser. With Playwright: real page. Without: labelled static fetch.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"url": {"type": "STRING"}}},
     browser_open),
    ("browser_navigate",
     "Navigate the browser to a URL. Needs Playwright installed.",
     Level.USER_CONFIRMATION,
     {"type": "object", "properties": {"url": {"type": "STRING"}}},
     browser_navigate),
    ("browser_click",
     "Click an element (CSS selector or visible text) on a page. Needs Playwright installed.",
     Level.USER_CONFIRMATION,
     {"type": "object",
      "properties": {"url": {"type": "STRING"}, "selector": {"type": "STRING"},
                     "text": {"type": "STRING"}}},
     browser_click),
    ("browser_type",
     "Type text into a page field (CSS selector), optionally submitting. Needs Playwright.",
     Level.USER_CONFIRMATION,
     {"type": "object",
      "properties": {"url": {"type": "STRING"}, "selector": {"type": "STRING"},
                     "text": {"type": "STRING"}, "submit": {"type": "BOOLEAN"}}},
     browser_type),
    ("browser_extract",
     "Extract readable text from a page. With Playwright: rendered text. Without: labelled static extraction.",
     Level.READ_ONLY,
     {"type": "object", "properties": {"url": {"type": "STRING"}}},
     browser_extract),
    ("browser_search",
     "Search the web (keyless) and return results. Read-only.",
     Level.READ_ONLY,
     {"type": "object",
      "properties": {"query": {"type": "STRING"}, "mode": {"type": "STRING"}}},
     browser_search),
    ("browser_download",
     "Download a file to the downloads folder. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     {"type": "object",
      "properties": {"url": {"type": "STRING"}, "destination": {"type": "STRING"}}},
     browser_download),
]


def specs() -> list[ToolSpec]:
    return [ToolSpec(name=n, category="browser", permission=l, description=d,
                     schema=s, source="agent")
            for n, d, l, s, _h in _SPECS]


def dispatch() -> dict[str, Callable]:
    return {n: h for n, _d, _l, _s, h in _SPECS}


def tool_names() -> list[str]:
    return [n for n, _d, _l, _s, _h in _SPECS]


def playwright_available() -> tuple[bool, str]:
    """Honest capability probe for docs/UI/diagnostics."""
    if _playwright() is not None:
        return True, "Playwright is installed."
    return False, _INSTALL_HINT
