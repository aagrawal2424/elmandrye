"""Build the internal-link registry that gets fed to the writer.

Pulls live products and recent blog articles from Shopify. The writer
picks 2–3 of these to cite, using descriptive 3–5 word anchor text.

Per the spec: 1 authority link (product/category, top 30% of article)
plus 1–2 cluster links (related blog posts).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gql import call  # noqa: E402

STORE = "elmandrye.com"


def fetch_products() -> list[dict]:
    """Return live products with title, handle, tags — for authority links."""
    query = """
    {
      products(first: 100, query: "status:active") {
        nodes { id title handle tags productType }
      }
    }
    """
    r = call(query)
    if r.get("errors"):
        raise SystemExit(f"products query failed: {r['errors']}")
    out = []
    for p in r["data"]["products"]["nodes"]:
        out.append({
            "title": p["title"],
            "url": f"https://{STORE}/products/{p['handle']}",
            "tags": p.get("tags", []),
            "kind": "product",
        })
    return out


def fetch_articles(blog_id: str = "gid://shopify/Blog/74623221917", limit: int = 50) -> list[dict]:
    """Return recent published blog articles for cluster links."""
    query = """
    query($id: ID!, $limit: Int!) {
      blog(id: $id) {
        articles(first: $limit) {
          nodes { id title handle tags isPublished }
        }
      }
    }
    """
    r = call(query, {"id": blog_id, "limit": limit})
    if r.get("errors"):
        raise SystemExit(f"articles query failed: {r['errors']}")
    out = []
    for a in r["data"]["blog"]["articles"]["nodes"]:
        if not a.get("isPublished"):
            continue
        out.append({
            "title": a["title"],
            "url": f"https://{STORE}/blogs/news/{a['handle']}",
            "tags": a.get("tags", []),
            "kind": "article",
        })
    return out


def build_link_registry() -> dict:
    """Return everything available, plus a smaller curated set per topic-type."""
    products = fetch_products()
    articles = fetch_articles()
    return {
        "products": products,
        "articles": articles,
        "all": products + articles,
    }


def pick_relevant(topic_keywords: list[str], registry: dict, max_n: int = 8) -> list[dict]:
    """Score registry items by keyword overlap with topic, return top N."""
    kws = {k.lower() for k in topic_keywords}
    scored = []
    for item in registry["all"]:
        text = (item["title"] + " " + " ".join(item.get("tags", []))).lower()
        hits = sum(1 for k in kws if k and k in text)
        # boost products slightly so we always have an authority candidate
        if item["kind"] == "product":
            hits = hits * 1.3 + 0.5
        if hits > 0:
            scored.append((hits, item))
    scored.sort(key=lambda x: x[0], reverse=True)
    # always include 2 product candidates and 3-5 article candidates at minimum
    products = [i for _, i in scored if i["kind"] == "product"][:3]
    articles = [i for _, i in scored if i["kind"] == "article"][:5]
    seen = set()
    out = []
    for it in products + articles:
        if it["url"] not in seen:
            out.append(it)
            seen.add(it["url"])
    return out[:max_n]


if __name__ == "__main__":
    reg = build_link_registry()
    print(f"Products: {len(reg['products'])}")
    print(f"Articles: {len(reg['articles'])}")
    print("\nSample picks for 'magnesium glycinate sleep':")
    for p in pick_relevant(["magnesium", "glycinate", "sleep"], reg):
        print(f"  [{p['kind']:7s}] {p['title']:50s} {p['url']}")
