#!/usr/bin/env python3
"""Unified search over all 8 hadith part DBs (one library).

Each part DB uses the IDENTICAL builder/schema as the Hanafi/Maliki DBs
(build_aljam3_library) and the same search semantics as
shirazi_research.corpus.search.search: the query is normalized exactly
like indexed text (tashkeel stripped, alef/hamza/ya/ta-marbuta unified),
multi-word queries try an exact FTS5 phrase match first, then fall back
to an OR of the terms, and hits are BM25-ranked. This module runs that
same SQL per part, keeps the BM25 score, merges all parts with phrase matches first, then OR matches
(each tier by score ascending), and tags every hit with its part number and part file.

Caveat (honest, not hidden): BM25 scores are computed per index, so a
merged cross-part ordering is the standard multi-index approximation --
within any single part the ranking is exactly search()'s ranking.
Phrase-tier hits always sort before OR-tier hits (verified 2026-10-27:
a Part 7 exact-phrase hit ranked below 10 Part 6 OR hits under pure
BM25 merging, so tiering is what keeps exact matches on top).
Returned passages carry verbatim page text; normalization lives only
in the FTS index. Every hit prints book title + author + page, resolved
from the books table, never just a book_id.

Parts not present locally are NOT silently dropped: they are listed as
missing in the output. Pass --fetch-missing N (one part at a time) to
download + reassemble that part from its Drive folder, verifying
SHA256SUMS.txt before the DB is used. Fetching checks free disk first
and refuses when the part does not fit -- all 8 parts together are
~112 GB, so a full local copy needs a machine bigger than this sandbox
(24 GB free here on 2026-10-27); the normal pattern is to fetch the
parts you need, search, and delete the local .sqlite afterwards (the
Drive copy is the backup).

Reassembly (parts 1-7): cat <db>.part* > <db> && sha256sum -c SHA256SUMS.txt
Part 8 exception: the 1500 MiB chunks for parts 02/03/04 failed to upload
and were shipped as 250 MiB sub-chunks instead, so reassemble as:
  cat part01of04 + sub01..06-of-part02 + sub01..06-of-part03
      + sub01..06-of-part04 > hadith_part8_fts5.sqlite
then sha256sum -c SHA256SUMS.txt (whole-file hash 0730a243...).

Standing caveat: page numbers are Aljam3 digitized-file pages;
printed-edition pagination may differ, and key quotations still get
checked against the original/PDF per the hybrid retrieval policy.
"""
import argparse, hashlib, json, shutil, sqlite3, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from shirazi_research.corpus.arabic_norm import tokenize_arabic

CLI = "/opt/hatch/bin/hatch_gws_cli"
# part -> (db file, Drive folder id, Drive folder name, whole-file sha256 prefix, size bytes)
PARTS = {
    1: ("hadith_part1_fts5.sqlite", "1v0U94AmpJlldT5hY_xz_3ZOEn-FLoPoi", "hadith-part1-fts5-db", "74c9326eebbe", 17616875520),
    2: ("hadith_part2_fts5.sqlite", "17mD1Jz25m6_ryg2-rct9q0RGobpzBTkf", "hadith-part2-fts5-db", "ecfc756b5344", 15021379584),
    3: ("hadith_part3_fts5.sqlite", "1YJE48tVVxqD43A6ZWvSbXjwo3ax_HFT_", "hadith-part3-fts5-db", "c8816bec01b6", 15104733184),
    4: ("hadith_part4_fts5.sqlite", "1IZ0nm8mFZqhmze5-QvpApzzbMpKxUOxY", "hadith-part4-fts5-db", "0350fd8d2191", 14345195520),
    5: ("hadith_part5_fts5.sqlite", "1lZpfn67DH1hWydGLerEOP7m0uAFjR94m", "hadith-part5-fts5-db", "281d0c91580f", 13961285632),
    6: ("hadith_part6_fts5.sqlite", "1vLciend_HNKvnjfHEi6RB2VyRcZ59ZLm", "hadith-part6-fts5-db", "8f861684c47e", 14180155392),
    7: ("hadith_part7_fts5.sqlite", "1L5CvZ9Jd8585Qew0kNQqh2H3W4I96iIN", "hadith-part7-fts5-db", "5c68a62a6e7d", 15020609536),
    8: ("hadith_part8_fts5.sqlite", "1mVZY_XI7hsywte5vFkqQuLp8GP0Hbhr0", "hadith-part8-fts5-db", "0730a243258c", 6199414784),
}
PART_CATS = {
    1: ("kutub_sitta", "adhkar"), 2: ("masanid_ukhra", "raqaaiq_adab"),
    3: ("majmuat", "masanid_aimma"), 4: ("tarajim", "mustalah"),
    5: ("jarh", "fiqh_aam_ref", "maktabat_bahth"),
    6: ("ulum_quran", "fatawa_ref", "mabahith_quran"),
    7: ("sira", "mutun", "shuruh", "adilla_faharis", "ajza"),
    8: ("takhrij", "faharis_hadith", "gharib_maajim", "ulum", "buhuth_masail", "ilal"),
}


