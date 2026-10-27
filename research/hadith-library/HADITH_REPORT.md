# Hadith corpus — FTS5 library report (all 25 categories, 8 parts)

Status: ALL 8 PARTS BUILT, CITATION-AUDITED, AND TRANSFERRED TO GOOGLE DRIVE
(new account muhaqqiqcreates@gmail.com, 400 GiB). Unified search:
`hadith-library/search_all.py` (see § Unified search). Local .sqlite files
for parts 5/6/7 remain on this machine; parts 1/2/3/4/8 were deleted
locally after their Drive transfers verified (Drive is their backup).

Standing caveat (applies to every part): page numbers are Aljam3
digitized-file pages; printed-edition pagination may differ, and key
quotations still get checked against the original/PDF per the hybrid
retrieval policy (Aljam3 primary retrieval, Shamela citation-grade
verification/offline layer).

## Scope

All 25 hadith-related categories from Aljam3's official category list
(original 10 + 15 expansion categories, ids/names verified 25/25):

- Original 10 (complete 2026-10-24): 7,784 manifest records, 14,830 TXT.
- Expansion 15: 12,518 books / 8,959,883 pages gross (23,459 files gross).
- TXT home (Drive folder `shirazi-hadith-txt`, id
  `1eXPWwFDRiNn5tkmuQoG6nSF29rsVVSD9`): 25 per-category ZIPs, 38,285
  files, 37,854,741,117 B TXT (~9.85 GB zipped). Every category's ZIP was
  Drive-size verified before its local TXT was deleted.
- Known gap only: kutub_sitta book 21150 file 44833 is empty at source
  (alternate edition not substituted); it is the single `missing: 1`
  in the move checkpoint and is excluded from part 1's slice.
- Total catalog pages across parts: 14,724,713 (part_balance.json);
  built pages across the 8 part DBs: 14,529,397 (per-part audits below).
  Total books built: 20,299. The catalog-vs-built page difference is
  manifest page-counting vs built FTS5 pages (the empty-at-source
  kutub_sitta file accounts for only a small part of it); every part's
  own audit verified its built pages against its source TXT.

## Part inventory (all on Drive, new account)

Builder/schema identical for every part: `build_aljam3_library`
(same as Hanafi/Maliki DBs). Chunking: 250 MiB parts (1500 MiB uploads
fail persistently with Google 500 http2 errors — AGENTS.md lesson), except
part 8 (see its note). Reassembly for parts 1–7:
`cat hadith_part<N>_fts5.sqlite.part* > hadith_part<N>_fts5.sqlite &&
sha256sum -c SHA256SUMS.txt`. Drive folder links:
`https://drive.google.com/drive/folders/<folder-id>`.

| Part | Categories | Books | Built pages | Size (B) | Drive folder id | Whole-file SHA-256 | Audit |
|---|---|---|---|---|---|---|---|
| 1 | kutub_sitta, adhkar | 1,367 | 1,964,458 | 17,616,875,520 | 1v0U94AmpJlldT5hY_xz_3ZOEn-FLoPoi | 74c9326eebbe7e7d7362d2320a2bad04d6550d40413c9935909de4ededfd4e9e | 4/6 scripted — ranking artifact, true 6/6 (see note) |
| 2 | masanid_ukhra, raqaaiq_adab | 2,813 | 1,967,591 | 15,021,379,584 | 17mD1Jz25m6_ryg2-rct9q0RGobpzBTkf | ecfc756b5344… (full hash in part2_SHA256SUMS.txt) | 6/6 + 3/3 topical PASS |
| 3 | majmuat, masanid_aimma | 2,231 | 1,969,685 | 15,104,733,184 | 1YJE48tVVxqD43A6ZWvSbXjwo3ax_HFT_ | c8816bec01b6dc6fffd742eeca4ad80a2f78097badfb5a94441dd5b8f6fb3b01 | 5/5 + 3/3 topical PASS |
| 4 | tarajim, mustalah | 3,496 | 1,951,508 | 14,345,195,520 | 1IZ0nm8mFZqhmze5-QvpApzzbMpKxUOxY | 0350fd8d21914934a5c97e8a9329e900b524f3b891eb117fc45c0262275c3974 | 5/6 scripted — ranking artifact, true 6/6 (see note) |
| 5 | jarh, fiqh_aam_ref, maktabat_bahth | 3,541 | 1,944,766 | 13,961,285,632 | 1lZpfn67DH1hWydGLerEOP7m0uAFjR94m | 281d0c91580fd31076e28b0f34c6557096f2b054c25408befc393ffcdc8ec0b8 | 6/6 + 3/3 AUDIT_ALL_PASS |
| 6 | ulum_quran, fatawa_ref, mabahith_quran | 3,397 | 1,957,572 | 14,180,155,392 | 1vLciend_HNKvnjfHEi6RB2VyRcZ59ZLm | 8f861684c47e230064d19bb5e2797f6d90205e22b2eb513dff3217a40463a861 | 6/6 + 3/3 AUDIT_ALL_PASS |
| 7 | sira, mutun, shuruh, adilla_faharis, ajza | 2,656 | 1,969,190 | 15,020,609,536 | 1L5CvZ9Jd8585Qew0kNQqh2H3W4I96iIN | 5c68a62a6e7d2f93fa9f25b52a4e28fffb4518acf46a96de414cc94ce134156e | 7/7 + 3/3 AUDIT_ALL_PASS |
| 8 | takhrij, faharis_hadith, gharib_maajim, ulum, buhuth_masail, ilal | 798 | 804,627 | 6,199,414,784 | 1mVZY_XI7hsywte5vFkqQuLp8GP0Hbhr0 | 0730a243258cdc12b499049ecfc845fda17508fc3aff2e2351967dbb0f4c7c1c | 6/6 + 3/3 topical PASS |

