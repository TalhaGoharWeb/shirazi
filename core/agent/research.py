"""core/agent/research.py — research mode (Phase 6).

A specialized pipeline for "research this topic":

    collect sources → extract text → label claims → cited report

Claim labels (mission spec):
    FACT        — the same claim appears in ≥2 INDEPENDENT sources
                  (different domains).
    SOURCE      — stated by exactly one retrieved source.
    INFERENCE   — derived by the AI from the sources, not quoted from them.
    UNCERTAINTY — sources disagree, or nothing retrieved says this.

Citation honesty (hard rule): every citation is a real retrieved source —
title, URL, retrieval time, snippet. Fabricating a citation is worse than
saying "I don't know": when no sources are retrieved, the report says so
plainly and labels everything UNCERTAINTY.

Default retrieval is keyless and free: the `ddgs` package (already a
dependency via web_search) for search, plain urllib for page fetch. Both
are injectable so tests run with zero network.
"""

from __future__ import annotations

import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from core import logger

LABELS = ("FACT", "SOURCE", "INFERENCE", "UNCERTAINTY")


@dataclass
class Citation:
    title: str
    url: str
    retrieved_at: str = ""
    snippet: str = ""

    def __post_init__(self):
        if not self.retrieved_at:
            self.retrieved_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds")

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower().removeprefix("www.")
        except Exception:
            return ""


@dataclass
class Claim:
    text: str
    label: str                       # one of LABELS
    sources: list[Citation] = field(default_factory=list)
    note: str = ""


@dataclass
class ResearchReport:
    topic: str
    claims: list[Claim] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    ok: bool = True

    def render(self) -> str:
        lines = [f"# Research: {self.topic}", ""]
        if not self.ok or not self.citations:
            lines.append("**No sources could be retrieved.** Everything "
                         "below is UNCERTAINTY — I did not browse the web "
                         "successfully for this topic.")
            lines.append("")
        by_label: dict[str, list[Claim]] = {label: [] for label in LABELS}
        for c in self.claims:
            by_label.get(c.label, by_label["UNCERTAINTY"]).append(c)
        for label in LABELS:
            items = by_label[label]
            if not items:
                continue
            lines.append(f"## {label} ({len(items)})")
            for c in items:
                srcs = (" [" + ", ".join(_short_cite(s) for s in c.sources)
                        + "]") if c.sources else ""
                lines.append(f"- {c.text}{srcs}")
                if c.note:
                    lines.append(f"  _{c.note}_")
            lines.append("")
        if self.citations:
            lines.append("## Sources")
            for i, s in enumerate(self.citations, 1):
                lines.append(f"{i}. [{s.title}]({s.url}) — retrieved "
                             f"{s.retrieved_at}")
        if self.notes:
            lines.append("")
            lines.append("## Notes")
            lines.extend(f"- {n}" for n in self.notes)
        return "\n".join(lines)


def _short_cite(c: Citation) -> str:
    return c.domain or c.url[:40]


# ── Retrieval (injectable) ──────────────────────────────────────────────────

def _default_search(query: str, max_results: int = 6) -> list[Citation]:
    """Keyless web search via ddgs (structured results with real URLs)."""
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception:
            return []
    out: list[Citation] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                url = (r.get("href") or "").strip()
                if not url:
                    continue
                out.append(Citation(title=(r.get("title") or url)[:120],
                                    url=url,
                                    snippet=(r.get("body") or "")[:400]))
    except Exception as e:
        logger.warn("research", f"search failed: {e}")
    return out


