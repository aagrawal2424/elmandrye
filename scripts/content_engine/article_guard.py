"""Polling guard: scan recent articles + auto-rescue any that violate the
engine's invariants (off-path publish detection).

Invariants the engine guarantees but Shopify Admin manual publishes break:
  1. body_html does NOT begin with <h1>...</h1>
  2. image is set (every article has a hero)
  3. engine.run_id metafield is present (= came from the publish pipeline)

If an article fails any check, this script:
  - calls scripts/rescue_article.py logic to fix it in place
  - emits a Resend alert to OPS_EMAIL listing what was found + fixed
  - records the (article_id, failure_tuple) in a 24h-TTL dedup state so
    we don't re-alert on the same article every tick

Designed to run on a tight cron (every 30 min during business hours) so an
off-path manual publish has a max exposure window of ~30 min. Idempotent —
re-running on a clean article is a no-op.

Safety floors applied:
  - MIN_ARTICLE_AGE_MINUTES = 10 — skip articles created in the last 10
    min so we don't race the daily cron's read-after-write lag.
  - MIN_CREATED_AT — articles created before the engine.run_id metafield
    rollout (2026-06-01) only get audited for h1/image violations, NOT
    for missing metafields. Otherwise the first run would mass-stamp
    every historical article.
  - LOOKBACK_HOURS capped at 12 — prevents accidental month-long scans
    via env-var override.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
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
# Lookback for the article-listing query. Capped at MAX_LOOKBACK_HOURS so
# an env-var override can't accidentally trigger a months-long scan.
MAX_LOOKBACK_HOURS = 12
LOOKBACK_HOURS = min(MAX_LOOKBACK_HOURS,
                     int(os.environ.get("ARTICLE_GUARD_LOOKBACK_HOURS", "6")))
# Skip articles created in this window — gives the daily content engine
# time to finish stamping its metafield + lets Shopify's read-after-write
# settle. The daily cron's publish step itself is bounded by the GHA
# job's 30-min timeout; 10 min is well past the median publish time.
# Was 10 min — caused the 2026-06-04 inositol incident: the bundled
# guard fired at 17:00 but the article was 4.5 min old so it got skipped.
# Lowered to 2 min — still beats Shopify's read-after-write window for
# the metafield endpoint (~30s observed) while catching everything else.
MIN_ARTICLE_AGE_SEC = 2 * 60
# Engine started writing engine.run_id at commit 7ef46c4 on 2026-06-01
# 23:19 UTC. Articles older than that legitimately lack the metafield
# and should NOT be flagged as "off-path" on that signal alone. They are
# still audited for h1/image violations.
ENGINE_METAFIELD_CUTOVER_TS = 1780423140  # 2026-06-01T23:19:00Z
# Dedup window — don't re-alert on the same (article_id, failure_tuple)
# within this many seconds. Avoids the every-30-min alert spam loop when
# a violation can't be auto-fixed (e.g. hero provider returning 5xx).
DEDUP_TTL_SEC = 24 * 3600
DEDUP_STATE_FILE = HERE / "article_guard_state.json"


@dataclass
class Violation:
    article_id: int
    title: str
    handle: str
    failures: list[str]
    fixed: list[str]
    error: str | None = None


def _admin(method: str, path: str, body: dict | None = None,
           _retry_429: int = 3, _retry_401: int = 1) -> dict:
    """Admin REST with 429 Retry-After backoff + 401 token-refresh.
    Mirrors rescue_article.py::_request — kept in sync intentionally."""
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
            print(f"[guard] 429 on {method} {path}, sleeping {wait:.1f}s")
            time.sleep(wait)
            return _admin(method, path, body,
                          _retry_429=_retry_429 - 1, _retry_401=_retry_401)
        if e.code == 401 and _retry_401 > 0:
            cache = HERE.parent.parent / ".token-cache.json"
            cache.unlink(missing_ok=True)
            print("[guard] 401 — wiped token cache and retrying once")
            return _admin(method, path, body,
                          _retry_429=_retry_429, _retry_401=_retry_401 - 1)
        raise


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


def _has_run_id_metafield(article_id: int) -> bool | None:
    """Returns True/False on success, None on transient API failure (so the
    caller can skip the violation rather than swallow it silently)."""
    try:
        resp = _admin(
            "GET",
            f"/articles/{article_id}/metafields.json?namespace=engine&key=run_id",
        )
        return bool(resp.get("metafields"))
    except Exception as e:
        print(f"[guard] metafield GET failed for {article_id}: {e}")
        return None


def _parse_created_at_ts(s: str | None) -> float:
    """Best-effort ISO 8601 → unix timestamp. Returns 0.0 on parse failure
    (caller treats that as 'unknown age → skip safety floor')."""
    if not s:
        return 0.0
    try:
        # Shopify returns "2026-06-02T11:07:04-07:00"
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def audit_one(article: dict) -> Violation | None:
    """Returns a Violation iff the article fails at least one invariant,
    after attempting to auto-fix what we can. Each invariant check is
    isolated in its own try block so a transient failure on one doesn't
    mask the others."""
    aid = int(article["id"])
    title = article.get("title") or ""
    handle = article.get("handle") or ""
    body = article.get("body_html") or ""
    created_ts = _parse_created_at_ts(article.get("created_at"))

    # Safety floor: skip articles too young for read-after-write to settle.
    if created_ts and (time.time() - created_ts) < MIN_ARTICLE_AGE_SEC:
        return None

    failures: list[str] = []
    fixed: list[str] = []
    errors: list[str] = []

    # Check 1: leading <h1>. Loosened regex — original [^<]* missed h1s
    # with nested <strong>/<em>. DOTALL handles multi-line h1s.
    if re.match(r"^\s*<h1[^>]*>.*?</h1>", body, re.IGNORECASE | re.DOTALL):
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
            errors.append(f"h1 strip failed: {e}")

    # Check 2: feature image.
    has_image = bool((article.get("image") or {}).get("src"))
    if not has_image:
        failures.append("missing_feature_image")
        try:
            generate_and_attach_hero(aid, title)
            fixed.append("hero_attached")
        except Exception as e:
            errors.append(f"hero attach failed: {e}")

    # Check 3: engine.run_id metafield. Only apply to articles created
    # AFTER the metafield-writing code shipped — older articles get a pass.
    if created_ts and created_ts >= ENGINE_METAFIELD_CUTOVER_TS:
        present = _has_run_id_metafield(aid)
        if present is False:  # explicit False, not None (= skipped on error)
            failures.append("missing_engine_run_id")
            try:
                stamp_provenance(aid, f"guard-{int(time.time())}")
                fixed.append("run_id_stamped")
            except Exception as e:
                errors.append(f"metafield stamp failed: {e}")

    if not failures:
        return None
    return Violation(
        article_id=aid,
        title=title,
        handle=handle,
        failures=failures,
        fixed=fixed,
        error="; ".join(errors) if errors else None,
    )


def _load_dedup_state() -> dict:
    if not DEDUP_STATE_FILE.exists():
        return {}
    try:
        return json.loads(DEDUP_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_dedup_state(state: dict) -> None:
    try:
        DEDUP_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        print(f"[guard] dedup state save failed: {e}")


def _dedup_key(v: Violation) -> str:
    return f"{v.article_id}:{'|'.join(sorted(v.failures))}"


def _is_recently_alerted(v: Violation, state: dict) -> bool:
    """True if we've alerted on this exact (article_id, failure_tuple)
    in the last DEDUP_TTL_SEC. Prevents re-emailing every 30 min on a
    violation we can't auto-fix."""
    last = state.get(_dedup_key(v))
    if not last:
        return False
    return (time.time() - float(last)) < DEDUP_TTL_SEC


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


