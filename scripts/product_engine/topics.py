"""Discover niche trending product opportunities from Reddit.

Strategy: scan supplement, skincare, and cleaning subreddits for emerging
ingredients and products that have real discussion but haven't hit mainstream
yet. We want EARLY signal — posts with moderate engagement (not viral), real
ingredient names, and scientific backing in the comments.

Uses Reddit's unauthenticated JSON endpoints, same pattern as content_engine.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402

USER_AGENT = "Elm-And-Rye-Product-Bot/1.0 (https://elmandrye.com)"

# Subreddits by category — ordered by signal quality for each
SUBREDDITS = {
    "supplements": [
        "Nootropics", "Supplements", "Biohackers", "Longevity",
        "Peptides", "StackAdvice", "longevity",
    ],
    "skincare": [
        "SkincareAddiction", "DIYBeauty", "AsianBeauty",
        "30PlusSkinCare", "tretinoin", "scacjdiscussion",
    ],
    "cleaning": [
        "CleaningTips", "homemaking", "laundry",
        "ecofriendly", "ZeroWaste",
    ],
}

# Niche signal: low enough to catch early, high enough to filter noise
MIN_UPVOTES   = 15
MIN_COMMENTS  = 5
MAX_AGE_HOURS = 72   # 3 days — longer window catches slower-burn niche posts

# Things that are already mainstream — skip these
MAINSTREAM_SKIP = {
    "creatine", "whey protein", "vitamin c", "vitamin d", "zinc", "magnesium",
    "fish oil", "omega-3", "melatonin", "collagen", "probiotics", "ashwagandha",
    "caffeine", "protein powder", "multivitamin", "b12", "iron", "calcium",
    "retinol", "niacinamide", "hyaluronic acid", "vitamin e", "spf", "sunscreen",
    "tide", "downy", "dawn", "seventh generation",
}

# Signals that indicate an ingredient/product discussion (not just general chat)
INGREDIENT_SIGNALS = [
    # supplement signals
    "mg", "mcg", "iu", "extract", "standardized", "bioavailab", "half.life",
    "stack", "cycle", "dose", "mechanism", "pathway", "receptor", "precursor",
    "pubmed", "study", "trial", "research", "meta.analysis", "rct",
    "nmn", "nr", "urolithin", "spermidine", "fisetin", "quercetin", "berberine",
    "tongkat", "fadogia", "turkesterone", "ecdysterone", "apigenin",
    "glycine", "nac", "tmg", "taurine", "agmatine", "lion.s mane",
    "peptide", "bpc", "tb.500", "ghrp", "ipamorelin", "sermorelin",
    # skincare signals
    "ingredient", "serum", "retinoid", "bakuchiol", "peptide", "ceramide",
    "polyglutamic", "tranexamic", "azelaic", "mandelic", "phytic",
    "centella", "mugwort", "snow mushroom", "galactomyces", "bifida",
    "slugging", "skin barrier", "microbiome", "postbiotic",
    # cleaning signals
    "enzyme", "surfactant", "plant.based", "concentrate", "refillable",
    "fragrance.free", "hypoallergenic", "wool dryer", "oxygen bleach",
    "castile", "sodium percarbonate", "washing soda",
]


def _fetch_reddit(sub: str, listing: str = "hot", limit: int = 25) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}&t=week"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
        return [c["data"] for c in data.get("data", {}).get("children", [])]
    except Exception as e:
        print(f"[topics] r/{sub} fetch failed: {e}", file=sys.stderr)
        return []


def _score(post: dict) -> float:
    # Weight comments more — active discussion = real interest
    return post.get("ups", 0) * 0.5 + post.get("num_comments", 0) * 1.8


def _has_ingredient_signal(post: dict) -> bool:
    text = (post.get("title", "") + " " + (post.get("selftext", "") or ""))[:800].lower()
    return any(re.search(r'\b' + re.escape(s.replace(".", r"\s*")), text) for s in INGREDIENT_SIGNALS)


def _is_mainstream(title: str) -> bool:
    t = title.lower()
    return any(m in t for m in MAINSTREAM_SKIP)


def _fetch_top_comments(permalink: str, limit: int = 5) -> list[str]:
    url = f"https://www.reddit.com{permalink}.json?limit={limit}&sort=top"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        comments = []
        if len(data) > 1:
            for child in data[1].get("data", {}).get("children", []):
                body = child.get("data", {}).get("body", "")
                if body and body not in ("[deleted]", "[removed]") and len(body) > 40:
                    comments.append(body[:500])
                if len(comments) >= limit:
                    break
        return comments
    except Exception:
        return []


def _extract_product_opportunity(post: dict, comments: list[str], env: dict) -> dict | None:
    """Ask Claude to extract a specific product opportunity from a Reddit post."""
    title   = post.get("title", "")
    selftext = (post.get("selftext", "") or "")[:600]
    comment_block = "\n".join(f"- {c[:200]}" for c in comments[:4])
    category = post.get("_category", "supplements")

    prompt = f"""Reddit post from r/{post.get('_sub', '')} (category: {category}):