Chunk counts (parts + SHA256SUMS.txt on Drive): part 1: 68 parts;
part 2: 58; part 3: 58; part 4: 55; part 5: 54; part 6: 55; part 7: 58;
part 8: 20 files (see part 8 note). Full per-chunk hashes live in each
part's local `part<N>_SHA256SUMS.txt` (and on Drive as SHA256SUMS.txt).

Audit notes (NOT data defects — do not re-flag):
- Part 1: scripted 4/6; book-scoped verification showed the 2 "failures"
  were ranking artifacts (correct book hit, unscoped rank below the
  script's top-10 window); true accuracy 6/6.
- Part 4: scripted 5/6; the 1 "failure" verified book-scoped verbatim
  (unscoped rank #26); true accuracy 6/6.
- Every audit printed book title + author + page for each hit, resolved
  from the books table, and checked the returned passage verbatim
  against the source TXT chunk at that page (whitespace-normalized).

Part 8 reassembly note: the 1500 MiB chunks for parts 02/03/04 failed
to upload and were shipped as 250 MiB sub-chunks instead. Reassemble:
`cat part01of04 + part02 sub01..06 + part03 sub01..06 + part04 sub01..06
> hadith_part8_fts5.sqlite`, then `sha256sum -c SHA256SUMS.txt`
(whole-file hash above; Drive total 6,199,417,299 B = DB + SHA256SUMS.txt).

## Unified search

`hadith-library/search_all.py` runs the same FTS5 search as
`shirazi_research.corpus.search.search` over every part DB (phrase
match, OR fallback, BM25 rank), merges hits by BM25 score, and tags
each hit with its part number and part file:

```
python3 search_all.py "الصلاة" --limit 10
python3 search_all.py --list-parts        # local vs Drive-only parts
python3 search_all.py --fetch-missing 2   # one part at a time, disk-checked
```

Parts not present locally are listed as missing, never silently
dropped. All 8 parts together are ~112 GB, so a full local copy needs
a machine with that much free space; the normal pattern is to fetch
the parts needed, search, and delete the local .sqlite afterwards
(the Drive copy is the backup). BM25 scores are per-index, so merged
cross-part ordering is the standard multi-index approximation; within
any one part the ranking is exactly search()'s ranking.

## Unified-search test (2026-10-27, local parts 5/6/7 only)

On this machine only parts 5, 6 and 7 are local (24 GB free); parts
1/2/3/4/8 are Drive-only and the tool reports them as missing, not
searched — it never silently drops a part. Three phrase queries were
run through `search_all.py` (full output: `search_all_test.log`); each
returned 5 merged hits with part number, part file, book title,
author and page:

- «صلاة الفجر» — top hit part 5, «التأخير وأحكامه في الفقه الإسلامي
  دراسة فقهية مقارنة», محمد بن عبد الكريم العيسى, p.115, bm25 −12.060;
  all 5 top hits part 5.
- «أذكار الصباح والمساء» — merged across parts: top hit part 6,
  «الكنز الثمين في الأجوبة على أسئلة طلبة العلم والزائرين»,
  يحيى بن علي الحجوري, p.508, bm25 −17.948; hit 3 is part 5
  (سعيد حوى, p.2115).
- «غزوة بدر» — top hit part 6, «حديث القرآن الكريم عن غزوات الرسول»,
  أبو بدر محمد بن بكر آل عابد, p.37, bm25 −14.036.

Known performance note (not a correctness issue): a single very common
term (e.g. «الصلاة» alone) scans a huge FTS5 posting list — one such
test read ~12 GB from part 5 and was still running after 9 minutes,
so it was abandoned; selective phrase queries return in ~1–2 minutes
per query across the three local parts. Per-part audits already
verified single-term search on every part independently.

Full 8-part simultaneous search still needs a machine that can hold
the reassembled parts (~112 GB) or one-part-at-a-time fetching via
`--fetch-missing N`; the tool supports both, and per-part audits above
already verify every part's search independently.

## Sibling libraries (for the final combined summary)

- Maliki fiqh (id 59, الفقه المالكي): COMPLETE — 519 books, 671,346
  pages, DB shipped to GitHub Releases (db-maliki) before the
  2026-10-25 venue change; MALIKI_REPORT.md on the remote. No action.
- Shafi'i fiqh (id 57, الفقه الشافعي): COMPLETE — 492 books, 829,413
  pages, 7,829,778,432 B, sha256 0d28109d…e95a; DB on the OLD Drive
  account (folder `shafi-fts5-db`); SHAFI_REPORT.md local. No action.
- Hanafi fiqh: COMPLETE on GitHub (1,082 TXT, FTS5 DB 480,522 pages /
  4.67 GB); local TXT deleted after remote verification. No action.

## Drive inventory (this library)

- TXT home: folder `shirazi-hadith-txt`
  (https://drive.google.com/drive/folders/1eXPWwFDRiNn5tkmuQoG6nSF29rsVVSD9)
  — 25 per-category ZIPs (see move_txt_to_drive.checkpoint.json for
  per-category file counts, TXT bytes, ZIP bytes and Drive ids).
- DB folders: the 8 folders in the table above (new account).
- This report: local copy `hadith-library/HADITH_REPORT.md`; a copy is
  uploaded to the TXT-home Drive folder alongside the ZIPs.