def _phrase(tokens):
    esc = [t.replace('"', '""') for t in tokens]
    return '"' + " ".join(esc) + '"'


def _or(tokens):
    esc = [t.replace('"', '""') for t in tokens]
    return " OR ".join(f'"{t}"' for t in esc)


def _run(con, fts_q, limit):
    sql = ("SELECT p.book_id, b.title, b.author, p.page_no, p.chapter, p.text,"
           " bm25(pages_fts) AS rank FROM pages_fts AS f"
           " JOIN pages AS p ON p.page_rowid = f.page_rowid"
           " JOIN books AS b ON b.book_id = p.book_id"
           " WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?")
    return con.execute(sql, (fts_q, limit)).fetchall()


def search_part(db_path, query, per_part_limit):
    tokens = tokenize_arabic(query)
    if not tokens:
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = _run(con, _phrase(tokens) if len(tokens) > 1 else f'"{tokens[0]}"', per_part_limit)
        tier = "phrase"
        if not rows and len(tokens) > 1:
            rows = _run(con, _or(tokens), per_part_limit)
            tier = "or"
    finally:
        con.close()
    return [{"book_id": r[0], "title": r[1], "author": r[2], "page": r[3],
             "chapter": r[4], "text": r[5], "bm25": r[6], "tier": tier} for r in rows]


def search_all(query, limit=10, parts_dir=HERE, parts=None):
    """Return (merged hits, missing part numbers). Each hit gains part/part_file."""
    parts = sorted(parts) if parts else sorted(PARTS)
    hits, missing = [], []
    for n in parts:
        db = Path(parts_dir) / PARTS[n][0]
        if not db.is_file():
            missing.append(n)
            continue
        for h in search_part(db, query, limit):
            h["part"] = n
            h["part_file"] = PARTS[n][0]
            hits.append(h)
    hits.sort(key=lambda h: (0 if h["tier"] == "phrase" else 1, h["bm25"]))
    return hits[:limit], missing


def _drive_files(folder):
    r = subprocess.run([CLI, "drive", "files", "list", "--params", json.dumps({
        "q": f"'{folder}' in parents and trashed=false",
        "fields": "files(id,name,size)", "pageSize": 100})],
        capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"drive list failed: {r.stdout} {r.stderr}")
    return {f["name"]: (f["id"], int(f["size"])) for f in json.loads(r.stdout)["files"]}


