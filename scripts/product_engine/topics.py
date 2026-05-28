"""Discover product opportunities via the multi-source chain.

Source chain (in order):
  1. Ahrefs Keywords Explorer — supplement keywords with ≥30% recent
     search-volume growth
  2. PubMed E-utilities — compounds appearing in recent supplement research
  3. Curated evergreen reserve — 200-item backstop, guaranteed non-empty
     until every entry is productized

The old Reddit-mining path is gone. Reddit was unauthenticated and
blocked from GitHub Actions IPs (HTTP 403 for every fetch), and its
signal quality was already lower than search-volume trend data anyway.

Public interface (`fetch_trending`) is unchanged so main.py keeps working.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env, get_token  # noqa: E402

from product_engine.sources import run_source_chain  # noqa: E402
from product_engine.sources.errors import NoIdeasError  # noqa: E402


def _fetch_shopify_catalog_handles() -> set[str]:
    """Pull every product handle from the live Shopify store so any source
    can dedupe against the real catalog (not just state.json, which only
    tracks what THIS engine created).

    Best-effort: if Shopify is unreachable we return an empty set rather
    than crashing the run — state.json dedup still applies.
    """
    try:
        env = load_env()
        token = get_token()
        store = env.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
        api = env.get("SHOPIFY_API_VERSION", "2025-01")
    except Exception as e:
        print(f"[topics] catalog fetch skipped (env): {e}")
        return set()

    handles: set[str] = set()
    next_url: str | None = (
        f"https://{store}/admin/api/{api}/products.json"
        f"?fields=handle&limit=250&status=active,draft"
    )
    page = 0
    while next_url and page < 10:
        page += 1
        req = urllib.request.Request(
            next_url, headers={"X-Shopify-Access-Token": token}
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read())
                link = resp.headers.get("Link", "")
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"[topics] catalog fetch page {page} failed: {e}")
            break
        for p in data.get("products", []):
            h = (p.get("handle") or "").strip()
            if h:
                handles.add(h)
        next_url = None
        if 'rel="next"' in link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part.split(";")[0].strip().strip("<>")
                    break

    print(f"[topics] Shopify catalog: {len(handles)} active/draft product handles loaded for dedupe")
    return handles


def fetch_trending(
    limit: int = 10,
    existing_titles: set[str] | None = None,
) -> list[dict]:
    """Return up to `limit` product opportunities via the source chain.

    Output shape is unchanged from the old Reddit-based implementation:
    each dict has product_name, category, niche_score, why_interesting,
    reddit_url (kept blank for back-compat), plus new fields:
    growth_score, source, source_url, handle, ingredient.
    """
    env = load_env()
    existing = {t.lower() for t in (existing_titles or set())}
    catalog = _fetch_shopify_catalog_handles()

    try:
        result = run_source_chain(
            env=env,
            existing_titles=existing,
            shopify_catalog=catalog,
            limit=limit,
        )
    except NoIdeasError as e:
        print(f"[topics] CRITICAL — source chain returned zero candidates: {e}")
        raise

    print(
        f"[topics] {len(result.opportunities)} opportunities ranked "
        f"(sources used: {', '.join(result.sources_used)})"
    )
    for i, o in enumerate(result.opportunities[:5]):
        print(
            f"  #{i+1} [{o.get('source', '?'):<14}] "
            f"{o['product_name']:<35} growth={o.get('growth_score', 1.0):.2f} "
            f"niche={o.get('niche_score', '?')}"
        )
    return result.opportunities
