"""Evergreen reserve source: a curated 200-item list of niche supplement
compounds with mechanism evidence and no existing Elm & Rye SKU.

This is the source-chain backstop. It is BY DESIGN never empty until
every entry has been productized — at which point we'd refresh the file
via a separate Claude-driven curator script (~quarterly).

Selection per run is deterministic by date (so the same day produces the
same candidate even on re-runs, but consecutive days vary) and filtered
against the live Shopify catalog + state.json so we never re-suggest
something already shipped.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

RESERVE_FILE = Path(__file__).parent / "reserve.json"


def _to_handle(name: str) -> str:
    return "-".join(name.lower().split())[:60]


def _date_seed() -> int:
    """Deterministic seed from today's UTC date — same day → same shuffle,
    next day → different shuffle. Pure stdlib, no Date.now()-style sources."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return int(hashlib.sha1(today.encode()).hexdigest()[:12], 16)


def draw_from_reserve(
    env: dict,
    existing_titles: set[str],
    shopify_catalog: set[str],
    limit: int = 30,
) -> list[dict]:
    """Read the curated list, filter against what's already shipped, return
    a deterministically-shuffled top-N. Designed to always return ≥1
    unless every reserve entry has been productized."""
    try:
        data = json.loads(RESERVE_FILE.read_text())
    except FileNotFoundError:
        print(f"[reserve] missing {RESERVE_FILE} — returning empty")
        return []
    except json.JSONDecodeError as e:
        print(f"[reserve] invalid JSON in reserve.json: {e}")
        return []

    compounds = data.get("compounds", [])
    available: list[dict] = []
    for c in compounds:
        name = c.get("product_name", "").strip()
        if not name:
            continue
        if name.lower() in existing_titles:
            continue
        handle = _to_handle(name)
        if handle in shopify_catalog:
            continue
        available.append({
            "product_name":   name,
            "ingredient":     name.lower(),
            "category":       c.get("category", "supplements"),
            "niche_score":    c.get("niche_score", 7),
            "growth_score":   1.0,
            "why_interesting":
                f"{c.get('why', '')} "
                f"(From Elm & Rye curated reserve — peer-reviewed mechanism, "
                f"no existing SKU.)",
            "source":         "claude_reserve",
            "source_url":     "https://elmandrye.com/internal/reserve",
            "handle":         handle,
            "reddit_url":     "",
        })

    seed = _date_seed()
    available.sort(key=lambda r: hashlib.sha1(
        f"{seed}-{r['product_name']}".encode()
    ).hexdigest())

    print(f"[reserve] {len(available)} candidates available from reserve (of {len(compounds)} curated)")
    return available[:limit]