def fetch_part(n, parts_dir=HERE):
    """Download + reassemble one part from Drive; verify whole-file SHA-256."""
    db_name, folder, _fname, _sha, size = PARTS[n]
    free = shutil.disk_usage(str(parts_dir)).free
    if free < size + 2 * 1024 ** 3:
        raise RuntimeError(f"refusing fetch of part {n}: need {size} B + headroom, "
                           f"only {free} B free")
    files = _drive_files(folder)
    tmp = Path(parts_dir) / f".fetch_part{n}"
    tmp.mkdir(exist_ok=True)
    sums = files.get("SHA256SUMS.txt")
    if not sums:
        raise RuntimeError(f"part {n}: SHA256SUMS.txt not on Drive")
    order = []
    if n == 8:
        order = ["hadith_part8_fts5.sqlite.part01of04"]
        for chunk in ("02", "03", "04"):
            order += [f"hadith_part8_fts5.sqlite.part{chunk}of04.sub0{i}of06"
                      for i in range(1, 7)]
    else:
        sums_name = f"part{n}_SHA256SUMS.txt"
        local_sums = HERE / sums_name
        if not local_sums.is_file():
            raise RuntimeError(f"part {n}: local {sums_name} missing; cannot order chunks")
        order = [ln.split(None, 1)[1].strip() for ln in
                 local_sums.read_text(encoding="utf-8").splitlines()[1:]]
    out = Path(parts_dir) / db_name
    with open(out, "wb") as fh:
        for name in order:
            if name not in files:
                raise RuntimeError(f"part {n}: {name} missing on Drive")
            fid, sz = files[name]
            dest = tmp / name
            r = subprocess.run([CLI, "drive", "files", "get", "--params",
                                json.dumps({"fileId": fid, "alt": "media"}),
                                "--output", str(dest)],
                               capture_output=True, text=True, timeout=3600)
            if r.returncode != 0 or dest.stat().st_size != sz:
                raise RuntimeError(f"part {n}: download failed for {name}: "
                                   f"{r.stdout[-500:]} {r.stderr[-500:]}")
            with open(dest, "rb") as src:
                shutil.copyfileobj(src, fh, 1024 * 1024)
            dest.unlink()
    h = hashlib.sha256()
    with open(out, "rb") as fh:
        for b in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(b)
    whole = open(HERE / f"part{n}_SHA256SUMS.txt", encoding="utf-8").readline().split()[0]
    if h.hexdigest() != whole:
        out.unlink()
        raise RuntimeError(f"part {n}: whole-file SHA-256 mismatch after reassembly")
    shutil.rmtree(tmp, ignore_errors=True)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Unified search over hadith parts 1-8.")
    ap.add_argument("query", nargs="?", help="Arabic search query")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--parts-dir", default=str(HERE))
    ap.add_argument("--parts", default="",
                    help="comma-separated part numbers to search (default: all local)")
    ap.add_argument("--fetch-missing", type=int, metavar="N",
                    help="download + reassemble part N from Drive, then exit")
    ap.add_argument("--list-parts", action="store_true")
    a = ap.parse_args(argv)
    if a.list_parts:
        for n in sorted(PARTS):
            db = Path(a.parts_dir) / PARTS[n][0]
            print(f"part {n}: {PARTS[n][0]} categories={','.join(PART_CATS[n])} "
                  f"local={'yes' if db.is_file() else 'NO'} "
                  f"drive_folder={PARTS[n][1]} sha={PARTS[n][3]}...")
        return 0
    if a.fetch_missing:
        out = fetch_part(a.fetch_missing, Path(a.parts_dir))
        print(f"FETCHED part {a.fetch_missing}: {out} ({out.stat().st_size} B, SHA verified)")
        return 0
    if not a.query:
        ap.error("a query is required (or --list-parts / --fetch-missing)")
    want = [int(x) for x in a.parts.split(",") if x.strip()] or None
    hits, missing = search_all(a.query, limit=a.limit, parts_dir=Path(a.parts_dir),
                               parts=want)
    print(f"query: {a.query} | searched parts present locally; "
          f"missing (not searched): {missing if missing else 'none'}")
    for i, h in enumerate(hits, 1):
        print(f"[{i}] part {h['part']} ({h['part_file']}) | {h['title']} | "
              f"{h['author']} | p.{h['page']} | {h['chapter']} | bm25={h['bm25']:.3f} | tier={h['tier']}")
        print("    " + " ".join(str(h["text"]).split())[:300])
    if missing:
        print(f"NOTE: parts {missing} are on Drive only; rerun with "
              f"--fetch-missing N (one part at a time, disk-checked) to include them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
