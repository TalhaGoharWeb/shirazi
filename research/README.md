# research/ — Shirazi hadith library search

This folder makes the Shirazi assistant able to search the hadith
library (25 Aljam3 categories, 8 FTS5 parts, 20,299 books,
14,529,397 built pages) as ONE library.

## Layout

- `hadith-library/search_all.py` — unified search over all 8 part DBs
  (phrase matches first, then BM25 within each tier, part-tagged, title + author + page per hit). This is
  the maintained copy; the same file lives in the shirazi-research
  checkout, where the parts were built and audited.
- `hadith-library/part*_SHA256SUMS.txt` — per-chunk + whole-file
  SHA-256 for every part (required for reassembly verification).
- `hadith-library/HADITH_REPORT.md` — full part table, audits,
  Drive folders, hashes, digitized-page caveat.
- `shirazi_research/` — runtime search support ONLY:
  `textnorm.py` + `corpus/arabic_norm.py` (query normalization,
  the single thing search_all.py imports). `corpus/__init__.py` is
  intentionally minimal so this import does not pull the full corpus
  layer (schemas, bok_reader, indexer). Builders, single-DB
  `search.py` and manifests stay in the shirazi-research repo;
  they are not needed to run search.

## What is NOT in this repo (on purpose)

- **No `.sqlite` part DBs** (~111 GB total) and **no TXT files**
  (38,285 files). They live on Google Drive (see LAPTOP_SETUP.md)
  and are downloaded + reassembled on the machine that runs Shirazi.
  `.gitignore` blocks `research/hadith-library/*.sqlite` so a future
  `git add` can never stage a downloaded part by accident.
- **No secrets.** API keys live in `config/api_keys.json`, ignored
  by git (see the repo `.gitignore`).

## How Shirazi finds the parts

`plugins/hadith_search.py` (auto-discovered at startup) resolves the
parts dir in order: `HADITH_PARTS_DIR` env → this bundled
`research/hadith-library/` (preferred once parts are downloaded
there) → `SHIRAZI_RESEARCH_DIR` checkout → the default research
checkout. Parts not present locally are reported as missing, never
silently dropped, and the plugin never downloads a part itself.

## Docs

- `../LAPTOP_SETUP.md` — run this project on a laptop, fetch the
  parts from Drive, connect Telegram / WhatsApp (phase 2).
- `../RESEARCH_RULES.md` — binding research rules (madhhab policy,
  citations, caveats, venue, free-first).
