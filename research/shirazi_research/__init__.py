"""Shirazi Research Assistant v2 — laptop-first Islamic research engine.

Subpackages (built in parallel; see ARCHITECTURE.md for the full contract):
    corpus        Shamela 4 detection, .bok reading, FTS5 index, search API
    retrieval     query rewrite, madhhab restriction, rerank, evidence budgets
    authenticity  madhhab canons, grounding rules, quote audit, turath client
    providers     free-first chain, health manager, failover, payload guard
    modes         research / answer / ibarat + intent router
    verify        thesis verification mode (batch reference checking)
    interface     CLI + local web UI (this package)
"""

__version__ = "2.0.11"
