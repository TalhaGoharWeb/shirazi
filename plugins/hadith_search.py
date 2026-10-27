"""
SHIRAZI plugin: hadith_search.

Search the Shirazi hadith library — all 8 FTS5 part DBs (20,299 books,
14,529,397 built pages, 25 Aljam3 categories) — as ONE library, using
the maintained unified search in shirazi-research/hadith-library/
search_all.py. Hits are BM25-merged across parts and tagged with their
part file; every hit carries book title + author + page, resolved from
the books table, never just a book_id.

Design notes (read before changing this file):
- This plugin REUSES search_all.py — first from the bundled copy in
  <repo>/research/hadith-library/ (the laptop checkout), else from the
  shirazi-research checkout (env SHIRAZI_RESEARCH_DIR, default
  ~/workspace/shirazi-research). It does NOT vendor a second copy of
  the search logic, so Drive folder ids, SHAs and part sizes stay in
  one maintained place per checkout. If no copy is found, run()
  returns a spoken error instead of raising.
- Part-dir resolution prefers a directory that actually holds part
  DBs, so a laptop checkout with downloaded parts uses its bundled
  dir, while a dev checkout without local parts falls back to the
  research checkout that has them.
- Search-only: parts that are not local are reported as MISSING, never
  silently dropped, and this plugin never downloads a part (a part is
  ~6-18 GB). Fetch a missing part with the search_all.py CLI
  (--fetch-missing N) and re-run.
- Scope: this searches hadith TEXT (supporting dalil). It does NOT give
  fiqh rulings — rulings come exclusively from the selected madhhab's
  authoritative books, never from a hadith hit alone.
- Standing caveat: page numbers are Aljam3 digitized-file pages;
  printed-edition pagination may differ, and key quotations still get
  checked against the original/PDF per the hybrid retrieval policy.
"""

from __future__ import annotations

import importlib.util as _ilu
import os as _os
from pathlib import Path as _Path

PLUGIN = {
    "name": "hadith_search",
    "description": (
        "Search the Shirazi hadith library (Kutub al-Sitta, Masanid, Sira, "
        "Shuruh and 20 more categories, 8 FTS5 parts, 20,299 books) for an "
        "Arabic phrase or hadith text. Trigger on 'find this hadith', "
        "'is hadith ko dhoondo', 'yeh riwayat kahan hai', or any request "
        "for the text of a hadith with its book and page. Pass the Arabic "
        "query as 'query'. Returns matching passages with book title, "
        "author, page and part file. Do NOT use for fiqh rulings — this "
        "returns hadith text only; rulings come from the madhhab books."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "Arabic search phrase (hadith text or keywords).",
            },
            "limit": {
                "type": "NUMBER",
                "description": "Max hits to return (default 5).",
            },
            "parts": {
                "type": "STRING",
                "description": (
                    "Optional comma-separated part numbers to search "
                    "(1-8, default: all parts present locally)."
                ),
            },
        },
        "required": ["query"],
    },
}

_EXCERPT_CHARS = 300
_DEFAULT_LIMIT = 5

_search_all = None
_search_all_error = None


def _repo_root() -> _Path:
    # plugins/ lives directly under the Shirazi repo root.
    return _Path(__file__).resolve().parent.parent


def _candidate_dirs() -> list[_Path]:
    """Ordered part-dir candidates (existing dirs only, deduped)."""
    cands = []
    env_parts = _os.environ.get("HADITH_PARTS_DIR")
    if env_parts:
        cands.append(_Path(env_parts).expanduser())
    cands.append(_repo_root() / "research" / "hadith-library")
    env_research = _os.environ.get("SHIRAZI_RESEARCH_DIR")
    if env_research:
        cands.append(_Path(env_research).expanduser() / "hadith-library")
    cands.append(_Path.home() / "workspace" / "shirazi-research" / "hadith-library")
    seen, out = set(), []
    for c in cands:
        if str(c) not in seen and c.is_dir():
            seen.add(str(c))
            out.append(c)
    return out


def _parts_dir() -> _Path:
    # Prefer a dir that actually holds part DBs; else the first dir
    # with search_all.py (so the missing-parts message stays accurate).
    cands = _candidate_dirs()
    for c in cands:
        if any(c.glob("hadith_part*_fts5.sqlite")):
            return c
    for c in cands:
        if (c / "search_all.py").is_file():
            return c
    return _repo_root() / "research" / "hadith-library"


def _load_search_all():
    """Import search_all.py from the research checkout (cached)."""
    global _search_all, _search_all_error
    if _search_all is not None or _search_all_error is not None:
        return _search_all
    # Try each candidate until one imports: a bundled copy that fails
    # (e.g. a missing normalization module) must fall through to the
    # research checkout instead of failing the whole plugin.
    tried = []
    for c in _candidate_dirs():
        mod_path = c / "search_all.py"
        if not mod_path.is_file():
            continue
        tried.append(str(mod_path))
        try:
            spec = _ilu.spec_from_file_location("hadith_search_all", str(mod_path))
            if spec is None or spec.loader is None:
                continue
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _search_all = mod
            return _search_all
        except Exception:
            continue
    _search_all_error = (
        f"Hadith library search is unavailable: no working search_all.py "
        f"found (tried {tried or 'nothing'}). Set SHIRAZI_RESEARCH_DIR "
        f"to the shirazi-research checkout, or fetch the library first."
    )
    return None


def _parse_parts(raw) -> list[int] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = str(raw).split(",")
    out = []
    for item in items:
        item = item.strip()
        if item.isdigit() and 1 <= int(item) <= 8:
            out.append(int(item))
    return out or None


def run(parameters: dict, player=None, session_memory=None) -> str:
    query = (parameters or {}).get("query", "")
    if isinstance(query, str):
        query = query.strip()
    if not query:
        return "Sir, hadith search ke liye koi Arabic phrase batayein."
    try:
        limit = int((parameters or {}).get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, 10))
    parts = _parse_parts((parameters or {}).get("parts"))

    mod = _load_search_all()
    if mod is None:
        result_text = _search_all_error or "Sir, hadith search unavailable."
        if player:
            try:
                player.write_log(f"SHIRAZI: {result_text}")
            except Exception:
                pass
        return result_text
    try:
        hits, missing = mod.search_all(
            query, limit=limit, parts_dir=_parts_dir(), parts=parts)
    except Exception as e:
        return f"Sir, hadith search failed: {e}"

    lines = [f"Hadith library search: '{query}'"]
    if not hits:
        lines.append("Koi nateeja nahi mila (searched parts mein).")
    for i, h in enumerate(hits, 1):
        excerpt = " ".join(str(h.get("text", "")).split())[:_EXCERPT_CHARS]
        lines.append(
            f"[{i}] Part {h['part']} ({h['part_file']}) | {h['title']} | "
            f"{h['author']} | p.{h['page']} | {h['chapter']}")
        lines.append(f"    {excerpt}")
    if missing:
        lines.append(
            f"Note: parts {missing} Drive par hain, local nahi — un mein "
            f"search nahi hua. Unhein shaamil karne ke liye search_all.py "
            f"se --fetch-missing N chalayein, phir dobara search karein.")
    lines.append(
        "Caveat: page numbers Aljam3 digitized-file pages hain; "
        "printed edition se farq ho sakta hai.")
    result_text = "\n".join(lines)
    if player:
        try:
            player.write_log(f"SHIRAZI: {result_text}")
        except Exception:
            pass
    return result_text