def _send_infra_alert(error: Exception) -> None:
    """Last-resort alert when main() itself throws (auth, network outage,
    Shopify down). Without this, the guard would just exit non-zero and
    GHA would mark the run failed — but AJ would only notice on the
    weekly digest."""
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        return
    body = json.dumps({
        "from": "guard@notifications.elmandrye.com",
        "to": [OPS_EMAIL],
        "subject": "[elmandrye] Article guard INFRA failure",
        "html": (
            "<p>article_guard.py main() raised:</p>"
            f"<pre>{type(error).__name__}: {error}</pre>"
            "<p>Check the GitHub Actions run for the full traceback. Common causes: "
            "Shopify Admin token rotated, Shopify Admin API down, network egress blocked.</p>"
        ),
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=30)
    except Exception:
        pass  # nothing more we can do


def main() -> int:
    since = time.time() - LOOKBACK_HOURS * 3600
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
    print(f"[guard] scanning articles created since {since_iso} "
          f"(lookback={LOOKBACK_HOURS}h, min_age={MIN_ARTICLE_AGE_SEC // 60}min)")

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

    if not violations:
        print("[guard] all clean.")
        return 0

    # Dedup: only alert on (article_id, failure_tuple)s we haven't
    # already alerted on in the last 24h.
    state = _load_dedup_state()
    new_violations = [v for v in violations if not _is_recently_alerted(v, state)]
    suppressed = len(violations) - len(new_violations)
    if suppressed:
        print(f"[guard] {suppressed} violation(s) suppressed by dedup TTL")

    if new_violations:
        alert(new_violations)
        # Record EVERY violation (new + suppressed), so the TTL window
        # extends each time we see them.
        now = time.time()
        for v in violations:
            state[_dedup_key(v)] = now
        # Prune entries older than 2× TTL — keeps file bounded.
        cutoff = now - (2 * DEDUP_TTL_SEC)
        state = {k: t for k, t in state.items() if float(t) >= cutoff}
        _save_dedup_state(state)

    # Exit 0 either way — violations are auto-corrected, not failures.
    # CI failure should only happen for infra problems (auth, network).
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"[guard] INFRA FAILURE: {type(e).__name__}: {e}")
        _send_infra_alert(e)
        raise
