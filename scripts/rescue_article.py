#!/usr/bin/env python3
"""Surgically repair a published article that was hand-pasted via Shopify Admin
(bypassing the content engine's H1-strip + image-required + metafield guards).

Usage:
    python3 scripts/rescue_article.py <article_id>

What it does:
    1. Fetch the article via Admin REST.
    2. Strip a leading <h1>...</h1> from body_html if present.
    3. If no feature image is set, generate a fresh hero via the engine's
       provider chain and attach it.
    4. Stamp an engine.run_id metafield = "rescue-<unix_ts>" so the
       provenance check in the validator stops treating it as off-path.
    5. PUT the changes back.

Idempotent: re-running on an already-clean article is a no-op (the fields
that need changing won't get touched).

Born from the recurring "manual paste from Shopify Admin → double title +
no image" failure mode (omega-3 article on 2026-06-01, vitamin D3 vs K2
article on 2026-06-02). The long-term fix is the articles-create webhook
validator + admin scope lockdown; this script handles the cleanup until
those are wired.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "content_engine"))

from get_token import get_token, load_env  # noqa: E402


SHOPIFY_STORE = load_env().get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API_VERSION = load_env().get("SHOPIFY_API_VERSION", "2025-01")


def _request(method: str, path: str, body: dict | None = None,
             _retry_429: int = 3, _retry_401: int = 1) -> dict:
    """Admin REST call with 429 Retry-After backoff and one 401 token-refresh.

    Shopify Admin REST returns 429 on rate-limit with a `Retry-After` header
    (seconds). We honor it up to _retry_429 attempts.

    A 401 typically means the cached token expired between our refresh
    buffer and the actual call. Wipe the cache and retry once.
    """
    token = get_token()
    url = f"https://{SHOPIFY_STORE}/admin/api/{API_VERSION}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry_429 > 0:
            wait = float(e.headers.get("Retry-After", "2"))
            print(f"[shopify] 429 on {method} {path}, sleeping {wait:.1f}s")
            time.sleep(wait)
            return _request(method, path, body,
                            _retry_429=_retry_429 - 1, _retry_401=_retry_401)
        if e.code == 401 and _retry_401 > 0:
            # Stale token in cache — wipe and re-fetch on the retry.
            cache = Path(__file__).resolve().parent.parent / ".token-cache.json"
            cache.unlink(missing_ok=True)
            print("[shopify] 401, wiped token cache and retrying once")
            return _request(method, path, body,
                            _retry_429=_retry_429, _retry_401=_retry_401 - 1)
        raise


def fetch_article(article_id: int) -> dict:
    return _request("GET", f"/articles/{article_id}.json")["article"]


def fetch_blog_handle(blog_id: int) -> str:
    return _request("GET", f"/blogs/{blog_id}.json")["blog"]["handle"]


def strip_leading_h1(body_html: str) -> tuple[str, bool]:
    """Returns (new_body, changed_bool)."""
    new_body, n = re.subn(
        r"^\s*<h1[^>]*>[^<]*</h1>\s*", "", body_html, count=1, flags=re.IGNORECASE
    )
    return new_body, n > 0


def stamp_provenance(article_id: int, run_id: str) -> None:
    """Attach engine.run_id metafield. Real upsert: GET → PUT-if-exists, else POST.

    Shopify's REST Admin API does NOT upsert on POST — a (namespace, key)
    collision returns 422 'key has already been taken'. We GET first to
    detect that and PUT the existing metafield id when found. This matters
    because rescue_article.py can be re-run on the same article (e.g. if
    the operator runs both the manual CLI and article_guard fires later),
    and we want the second run to either no-op cleanly OR refresh the
    value, never silently fail."""
    existing = _request(
        "GET",
        f"/articles/{article_id}/metafields.json?namespace=engine&key=run_id",
    )
    metafields = existing.get("metafields") or []
    if metafields:
        mf_id = metafields[0]["id"]
        if metafields[0].get("value") == run_id:
            return  # no-op: value already current
        _request(
            "PUT",
            f"/metafields/{mf_id}.json",
            {"metafield": {"id": mf_id, "value": run_id, "type": "single_line_text_field"}},
        )
        return
    _request(
        "POST",
        f"/articles/{article_id}/metafields.json",
        {
            "metafield": {
                "namespace": "engine",
                "key": "run_id",
                "type": "single_line_text_field",
                "value": run_id,
            }
        },
    )


def generate_and_attach_hero(article_id: int, title: str) -> str:
    """Call the engine's hero pipeline + PUT the article's image field."""
    # Import lazily so the script can do strip-only fixes without pulling
    # in the full image generation graph + its API key requirements.
    from generate_image import generate_hero

    cdn_url = generate_hero(title)
    _request(
        "PUT",
        f"/articles/{article_id}.json",
        {
            "article": {
                "id": article_id,
                "image": {"src": cdn_url, "alt": title},
            }
        },
    )
    return cdn_url


def fix(article_id: int) -> int:
    art = fetch_article(article_id)
    title = art.get("title") or "(untitled)"
    body = art.get("body_html") or ""
    has_image = bool((art.get("image") or {}).get("src"))

    print(f"\n=== rescue_article #{article_id} ===")
    print(f"title       : {title!r}")
    print(f"blog_id     : {art.get('blog_id')}")
    print(f"published_at: {art.get('published_at')}")
    print(f"image       : {'YES' if has_image else 'NONE'}")
    print(f"body length : {len(body):,} chars")

    changes: list[str] = []

    new_body, body_changed = strip_leading_h1(body)
    if body_changed:
        print(f"[fix] stripped leading <h1>")
        _request(
            "PUT",
            f"/articles/{article_id}.json",
            {"article": {"id": article_id, "body_html": new_body}},
        )
        changes.append("body_h1_stripped")
    else:
        print("[ok]  no leading <h1> found")

    if not has_image:
        print(f"[fix] generating hero image via provider chain…")
        try:
            cdn = generate_and_attach_hero(article_id, title)
            print(f"[fix] attached hero: {cdn}")
            changes.append("hero_attached")
        except Exception as e:
            print(f"[ERR] hero generation/attach failed: {e}")
            return 2
    else:
        print("[ok]  feature image already set")

    rid = f"rescue-{int(time.time())}"
    try:
        stamp_provenance(article_id, rid)
        print(f"[fix] stamped metafield engine.run_id = {rid}")
        changes.append("run_id_stamped")
    except Exception as e:
        print(f"[warn] metafield stamp failed (non-fatal): {e}")

    print(f"\ndone. changes: {changes or ['none']}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: rescue_article.py <article_id>", file=sys.stderr)
        return 2
    try:
        article_id = int(argv[1])
    except ValueError:
        print(f"article_id must be int, got {argv[1]!r}", file=sys.stderr)
        return 2
    return fix(article_id)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