def _default_fetch(url: str, timeout: float = 20.0) -> str:
    """Fetch a page and strip to text. Failures return '' (never fake text)."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (SHIRAZI research)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            html = resp.read(1_500_000).decode("utf-8", errors="ignore")
        from core.agent.tools_browser import _TextStripper  # reuse
        stripper = _TextStripper()
        stripper.feed(html)
        return stripper.text()[:8000]
    except Exception:
        return ""


# ── Claim extraction & labeling ─────────────────────────────────────────────

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")

_STOP = frozenset(
    "the a an and or of to in on for with is are was were be been by at as "
    "it its this that these those from we you they he she i my our your their "
    "not no yes if then than so such can could will would should may might "
    "do does did has have had what when where who how why which there here".split())


def _key_terms(sentence: str) -> frozenset:
    words = re.findall(r"[a-z]{4,}", sentence.lower())
    return frozenset(w for w in words if w not in _STOP)


def _claims_from_source(cite: Citation, page_text: str,
                        max_claims: int = 4) -> list[str]:
    """Candidate claims = the most informative sentences: search snippet
    first, then page sentences with the most key terms."""
    cands: list[str] = []
    if cite.snippet:
        cands.append(cite.snippet.strip())
    for sent in _SENT_SPLIT.split(page_text or ""):
        sent = " ".join(sent.split())
        if 40 < len(sent) < 280 and len(_key_terms(sent)) >= 4:
            cands.append(sent)
        if len(cands) >= max_claims + 2:
            break
    # Rank by informativeness (key-term density), keep the best.
    ranked = sorted(cands[:max_claims + 2],
                    key=lambda s: len(_key_terms(s)), reverse=True)
    return ranked[:max_claims]


def _label_claims(sources: list[tuple[Citation, list[str]]]) -> list[Claim]:
    """Corroboration: a claim is a FACT when ≥2 independent domains share
    ≥3 key terms with it; otherwise SOURCE. Disagreement is detected when
    two sources' claims share key terms but one contains a negation the
    other lacks — those become UNCERTAINTY."""
    claims: list[Claim] = []
    seen: list[tuple[frozenset, Citation, str]] = []  # (terms, citation, sentence)
    negs = ("not ", "never", "n't", "false", "myth", "debunk", "contradict")

    def _negated(s: str) -> bool:
        low = " " + s.lower() + " "
        return any(n in low for n in negs)

    for cite, sentences in sources:
        for sent in sentences:
            terms = _key_terms(sent)
            if len(terms) < 4:
                continue
            if any(len(t & terms) >= 3 and c.domain == cite.domain
                   for t, c, _s in seen):
                continue  # near-duplicate from the same domain
            others = [(t, c, s2) for t, c, s2 in seen
                      if len(t & terms) >= 3 and c.domain != cite.domain]
            neg = _negated(sent)
            conflicts = [c for _t, c, s2 in others if _negated(s2) != neg]
            if conflicts:
                # Same topic, opposite polarity across sources → conflict.
                srcs = [cite] + conflicts
                claims.append(Claim(
                    text=sent, label="UNCERTAINTY", sources=srcs,
                    note=("Sources disagree on this point — the claim is "
                          "stated here but contradicted elsewhere.")))
            elif others:
                srcs = [cite] + [c for _t, c, _s2 in others]
                domains = len({c.domain for c in srcs})
                claims.append(Claim(
                    text=sent, label="FACT", sources=srcs,
                    note=(f"Corroborated by {domains} independent "
                          f"source{'s' if domains > 1 else ''}.")))
            else:
                claims.append(Claim(
                    text=sent, label="SOURCE", sources=[cite],
                    note="Stated by a single retrieved source."))
            seen.append((terms, cite, sent))
    return claims


class ResearchAgent:
    """Runs the research pipeline. Inject search_fn/fetch_fn for tests."""

    def __init__(self,
                 search_fn: Optional[Callable[[str, int], list[Citation]]] = None,
                 fetch_fn: Optional[Callable[[str], str]] = None,
                 max_sources: int = 5,
                 fetch_pages: bool = True):
        self.search_fn = search_fn or _default_search
        self.fetch_fn = fetch_fn or _default_fetch
        self.max_sources = max(1, min(max_sources, 10))
        self.fetch_pages = fetch_pages

    def run(self, topic: str) -> ResearchReport:
        topic = (topic or "").strip()
        if not topic:
            return ResearchReport(topic=topic, ok=False,
                                  notes=["No topic given."])
        logger.searching(f"research: {topic!r}")
        report = ResearchReport(topic=topic)

        try:
            cites = self.search_fn(topic, self.max_sources) or []
        except Exception as e:
            logger.warn("research", f"search_fn failed: {e}")
            cites = []
        if not cites:
            report.ok = False
            report.notes.append(
                "The web search returned nothing (network down, or the "
                "search backend failed). No claims are made — see UNCERTAINTY.")
            report.claims.append(Claim(
                text="No verifiable information retrieved for this topic.",
                label="UNCERTAINTY",
                note="The search step produced zero sources."))
            logger.warn("research", "zero sources — honest empty report")
            return report

        report.citations = cites[:self.max_sources]
        sources: list[tuple[Citation, list[str]]] = []
        for cite in report.citations:
            page_text = ""
            if self.fetch_pages:
                try:
                    page_text = self.fetch_fn(cite.url) or ""
                except Exception as e:
                    logger.warn("research", f"fetch failed for {cite.domain}: {e}")
            time.sleep(0.2)  # be polite to source sites
            sentences = _claims_from_source(cite, page_text)
            sources.append((cite, sentences))
            logger.searching(f"research: {cite.domain} → {len(sentences)} claim(s)")

        report.claims = _label_claims(sources)
        if not report.claims:
            report.claims.append(Claim(
                text="Sources were retrieved but yielded no extractable claims.",
                label="UNCERTAINTY",
                sources=report.citations[:2],
                note="The pages may be paywalled, JS-rendered, or too short."))
        report.notes.append(
            "FACT = corroborated by ≥2 independent sources; SOURCE = one "
            "source; INFERENCE = the AI's own derivation (none added "
            "automatically — ask for analysis); UNCERTAINTY = conflicting or "
            "missing evidence.")
        logger.completed(f"research: {len(report.claims)} claim(s), "
                         f"{len(report.citations)} source(s)")
        return report

    def add_inference(self, report: ResearchReport, text: str,
                      based_on: Optional[list[Citation]] = None) -> Claim:
        """The AI's own derivation, explicitly labelled INFERENCE — never
        presented as sourced fact."""
        claim = Claim(text=text, label="INFERENCE",
                      sources=list(based_on or []),
                      note="Derived by the AI from the sources above, not "
                           "quoted from them.")
        report.claims.append(claim)
        return claim
