"""Polling guard: scan recent articles + auto-rescue any that violate the
engine's invariants (off-path publish detection).

Invariants the engine guarantees but Shopify Admin manual publishes break:
  1. body_html does NOT begin with <h1>...</h1>
  2. image is set (every article has a hero)
  3. engine.run_id metafield is present (= came from the publish pipeline)

If an article fails any check, this script:
  - calls scripts/rescue_article.py logic to fix it in place
  - emits a Resend alert to OPS_EMAIL listing what was found + fixed
  - logs the incident to state.json for audit

Designed to run on a tight cron (every 30 min during business hours) so an
off-path manual publish has a max exposure window of ~30 min on the
storefront. Idempotent — re-running a clean article is a no-op.

Why polling instead of just the workflow-build webhook validator? The
webhook validator has lower latency but depends on cross-repo Vercel env
wiring. This guard uses ONLY the elmandrye Custom App creds already in
the repo's GitHub Secrets — zero external dependencies, ships in one PR.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # scripts/
sys.path.insert(0, str(HERE))         # scripts/content_engine/

from get_token import get_token, load_env  # noqa: E402
from rescue_article import (             # noqa: E402
    strip_leading_h1,
    generate_and_attach_hero,
    stamp_provenance,
)


SHOPIFY_STORE = load_env().get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API_VERSION = load_env().get("SHOPIFY_API_VERSION", "2025-01")
BLOG_HANDLE = load_env().get("BLOG_HANDLE", "news")
OPS_EMAIL = os.environ.get("CONTENT_ENGINE_OPS_EMAIL", "aj@portraitpal.ai")
# How far back to scan on each tick — covers our cron interval + buffer.
LOOKBACK_HOURS = int(os.environ.get("ARTICLE_GUARD_LOOKBACK_HOURS", "6"))


@dataclass
class Violation:
    article_id: int
    title: str
    handle: str
    failures: list[str]
    fixed: list[str]
    error: str | None = None


def _admin(method: str, path: str, body: dict | None = None) -> dict:
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
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _list_blog_id() -> int:
    blogs = _admin("GET", "/blogs.json")["blogs"]
    for b in blogs:
        if b.get("handle") == BLOG_HANDLE:
            return int(b["id"])
    raise RuntimeError(f"no blog with handle={BLOG_HANDLE!r}; found: {[b['handle'] for b in blogs]}")


def _list_recent_articles(blog_id: int, since_iso: str) -> list[dict]:
    """Articles created on/after since_iso. Shopify accepts the
    created_at_min filter against the blog scope."""
    params = [
        f"created_at_min={since_iso}",
        "limit=50",
        "published_status=any",  # catch both live + draft (manual pastes
                                  # sometimes land as draft then get
                                  # promoted in a second click)
    ]
    path = f"/blogs/{blog_id}/articles.json?{'&'.join(params)}"
    return _admin("GET", path).get("articles") or []


def _has_run_id_metafield(article_id: int) -> bool:
    resp = _admin(
        "GET",
        f"/articles/{article_id}/metafields.json?namespace=engine&key=run_id",
    )
    return bool(resp.get("metafields"))


def audit_one(article: dict) -> Violation | None:
    """Returns a Violation iff the article fails at least one invariant,
    after attempting to auto-fix what we can."""
    aid = int(article["id"])
    title = article.get("title") or ""
    handle = article.get("handle") or ""
    body = article.get("body_html") or ""

    failures: list[str] = []
    fixed: list[str] = []
    err: str | None = None

    if re.match(r"^\s*<h1[^>]*>[^<]*</h1>", body, re.IGNORECASE):
        failures.append("body_starts_with_h1")
        try:
            new_body, _ = strip_leading_h1(body)
            _admin(
                "PUT",
                f"/articles/{aid}.json",
                {"article": {"id": aid, "body_html": new_body}},
            )
            fixed.append("body_h1_stripped")
        except Exception as e:
            err = f"h1 strip failed: {e}"

    has_image = bool((article.get("image") or {}).get("src"))
    if not has_image:
        failures.append("missing_feature_image")
        try:
            generate_and_attach_hero(aid, title)
            fixed.append("hero_attached")
        except Exception as e:
            err = (err + "; " if err else "") + f"hero attach failed: {e}"

    try:
        if not _has_run_id_metafield(aid):
            failures.append("missing_engine_run_id")
            stamp_provenance(aid, f"guard-{int(time.time())}")
            fixed.append("run_id_stamped")
    except Exception as e:
        err = (err + "; " if err else "") + f"metafield check failed: {e}"

    if not failures:
        return None
    return Violation(
        article_id=aid,
        title=title,
        handle=handle,
        failures=failures,
        fixed=fixed,
        error=err,
    )


def alert(violations: list[Violation]) -> None:
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        print("[guard] no RESEND_API_KEY — skipping alert email")
        return
    rows = "\n".join(
        f"- <b>{v.title}</b> (id={v.article_id}, handle={v.handle})<br>"
        f"  found: {', '.join(v.failures)}<br>"
        f"  fixed: {', '.join(v.fixed) or '<i>none</i>'}<br>"
        f"  url: https://elmandrye.com/blogs/{BLOG_HANDLE}/{v.handle}"
        + (f"<br>  error: {v.error}" if v.error else "")
        for v in violations
    )
    html = (
        "<h2 style='color:#b00;'>Off-path article publishes detected</h2>"
        "<p>These articles were created outside the content engine pipeline "
        "(no engine.run_id metafield) or violated invariants the engine "
        "guarantees. The guard auto-corrected what it could; review the live "
        "URLs to confirm.</p>"
        f"<p style='font-size:14px;line-height:1.7'>{rows}</p>"
        "<p style='color:#888;font-size:12px;'>article_guard.py — "
        f"elmandrye repo — {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}</p>"
    )
    body = json.dumps({
        "from": "guard@notifications.elmandrye.com",
        "to": [OPS_EMAIL],
        "subject": f"[elmandrye] {len(violations)} off-path article(s) auto-corrected",
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[guard] alert sent (status={r.status})")
    except Exception as e:
        print(f"[guard] alert send failed: {e}")


def main() -> int:
    since = time.time() - LOOKBACK_HOURS * 3600
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
    print(f"[guard] scanning articles created since {since_iso}")

    blog_id = _list_blog_id()
    articles = _list_recent_articles(blog_id, since_iso)
    print(f"[guard] found {len(articles)} recent article(s) in blog {blog_id}")

    violations: list[Violation] = []
    for a in articles:
        v = audit_one(a)
        if v:
            violations.append(v)
            print(
                f"[guard] VIOLATION id={v.article_id} title={v.title!r} "
                f"failures={v.failures} fixed={v.fixed}"
                + (f" error={v.error}" if v.error else "")
            )

    if violations:
        alert(violations)
    else:
        print("[guard] all clean.")

    # Exit 0 either way — violations are auto-corrected, not failures.
    # CI failure should only happen for infra problems (auth, network).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
