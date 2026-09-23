"""core/agent/tools_files.py — file tools for the agent engine (Phase 6).

Nine fine-grained tools reusing `actions/file_controller.py` (which is
already safe-path-gated, undo-aware and trash-based) and
`actions/file_processor.py` for document reading. Registered through the
Phase-4 central registry with per-operation permission levels — dangerous
operations (delete/move/rename/copy/write) REQUIRE USER_CONFIRMATION via
the permission engine; reads are READ_ONLY.

Permission levels:
    search_files / read_file / open_file / summarize_file → READ_ONLY
    create_file / rename_file / move_file / copy_file /
    delete_file / file_organize                        → USER_CONFIRMATION
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from core import logger
from core.permissions import Level
from core.tools.registry import ToolSpec


def _fc():
    """Lazy import: actions/file_controller.py is headless-safe, but keep the
    agent package importable even if an action module ever grows a UI dep."""
    from actions import file_controller as fc
    return fc


def _schema(**props) -> dict:
    return {"type": "object", "properties": props}


# ── Handlers ──────────────────────────────────────────────────────────────

def search_files(params: dict) -> str:
    fc = _fc()
    logger.searching(f"search_files name={params.get('name')} ext={params.get('extension')}")
    return fc.find_files(
        name=params.get("name", "") or "",
        extension=params.get("extension", "") or "",
        path=params.get("path", "home") or "home",
        max_results=min(int(params.get("max_results", 20) or 20), 50),
    )


def read_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"read_file {params.get('name') or params.get('path')}")
    return fc.read_file(
        params.get("path", "home") or "home",
        name=params.get("name", "") or "",
        max_chars=int(params.get("max_chars", 8000) or 8000),
    )


def create_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"create_file {params.get('name')}")
    return fc.create_file(
        params.get("path", "desktop") or "desktop",
        name=params.get("name", "") or "",
        content=params.get("content", "") or "",
    )


def rename_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"rename_file {params.get('name')} -> {params.get('new_name')}")
    return fc.rename_file(
        params.get("path", "home") or "home",
        name=params.get("name", "") or "",
        new_name=params.get("new_name", "") or "",
    )


def move_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"move_file {params.get('name')} -> {params.get('destination')}")
    return fc.move_file(
        params.get("path", "home") or "home",
        name=params.get("name", "") or "",
        destination=params.get("destination", "") or "",
    )


def copy_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"copy_file {params.get('name')} -> {params.get('destination')}")
    return fc.copy_file(
        params.get("path", "home") or "home",
        name=params.get("name", "") or "",
        destination=params.get("destination", "") or "",
    )


def delete_file(params: dict) -> str:
    fc = _fc()
    logger.file_op(f"delete_file {params.get('name')}")
    return fc.delete_file(
        params.get("path", "home") or "home",
        name=params.get("name", "") or "",
    )


def open_file(params: dict) -> str:
    """Open a file or folder with the OS default application (read-only view).
    Resolves through the same safe-path logic as file_controller."""
    fc = _fc()
    base = fc._resolve_path(params.get("path", "home") or "home")
    name = (params.get("name", "") or "").strip()
    target = (base / name) if name else base
    if not fc._is_safe_path(target):
        return f"Access denied: {target}"
    if not target.exists():
        return f"Not found: {target}"
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606 — user-confirmed local file open
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
        logger.file_op(f"open_file {target.name}")
        return f"Opened: {target.name}"
    except Exception as e:
        return f"Could not open '{target.name}': {e}"


def summarize_file(params: dict, *, provider=None) -> str:
    """Read a document and summarize it. Reuses actions/file_processor for
    rich document types (PDF/DOCX/XLSX/PPTX) and falls back to a text read.
    Summarization goes through the provider chain; with no provider the
    honest fallback is an excerpt + key facts, never a fabricated summary."""
    fc = _fc()
    name = (params.get("name", "") or "").strip()
    path = params.get("path", "home") or "home"
    text = ""

    # Prefer the rich document reader for known document types.
    suffix = Path(name).suffix.lower() if name else ""
    rich = suffix in (".pdf", ".docx", ".doc", ".xlsx", ".xls",
                      ".pptx", ".ppt", ".csv", ".txt", ".md", ".json")
    if rich:
        try:
            from actions import file_processor as fp
            base = fc._resolve_path(path)
            target = (base / name) if name else base
            if fc._is_safe_path(target) and target.exists():
                out = fp.file_processor(
                    {"action": "read", "file_path": str(target)}, None, None, None)
                if out and "Could not" not in out[:60]:
                    text = out
        except Exception as e:
            logger.warn("summarize_file", f"rich reader failed: {e}")
    if not text:
        text = fc.read_file(path, name=name, max_chars=12000)
        if text.startswith(("File not found", "Access denied", "Not a file",
                            "Could not read")):
            return text

    logger.thinking(f"summarize_file ({len(text)} chars)")
    summary = _provider_summarize(text, name or "the file", provider=provider)
    return summary


def _provider_summarize(text: str, label: str, *, provider=None) -> str:
    prompt = (f"Summarize the following document in 5-8 bullet points. "
              f"Only use information present in the text — do not invent facts.\n\n"
              f"DOCUMENT ({label}):\n{text[:12000]}")
    try:
        if provider is None:
            from core.providers import registry as _preg
            result = _preg.generate(prompt, timeout_s=45.0)
            summary = result.text or ""
        else:
            summary = provider.generate(prompt, timeout_s=45.0) or ""
        if summary and not summary.startswith("I couldn't"):
            return f"Summary of {label}:\n{summary}"
    except Exception as e:
        logger.warn("summarize_file", f"provider failed: {e}")
    # Honest fallback: excerpt, never a fabricated summary.
    excerpt = text[:1500]
    return (f"No AI provider is available, so here is an excerpt of {label} "
            f"instead of a summary (I will not invent one):\n\n{excerpt}")


def file_organize(params: dict) -> str:
    fc = _fc()
    logger.file_op("file_organize desktop")
    return fc.organize_desktop()


# ── Registry specs ────────────────────────────────────────────────────────

_SPECS: list[tuple[str, str, Level, str, Callable]] = [
    ("search_files",
     "Search for files by name and/or extension under a folder shortcut (desktop, downloads, documents, home). Read-only.",
     Level.READ_ONLY,
     _schema(name={"type": "STRING"}, extension={"type": "STRING", "description": "e.g. .pdf"},
             path={"type": "STRING"}, max_results={"type": "INTEGER"}),
     search_files),
    ("read_file",
     "Read a text file's contents (truncated for long files). Read-only.",
     Level.READ_ONLY,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}, max_chars={"type": "INTEGER"}),
     read_file),
    ("create_file",
     "Create a file with given content. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}, content={"type": "STRING"}),
     create_file),
    ("rename_file",
     "Rename a file or folder. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}, new_name={"type": "STRING"}),
     rename_file),
    ("move_file",
     "Move a file or folder to a destination. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}, destination={"type": "STRING"}),
     move_file),
    ("copy_file",
     "Copy a file or folder to a destination. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}, destination={"type": "STRING"}),
     copy_file),
    ("delete_file",
     "Move a file or folder to the trash (recoverable). Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}),
     delete_file),
    ("open_file",
     "Open a file or folder with the OS default application. Gated: opening a file may launch an executable.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}),
     open_file),
    ("summarize_file",
     "Summarize a document (PDF/DOCX/XLSX/PPTX/text). Read-only; honest excerpt if no AI provider.",
     Level.READ_ONLY,
     _schema(path={"type": "STRING"}, name={"type": "STRING"}),
     summarize_file),
    ("file_organize",
     "Sort desktop files into type folders. Reversible via undo. Needs user confirmation.",
     Level.USER_CONFIRMATION,
     _schema(path={"type": "STRING"}),
     file_organize),
]


def specs() -> list[ToolSpec]:
    out = []
    for name, desc, level, schema, _handler in _SPECS:
        out.append(ToolSpec(name=name, category="files", permission=level,
                            description=desc, schema=schema, source="agent"))
    return out


def dispatch() -> dict[str, Callable]:
    return {name: handler for name, _d, _l, _s, handler in _SPECS}


def tool_names() -> list[str]:
    return [name for name, _d, _l, _s, _h in _SPECS]