TITLE: {title}
BODY: {selftext}
TOP COMMENTS:
{comment_block}

Elm & Rye is a premium supplement, skincare, and cleaning brand. We want to create \
demand-test products for niche ingredients that are EMERGING but not yet mainstream.

Extract ONE specific product opportunity from this post. Return ONLY valid JSON:
{{
  "product_name": "Clean 2-4 word product name matching Elm & Rye naming style (e.g. 'Urolithin A', 'Snow Mushroom Serum', 'Enzyme Laundry Wash')",
  "ingredient": "The specific active ingredient or compound",
  "category": "{category}",
  "is_niche": true/false (false = already mainstream, skip it),
  "has_research": true/false (has real scientific backing),
  "is_rx_or_illegal": true/false (prescription drug, controlled substance, or illegal),
  "niche_score": 1-10 (10 = very niche/early, 1 = totally mainstream),
  "why_interesting": "One sentence on why this is an opportunity"
}}

If this post does not contain a clear product opportunity, return {{"product_name": null}}."""

    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": env["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw = data["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw.strip())
        if not result.get("product_name"):
            return None
        if result.get("is_rx_or_illegal"):
            return None
        if not result.get("is_niche") or not result.get("has_research"):
            return None
        if result.get("niche_score", 0) < 5:
            return None
        result["reddit_title"]  = title
        result["reddit_url"]    = f"https://reddit.com{post.get('permalink', '')}"
        result["subreddit"]     = post.get("_sub", "")
        result["reddit_score"]  = post.get("_score", 0)
        return result
    except Exception as e:
        print(f"[topics] Claude extraction failed: {e}", file=sys.stderr)
        return None


def _claude_fallback(limit: int, existing: set[str], env: dict) -> list[dict]:
    """Generate product opportunities via Claude when Reddit is unavailable."""
    print("[topics] Reddit unavailable — using Claude market research fallback")
    existing_list = ", ".join(sorted(existing)) if existing else "none yet"

    prompt = f"""You are a product analyst for Elm & Rye, a premium supplement, skincare, and cleaning brand.

Existing products already in catalog: {existing_list}

Generate {limit + 3} emerging niche product opportunities for Elm & Rye. Focus on:
- Supplements: ingredients with growing clinical research but not yet mainstream (e.g. urolithin A, spermidine, fisetin, AKG, plasmalogens)
- Skincare: active ingredients gaining traction in clinical/enthusiast communities (e.g. tranexamic acid, polyglutamic acid, bakuchiol, snow mushroom)
- Cleaning: plant-based or enzyme-based innovation (e.g. enzyme laundry, oxygen bleach concentrate)

