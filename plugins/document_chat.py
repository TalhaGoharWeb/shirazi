"""
SHIRAZI plugin: document_chat.

Ask questions about a PDF or text document: extracts the text locally
(pypdf) and returns the most relevant passages for the question.
Fully offline — the document never leaves the machine. The LLM then
reasons over the returned passages.

For very long documents only the top-matching chunks are returned, so
set 'max_chars' if you need more context (default 4000).
"""

from __future__ import annotations

import os as _os
import re as _re
from pathlib import Path as _Path

PLUGIN = {
    "name": "document_chat",
    "description": (
        "Answer questions about a local PDF or text file: extracts passages "
        "relevant to the question. Trigger on 'ask this pdf', 'summarize "
        "this document', 'what does <file> say about <topic>'. Pass the "
        "full file path as 'path' and the question as 'question'. Do NOT "
        "use for web pages."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "path": {
                "type": "STRING",
                "description": "Full local path to the PDF/txt/md file.",
            },
            "question": {
                "type": "STRING",
                "description": "Question about the document (or 'summarize').",
            },
            "max_chars": {
                "type": "NUMBER",
                "description": "Max passage chars to return (default 4000).",
            },
        },
        "required": ["path", "question"],
    },
}

_STOP = set(
    "the a an and or of to in on for with is are was were be by as at from "
    "this that it its they them he she we you i my me our your his her their "
    "not no yes do does did will would can could should have has had than "
    "then so such what which who whom whose when where why how".split())


def _read(path: _Path) -> str:
    suf = path.suffix.lower()
    if suf == ".pdf":
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        try:
            reader = PdfReader(str(path))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages)
        except Exception:
            return ""
    if suf in (".txt", ".md", ".rst", ".log"):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    return ""


def _chunks(text: str, size: int = 900) -> list[str]:
    paras = [p.strip() for p in _re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n" + p).strip()
    if cur:
        out.append(cur)
    return out


def _score(chunk: str, terms: list[str]) -> int:
    low = chunk.lower()
    return sum(low.count(t) for t in terms)


def run(parameters: dict, player=None, session_memory=None) -> str:
    raw_path = str(parameters.get("path") or "").strip().strip('"')
    question = str(parameters.get("question") or "").strip()
    try:
        max_chars = int(parameters.get("max_chars") or 4000)
    except Exception:
        max_chars = 4000
    max_chars = max(500, min(12000, max_chars))

    path = _Path(_os.path.expandvars(raw_path)).expanduser()
    if not path.is_file():
        return f"Sir, I could not find the file: {raw_path}"
    text = _read(path)
    if not text.strip():
        return (f"Sir, I could not extract readable text from "
                f"{path.name} (unsupported or scanned PDF).")

    terms = [t.lower() for t in _re.findall(r"[a-zA-Z]{3,}", question)
             if t.lower() not in _STOP]
    chunks = _chunks(text)
    if question.strip().lower() in ("summarize", "summary", "summarise"):
        picked = chunks[:3]
        header = f"Summary passages from {path.name}:"
    elif not terms:
        picked = chunks[:2]
        header = f"Opening passages from {path.name}:"
    else:
        ranked = sorted(chunks, key=lambda c: _score(c, terms), reverse=True)
        picked = [c for c in ranked[:4] if _score(c, terms) > 0] or chunks[:2]
        header = f"Relevant passages from {path.name}:"

    body, total = [], 0
    for c in picked:
        if total + len(c) > max_chars:
            c = c[: max(200, max_chars - total)] + "…"
        body.append(c)
        total += len(c)
        if total >= max_chars:
            break
    result = header + "\n\n" + "\n\n---\n\n".join(body)
    if player:
        try:
            player.write_log(f"SHIRAZI: document_chat on {path.name}")
        except Exception:
            pass
    return result
