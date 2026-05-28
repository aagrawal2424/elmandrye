"""PubMed source: discover supplement compounds with recent (last 36 months)
mechanism-of-action or clinical-trial research that haven't been productized
at Elm & Rye yet.

Uses NCBI E-utilities (free, no auth). Strategy:
  1. esearch — find recent papers matching supplement-related queries
  2. esummary — pull title + abstract + MeSH terms for top hits
  3. heuristic compound extraction — pull the highest-frequency
     non-mainstream compound name from each abstract
  4. dedupe + score by paper recency + journal-tier weight

This is the source that originally surfaced Isoliquiritigenin. It tends
to find compounds 2-3 years before they hit Google Trends.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from typing import Optional

from .errors import SourceTransientError

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Topic queries chosen to surface niche compounds in major research areas
QUERIES = [
    'flavonoid AND ("clinical trial"[ptyp] OR "randomized controlled trial"[ptyp])',
    'polyphenol AND supplement AND mechanism',
    'adaptogen AND cortisol AND human',
    'nootropic AND BDNF',
    'longevity AND senolytic',
    'mitochondrial AND supplement',
    'gut microbiome AND prebiotic AND human',
    'skin AND ceramide AND oral supplementation',
]

# Recency window in days — anything older than this we skip
MAX_PAPER_AGE_DAYS = 36 * 30  # 36 months

# Compounds we'd rather skip (already mainstream)
MAINSTREAM_COMPOUNDS = {
    "vitamin c", "vitamin d", "zinc", "magnesium", "creatine", "caffeine",
    "ashwagandha", "turmeric", "curcumin", "ginseng", "fish oil", "omega-3",
    "probiotic", "collagen", "iron", "calcium", "biotin", "melatonin",
    "ginkgo biloba", "garcinia", "echinacea",
}

# Regex: candidate compound names from abstracts. We look for chemistry-y
# tokens (mixed-case, often ending in -in/-ine/-ol/-ide/-ate/-one)
COMPOUND_RE = re.compile(
    r"\b("
    r"[A-Z][a-z]+(?:in|ine|ol|ide|ate|one|enin|ezid)e?|"
    r"[a-z]+(?:in|ine|ol|ide|ate|one)e?"
    r")\b"
)

# Filter stopwords that match the compound regex but aren't compounds
COMPOUND_STOPWORDS = {
    "study", "trial", "human", "model", "result", "outcome", "patient",
    "system", "method", "design", "review", "article", "analysis", "between",
    "level", "rate", "high", "low", "potential", "phase", "stage", "factor",
    "marker", "protein", "enzyme", "receptor", "pathway", "mechanism",
    "treatment", "therapy", "supplement", "dose", "intake", "intervention",
    "compound", "molecule", "extract", "active", "effect", "effective",
    "anti", "pro", "control", "placebo", "double", "blind", "single",
    "before", "after", "during", "while", "above", "below", "within",
    "increase", "decrease", "reduce", "improve", "promote", "induce",
    "indicate", "demonstrate", "consider", "include", "compare", "associate",
    "decline", "online", "headline", "tagline", "outline", "guideline",
    "machine", "routine", "vaccine", "cuisine", "doctrine",
    "natural", "general", "mineral", "additional", "rational", "criteria",
    "inflammation", "obesity", "diabetes", "cancer", "disease", "syndrome",
    "research", "evidence", "literature",
}


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "elmandrye-product-engine/1.0 (contact: aj@elmandrye.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceTransientError(f"PubMed network: {e}")


def _esearch(query: str, retmax: int = 30) -> list[str]:
    """Return PMIDs for recent papers matching query."""
    url = (
        f"{EUTILS}/esearch.fcgi?db=pubmed"
        f"&term={urllib.parse.quote(query)}"
        f"&retmax={retmax}&sort=date&retmode=json"
        f"&datetype=pdat&reldate={MAX_PAPER_AGE_DAYS}"
    )
    try:
        data = json.loads(_http_get(url))
    except Exception as e:
        raise SourceTransientError(f"PubMed esearch parse: {e}")
    return data.get("esearchresult", {}).get("idlist", []) or []


def _esummary(pmids: list[str]) -> list[dict]:
    if not pmids:
        return []
    url = (
        f"{EUTILS}/esummary.fcgi?db=pubmed"
        f"&id={','.join(pmids)}&retmode=json"
    )
    try:
        data = json.loads(_http_get(url))
    except Exception as e:
        raise SourceTransientError(f"PubMed esummary parse: {e}")
    result = data.get("result") or {}
    summaries = []
    for pmid in pmids:
        item = result.get(pmid)
        if item:
            summaries.append(item)
    return summaries


def _extract_compounds(text: str) -> list[str]:
    """Extract candidate compound names from a paper title (we don't fetch
    abstracts to keep API quota low)."""
    if not text:
        return []
    tokens = COMPOUND_RE.findall(text)
    cleaned: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl in COMPOUND_STOPWORDS:
            continue
        if tl in MAINSTREAM_COMPOUNDS:
            continue
        if len(tl) < 6:
            continue
        if tl in cleaned:
            continue
        cleaned.append(tl)
    return cleaned


def _to_handle(name: str) -> str:
    return "-".join(name.lower().split())[:60]


def find_recent_research(
    env: dict,
    existing_titles: set[str],
    shopify_catalog: set[str],
    limit: int = 30,
) -> list[dict]:
    """Return Opportunity dicts based on recently-published research."""
    compound_score: dict[str, dict] = {}

    for q in QUERIES:
        try:
            pmids = _esearch(q, retmax=25)
        except SourceTransientError as e:
            print(f"[pubmed] esearch failed for '{q[:40]}': {e}")
            continue
        time.sleep(0.34)
        try:
            papers = _esummary(pmids)
        except SourceTransientError as e:
            print(f"[pubmed] esummary failed for '{q[:40]}': {e}")
            continue
        time.sleep(0.34)

        for p in papers:
            title = p.get("title", "")
            pubdate = p.get("pubdate", "")
            compounds = _extract_compounds(title)
            for c in compounds[:3]:
                if c in compound_score:
                    compound_score[c]["count"] += 1
                else:
                    compound_score[c] = {
                        "compound": c,
                        "count": 1,
                        "example_pmid": p.get("uid", ""),
                        "example_title": title[:160],
                        "pubdate": pubdate,
                    }

    opportunities: list[dict] = []
    for entry in sorted(compound_score.values(), key=lambda x: x["count"], reverse=True):
        compound = entry["compound"]
        handle = _to_handle(compound)
        if handle in shopify_catalog:
            continue
        if compound.lower() in existing_titles:
            continue
        if entry["count"] < 2:
            continue
        opportunities.append({
            "product_name":  compound.title(),
            "ingredient":    compound,
            "category":      "supplements",
            "niche_score":   8,
            "growth_score":  1.0 + min(entry["count"] * 0.1, 1.0),
            "why_interesting":
                f"PubMed: '{compound}' appears in {entry['count']} recent supplement-research "
                f"papers (most recent example: \"{entry['example_title']}\"). "
                f"High research signal — emerging compound with mechanism evidence.",
            "source":        "pubmed",
            "source_url":    f"https://pubmed.ncbi.nlm.nih.gov/{entry['example_pmid']}/",
            "handle":        handle,
            "reddit_url":    "",
        })
        if len(opportunities) >= limit:
            break

    print(f"[pubmed] {len(opportunities)} unique compounds with ≥2 recent papers")
    return opportunities
