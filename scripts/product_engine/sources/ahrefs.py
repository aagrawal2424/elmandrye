"""Ahrefs source: discover supplement keywords whose search volume is
rapidly increasing.

Approach:
  1. matching-terms — expand a small set of supplement-related seeds into
     hundreds of candidate keywords filtered by volume + difficulty
  2. volume-history — for top candidates, fetch the last 8 months of
     monthly search volume
  3. compute growth = mean(last 3 months) / mean(first 3 months); keep
     keywords with growth ≥ 1.30 (30%+ recent growth)
  4. classify each candidate via a fast heuristic (is this a supplement
     ingredient vs a brand/generic?) and emit Opportunity dicts

Cost: ~12 units per seed for matching-terms (one slice per seed), plus
~1 unit per volume-history call. With 10 seeds × 60 candidates checked
≈ 720 units per run. At 400k/mo workspace limit we can afford ~16,000
runs per month — daily cron uses ~0.2% of quota.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Optional

from .errors import (
    SourceAuthError, SourceRateLimitError, SourceTransientError
)

BASE = "https://api.ahrefs.com/v3/keywords-explorer"

# Seeds chosen for breadth across the supplement landscape. Each one will
# expand to ~50-100 niche-ish candidates via matching-terms.
#
# Notably absent: "peptide" — the online peptide market is dominated by
# Rx-territory brand searches (CJC, BPC, GHK-Cu, Klow, Skinfix) that we
# can't sell. Dedicated whole-food/herbal seeds yield better OTC results.
SEEDS: list[str] = [
    "nootropic",
    "adaptogen",
    "longevity supplement",
    "mushroom extract",
    "polyphenol",
    "flavonoid",
    "amino acid supplement",
    "mineral supplement",
    "anti aging supplement",
    "gut health supplement",
    "sleep supplement",
    "skin supplement",
    "anti inflammatory supplement",
    "herbal extract",
    "ayurvedic herb",
    "traditional chinese herb",
    "carotenoid supplement",
    "phytochemical",
    "standardized extract",
    "fermented mushroom",
]

# Skip mainstream — same list spirit as topics.py but stricter
MAINSTREAM_TERMS = {
    "creatine", "whey", "protein powder", "vitamin c", "vitamin d", "vitamin b12",
    "zinc", "magnesium", "fish oil", "omega-3", "omega 3", "melatonin", "collagen",
    "probiotic", "ashwagandha", "caffeine", "multivitamin", "iron supplement",
    "calcium supplement", "biotin", "msm", "glucosamine", "turmeric", "curcumin",
    "elderberry", "echinacea", "ginseng", "garcinia", "apple cider vinegar",
}

# Words that signal the keyword is informational rather than purchasable
NON_PRODUCT_TERMS = {
    "reddit", "review", "vs", "side effects", "dose", "dosage", "how much",
    "best time", "for women", "for men", "for kids", "amazon", "walmart",
    "cvs", "walgreens", "iherb", "near me", "wikipedia", "wiki",
    "what is", "what are", "how to", "where to", "buy online", "buy",
    "benefits", "vs.", "stack", "cycle", "before and after", "results",
    "discount", "coupon", "near", "online", "store", "shop", "price",
    "cost", "free", "alternative", "substitute",
}

# Rx-only / scheduled / GLP-1 drug class + research peptides — never
# productize at Elm & Rye. Covers both spaced and unspaced spellings.
RX_BLOCKLIST = {
    "retatrutide", "tirzepatide", "semaglutide", "ozempic", "wegovy",
    "mounjaro", "zepbound", "saxenda", "liraglutide", "rybelsus",
    "ipamorelin", "sermorelin", "tesamorelin",
    "cjc-1295", "cjc 1295", "cjc1295",
    "bpc-157", "bpc 157", "bpc157",
    "tb-500", "tb 500", "tb500",
    "mots-c", "mots c", "motsc",
    "epitalon", "selank", "semax",
    "pt-141", "pt 141", "pt141", "bremelanotide",
    "melanotan", "mt1", "mt-1", "mt2", "mt-2",
    "ghk-cu", "ghk cu", "ghkcu",
    # Often-confused branded peptide products
    "klow", "fuente", "skinfix", "lipo flavonoid", "lipoflavonoid",
    "prime peptide",
    # Topical peptide cosmetics — not our market
    "peptide cream", "peptide serum", "peptide threads", "peptide therapy",
    "peptide injection", "peptide research",
    # Scheduled compounds
    "sarms", "ostarine", "ligandrol", "rad-140", "rad 140", "rad140",
    "cardarine", "mk-677", "mk 677", "mk677", "mk-2866",
    "phenibut", "kratom", "tianeptine",
}

# Volume / difficulty filters: niche but real demand
MIN_VOLUME = 500
MAX_VOLUME = 30000
MAX_DIFFICULTY = 35

GROWTH_THRESHOLD = 1.30  # 30%+ recent growth (mean last 3 / mean first 3)
MIN_HISTORY_MONTHS = 6

DEFAULT_TIMEOUT = 25


def _api_call(path: str, params: dict, api_key: str) -> dict:
    """One Ahrefs GET. Translates HTTP errors into typed source errors so
    the chain runner can advance / retry intelligently."""
    qs = urllib.parse.urlencode(params, doseq=True)
    url = f"{BASE}/{path}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        if e.code in (401, 403):
            raise SourceAuthError(f"Ahrefs {e.code}: {body}")
        if e.code == 429:
            raise SourceRateLimitError(f"Ahrefs 429: {body}")
        if e.code >= 500:
            raise SourceTransientError(f"Ahrefs {e.code}: {body}")
        raise SourceTransientError(f"Ahrefs {e.code}: {body}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise SourceTransientError(f"Ahrefs network: {e}")


def _matching_terms(api_key: str, seed: str) -> list[dict]:
    """Fetch up to 100 keywords matching the seed, pre-filtered server-side
    by volume + difficulty."""
    data = _api_call("matching-terms", {
        "country": "us",
        "match_mode": "terms",
        "select": "keyword,volume,difficulty,traffic_potential,first_seen",
        "keywords": seed,
        "order_by": "volume:desc",
        "limit": 100,
        "volume_from": MIN_VOLUME,
        "volume_to": MAX_VOLUME,
        "difficulty_to": MAX_DIFFICULTY,
    }, api_key)
    return data.get("keywords", []) or []


def _volume_history(api_key: str, keyword: str) -> list[dict]:
    """8 months of monthly volume for one keyword."""
    data = _api_call("volume-history", {
        "country": "us",
        "select": "date,volume",
        "keyword": keyword,
        "limit": 12,
    }, api_key)
    return data.get("metrics", []) or []


def _compute_growth(history: list[dict]) -> Optional[float]:
    """Returns mean(last 3 mo) / mean(first 3 mo); None if insufficient data."""
    vols = [h.get("volume", 0) or 0 for h in history]
    if len(vols) < MIN_HISTORY_MONTHS:
        return None
    first = vols[:3]
    last = vols[-3:]
    if sum(first) == 0:
        return float("inf") if sum(last) > 0 else None
    mean_first = sum(first) / 3
    mean_last = sum(last) / 3
    if mean_first <= 0:
        return None
    return mean_last / mean_first


def _is_mainstream(keyword: str) -> bool:
    k = keyword.lower()
    return any(t in k for t in MAINSTREAM_TERMS)


def _is_informational(keyword: str) -> bool:
    k = keyword.lower()
    return any(t in k for t in NON_PRODUCT_TERMS)


def _is_rx_blocked(keyword: str) -> bool:
    k = keyword.lower()
    return any(t in k for t in RX_BLOCKLIST)


def _too_many_words(keyword: str, max_words: int = 4) -> bool:
    """Multi-word queries are almost always informational/long-tail searches,
    not actual ingredient names. Real supplement ingredients are 1-3 words."""
    return len(keyword.split()) > max_words


# Category words that aren't products by themselves — you can't sell a
# product called "Adaptogen", that's a class. Real product names need a
# specific compound, herb, or branded fraction.
CATEGORY_NOISE = {
    "nootropic", "nootropics", "adaptogen", "adaptogens", "adaptogenic",
    "supplement", "supplements", "vitamin", "vitamins", "mineral",
    "minerals", "herb", "herbs", "herbal", "natural", "organic",
    "extract", "extracts", "powder", "capsule", "capsules", "tablet",
    "tablets", "gummy", "gummies", "liquid", "tincture", "tea",
    "longevity", "antiaging", "anti-aging", "wellness", "health",
    "polyphenol", "polyphenols", "flavonoid", "flavonoids", "amino",
    "antioxidant", "antioxidants", "phytochemical", "carotenoid",
    "standardized", "fermented", "ayurvedic", "ayurveda",
    "anti", "pre", "pro", "post", "the best", "best",
    "natural sleep", "natural energy", "natural focus",
    "sleep", "energy", "focus", "stress", "mood", "drink", "shot",
    "stockists", "stockist", "stock", "where", "buy",
}

# Known supplement brand names that frequently appear in matching-terms
# results but aren't generic ingredients we can productize.
BRAND_BLOCKLIST = {
    "moment", "skinfix", "klow", "fuente", "glow", "prime",
    "athletic greens", "ag1", "huel", "soylent", "olipop", "poppi",
    "ritual", "care of", "rootine", "thorne", "pure encapsulations",
    "now foods", "bulk supplements", "double wood", "nootropics depot",
    "huberman", "andrew huberman", "joe rogan", "tim ferriss",
    "lipo flavonoid", "lipoflavonoid",
}


def _is_brand(keyword: str) -> bool:
    k = keyword.lower()
    return any(b in k for b in BRAND_BLOCKLIST)


def _is_category_noise(keyword: str) -> bool:
    """Skip pure category labels — these are seed-bleed, not ingredients."""
    k = keyword.lower().strip()
    if k in CATEGORY_NOISE:
        return True
    # Multi-word keywords that consist ONLY of category words
    tokens = k.split()
    if all(t in CATEGORY_NOISE for t in tokens):
        return True
    return False


def _looks_like_brand(keyword: str) -> bool:
    """Heuristic: original keyword had multiple capitalized words mid-phrase,
    suggesting it's a branded product not a generic ingredient. We get the
    lowercase form so this is approximated by checking for proper-noun-like
    prefixes."""
    # If the keyword starts with an uppercase-looking proper-noun token
    # AND has more tokens after (e.g. "Fuente Silk Peptide"), the first
    # token is probably a brand. This is a weak heuristic; the downstream
    # Anthropic classifier in market_research.py is the authoritative gate.
    tokens = keyword.lower().split()
    if not tokens:
        return False
    # Skip if first token is a known ingredient family
    INGREDIENT_PREFIXES = {
        "alpha", "beta", "gamma", "delta", "d-", "l-",
        "mitochondria", "anti", "pre", "pro", "tri", "tetra", "penta",
    }
    first = tokens[0]
    if any(first.startswith(p) for p in INGREDIENT_PREFIXES):
        return False
    return False  # disabled by default — let Anthropic classify in market_research


def _to_handle(keyword: str) -> str:
    """Convert 'isoliquiritigenin' → 'isoliquiritigenin'; strip 'supplement'
    suffix; lowercase; hyphenate."""
    k = keyword.lower().replace("supplement", "").strip()
    return "-".join(k.split())[:60]


def _to_product_name(keyword: str) -> str:
    """Title-case the keyword for use as a product name. Strip the word
    'supplement' since we add our own product format."""
    k = keyword.lower().replace(" supplement", "").strip()
    return k.title()


def _niche_score(volume: int, difficulty: Optional[int]) -> int:
    """1-10: lower volume + lower difficulty = higher niche score."""
    score = 10
    if volume > 20000: score -= 3
    elif volume > 10000: score -= 2
    elif volume > 5000: score -= 1
    if difficulty is not None:
        if difficulty > 25: score -= 2
        elif difficulty > 15: score -= 1
    return max(1, min(10, score))


def find_trending_keywords(
    env: dict,
    existing_titles: set[str],
    shopify_catalog: set[str],
    limit: int = 30,
) -> list[dict]:
    """Main entry — returns a list of Opportunity dicts for the source chain.

    Each dict has the same shape topics.py used to emit, plus extra fields
    for downstream ranking:
      - product_name, ingredient, category, niche_score, why_interesting
      - source: "ahrefs"
      - source_url: deep link to Ahrefs Keywords Explorer
      - growth_score: float (1.0 = flat; 1.5 = +50% recent)
      - handle: pre-computed Shopify handle for early dedupe
      - reddit_url: "" for back-compat with main.py
    """
    api_key = env.get("AHREFS_API_KEY", "").strip()
    if not api_key:
        raise SourceAuthError("AHREFS_API_KEY missing or empty in env")

    candidates: list[dict] = []
    for seed in SEEDS:
        try:
            terms = _matching_terms(api_key, seed)
        except SourceAuthError:
            raise
        except SourceRateLimitError:
            print(f"[ahrefs] rate-limited at seed '{seed}' — stopping early")
            break
        except SourceTransientError as e:
            print(f"[ahrefs] transient on seed '{seed}': {e} — continuing")
            continue

        for t in terms:
            kw = (t.get("keyword") or "").strip()
            if not kw:
                continue
            if _is_mainstream(kw) or _is_informational(kw) or _is_rx_blocked(kw):
                continue
            if _too_many_words(kw) or _looks_like_brand(kw) or _is_category_noise(kw):
                continue
            if _is_brand(kw):
                continue
            handle = _to_handle(kw)
            if handle in shopify_catalog:
                continue
            if _to_product_name(kw).lower() in existing_titles:
                continue
            candidates.append(t)

    seen = set()
    unique: list[dict] = []
    for c in sorted(candidates, key=lambda x: x.get("volume", 0) or 0, reverse=True):
        k = (c.get("keyword") or "").lower()
        if k and k not in seen:
            seen.add(k)
            unique.append(c)

    print(f"[ahrefs] {len(unique)} pre-filter candidates from {len(SEEDS)} seeds")

    trending: list[dict] = []
    history_budget = min(len(unique), 80)
    for c in unique[:history_budget]:
        kw = c["keyword"]
        try:
            history = _volume_history(api_key, kw)
        except SourceAuthError:
            raise
        except SourceRateLimitError:
            print("[ahrefs] rate-limited during history fetch — stopping early")
            break
        except SourceTransientError:
            continue
        growth = _compute_growth(history)
        if growth is None or growth < GROWTH_THRESHOLD:
            continue
        # Clamp absurd values so a 0-volume → tiny-volume keyword can't
        # dominate ranking. Real durable trends rarely exceed 5x.
        if growth == float("inf") or growth > 5.0:
            growth = 5.0
        vol = c.get("volume", 0) or 0
        diff = c.get("difficulty")
        trending.append({
            "product_name":  _to_product_name(kw),
            "ingredient":    kw,
            "category":      "supplements",
            "niche_score":   _niche_score(vol, diff),
            "growth_score":  round(growth, 2),
            "why_interesting":
                f"'{kw}' search volume up {(growth-1)*100:.0f}% in 6mo "
                f"(monthly volume {vol:,}, keyword difficulty {diff if diff is not None else '?'}). "
                f"Ahrefs trending signal — real demand, not yet productized at Elm & Rye.",
            "source":        "ahrefs",
            "source_url":    f"https://app.ahrefs.com/keywords-explorer/google/us/overview?keyword={urllib.parse.quote(kw)}",
            "handle":        _to_handle(kw),
            "reddit_url":    "",
            "volume":        vol,
            "difficulty":    diff,
        })

    trending.sort(key=lambda r: r["growth_score"], reverse=True)
    print(f"[ahrefs] {len(trending)} keywords passed growth ≥{GROWTH_THRESHOLD} filter")
    return trending[:limit]
