"""Product opportunity source chain.

The chain runs each source in order, aggregates results, and guarantees a
non-empty return via the evergreen reserve backstop. Failures in any one
source are logged and surfaced but never cause the whole pipeline to
return zero — the reserve mathematically can't be empty.

Mirrors the architecture of generate_image.py's provider chain: typed
errors classify failure modes, the runner keeps a structured attempts_log,
and the consumer (topics.fetch_trending) gets a `SourceChainResult` it
can act on instead of a sentinel empty list.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .errors import NoIdeasError, SourceError
from .ahrefs import find_trending_keywords
from .pubmed import find_recent_research
from .claude_reserve import draw_from_reserve

Opportunity = dict
SourceFn = Callable[[dict, set[str], set[str], int], list[Opportunity]]


@dataclass
class SourceChainResult:
    opportunities: list[Opportunity]
    attempts_log: list[dict] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)


SOURCE_CHAIN: list[tuple[str, SourceFn]] = [
    ("ahrefs",         find_trending_keywords),
    ("pubmed",         find_recent_research),
    ("claude_reserve", draw_from_reserve),
]


def run_source_chain(
    env: dict,
    existing_titles: set[str],
    shopify_catalog: set[str],
    limit: int,
) -> SourceChainResult:
    """Run every source, aggregate + dedupe, return top-N by composite score.

    The reserve is the last entry and is designed to ALWAYS produce ≥1
    candidate. If even the reserve returns nothing (only possible if
    reserve.json was emptied or every entry already shipped), raise
    NoIdeasError so the caller can alert.
    """
    aggregated: list[Opportunity] = []
    log: list[dict] = []
    sources_used: list[str] = []

    for name, fn in SOURCE_CHAIN:
        import time as _t
        t0 = _t.time()
        try:
            # Each source gets a generous fetch quota; we dedupe + rank later
            results = fn(env, existing_titles, shopify_catalog, limit * 3) or []
            latency_ms = int((_t.time() - t0) * 1000)
            log.append({
                "source": name, "count": len(results), "latency_ms": latency_ms,
                "error_class": "OK", "error_msg": "",
            })
            if results:
                sources_used.append(name)
                aggregated.extend(results)
                print(f"[sources] {name}: {len(results)} candidates ({latency_ms}ms)")
            else:
                print(f"[sources] {name}: 0 candidates ({latency_ms}ms)")
        except SourceError as e:
            latency_ms = int((_t.time() - t0) * 1000)
            log.append({
                "source": name, "count": 0, "latency_ms": latency_ms,
                "error_class": type(e).__name__, "error_msg": str(e)[:300],
            })
            print(f"[sources] {name}: FAIL {type(e).__name__}: {e}")
        except Exception as e:
            latency_ms = int((_t.time() - t0) * 1000)
            log.append({
                "source": name, "count": 0, "latency_ms": latency_ms,
                "error_class": f"unexpected:{type(e).__name__}",
                "error_msg": str(e)[:300],
            })
            print(f"[sources] {name}: UNEXPECTED {type(e).__name__}: {e}")

    seen: set[str] = set()
    unique: list[Opportunity] = []
    for r in aggregated:
        key = r.get("product_name", "").strip().lower()
        if not key or key in seen or key in existing_titles:
            continue
        if r.get("handle", "") and r["handle"] in shopify_catalog:
            continue
        seen.add(key)
        unique.append(r)

    unique.sort(
        key=lambda r: (
            r.get("growth_score", 0) or 0,
            r.get("niche_score", 0) or 0,
        ),
        reverse=True,
    )

    if not unique:
        raise NoIdeasError(
            f"All sources returned 0 usable candidates after dedupe. "
            f"Reserve is empty or fully consumed. attempts_log={log}"
        )

    return SourceChainResult(
        opportunities=unique[:limit],
        attempts_log=log,
        sources_used=sources_used,
    )