Rules:
- Must NOT be in the existing catalog
- Must NOT be already mainstream (no creatine, whey, vitamin C, retinol, etc.)
- Must have real scientific backing
- Must NOT be prescription, controlled, or illegal
- Niche score 6-9 out of 10

Return a JSON array:
[
  {{
    "product_name": "2-4 word name matching Elm & Rye style",
    "ingredient": "specific active ingredient",
    "category": "supplements|skincare|cleaning",
    "is_niche": true,
    "has_research": true,
    "is_rx_or_illegal": false,
    "niche_score": <6-9>,
    "why_interesting": "one sentence on the opportunity",
    "reddit_url": "",
    "subreddit": "claude_research",
    "reddit_score": 0
  }}
]"""

    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": env["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        raw = data["content"][0]["text"].strip()
        # Extract the JSON array robustly — find first [ and last ]
        start = raw.find("[")
        end   = raw.rfind("]")
        if start == -1 or end == -1:
            raise ValueError("No JSON array found in Claude response")
        raw = raw[start:end + 1]
        results = json.loads(raw)
        filtered = [
            r for r in results
            if r.get("product_name")
            and not r.get("is_rx_or_illegal")
            and r.get("is_niche")
            and r.get("has_research")
            and r.get("niche_score", 0) >= 5
            and r["product_name"].lower() not in existing
        ]
        for r in filtered:
            print(f"[topics] Claude found: {r['product_name']} (niche score: {r['niche_score']}/10) — {r['why_interesting']}")
        return filtered[:limit]
    except Exception as e:
        print(f"[topics] Claude fallback failed: {e}", file=sys.stderr)
        return []


def fetch_trending(limit: int = 10, existing_titles: set[str] | None = None) -> list[dict]:
    """
    Return up to `limit` product opportunities. Tries Reddit first;
    falls back to Claude market research if Reddit is blocked.
    Each dict has: product_name, ingredient, category, niche_score,
    why_interesting, reddit_url.
    """
    env = load_env()
    existing = {t.lower() for t in (existing_titles or set())}

    # Collect raw posts across all categories
    candidates: list[dict] = []
    for category, subs in SUBREDDITS.items():
        for sub in subs:
            posts = _fetch_reddit(sub, listing="hot", limit=20)
            posts += _fetch_reddit(sub, listing="rising", limit=10)
            for p in posts:
                if p.get("stickied") or p.get("over_18"):
                    continue
                if p.get("ups", 0) < MIN_UPVOTES or p.get("num_comments", 0) < MIN_COMMENTS:
                    continue
                if not _has_ingredient_signal(p):
                    continue
                if _is_mainstream(p.get("title", "")):
                    continue
                p["_score"]    = _score(p)
                p["_sub"]      = sub
                p["_category"] = category
                candidates.append(p)
            time.sleep(0.8)

    # Deduplicate by post ID and sort by score
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for p in sorted(candidates, key=lambda x: x["_score"], reverse=True):
        pid = p.get("id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            unique.append(p)

    print(f"[topics] {len(unique)} candidate posts found across all subs")

    # If Reddit returned nothing (blocked), fall back to Claude
    if not unique:
        return _claude_fallback(limit, existing, env)

    # Extract opportunities via Claude (top candidates only to save API calls)
    opportunities: list[dict] = []
    for post in unique[:30]:
        if len(opportunities) >= limit:
            break
        comments = _fetch_top_comments(post.get("permalink", ""))
        time.sleep(0.5)
        opp = _extract_product_opportunity(post, comments, env)
        if not opp:
            continue
        if opp["product_name"].lower() in existing:
            print(f"[topics] Skipping '{opp['product_name']}' — already in catalog")
            continue
        opportunities.append(opp)
        print(f"[topics] Found: {opp['product_name']} (niche score: {opp['niche_score']}/10) — {opp['why_interesting']}")

    # Sort by niche score descending — most unique first
    opportunities.sort(key=lambda x: x.get("niche_score", 0), reverse=True)
    return opportunities
