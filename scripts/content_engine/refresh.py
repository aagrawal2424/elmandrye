"""Monthly article refresh job.

Picks the 5 oldest published articles (by timestamp) that are due for a
refresh, regenerates their body content via the same pipeline used during
initial publish, and pushes the updated HTML back to Shopify via
articleUpdate.

Run directly:
    python scripts/content_engine/refresh.py

Or via the GitHub Actions workflow (.github/workflows/refresh.yml).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# ── path setup ──────────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

# ── internal imports ────────────────────────────────────────────────────────
from gql import call  # noqa: E402
from get_token import load_env  # noqa: E402
from generate_article import generate as generate_article  # noqa: E402
from validate import validate  # noqa: E402
from publish import (  # noqa: E402
    md_to_html,
    extract_title,
    extract_summary,
    extract_faq_pairs,
    build_jsonld,
    build_article_body_html,
    MEDICAL_DISCLAIMER_HTML,
    BLOG_HANDLE,
    STORE,
    slugify,
)
from originality import signature  # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────
STATE_FILE = HERE / "state.json"
REFRESH_CADENCE_DAYS = 60
MAX_REFRESH_PER_RUN = 5


# ── state helpers ────────────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"covered_topics": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── article selection ────────────────────────────────────────────────────────

def pick_articles_to_refresh(state: dict) -> list[dict]:
    """Return up to MAX_REFRESH_PER_RUN entries that are overdue for a refresh.

    Criteria:
    - article_id must be present (older entries without an id are skipped)
    - live_url must be present
    - ts must be older than REFRESH_CADENCE_DAYS
    """
    cutoff = time.time() - REFRESH_CADENCE_DAYS * 86_400
    candidates = [
        entry
        for entry in state.get("covered_topics", [])
        if entry.get("article_id")
        and entry.get("live_url")
        and entry.get("ts", 0) < cutoff
    ]
    candidates.sort(key=lambda e: e.get("ts", 0))
    return candidates[:MAX_REFRESH_PER_RUN]


# ── Shopify helpers ──────────────────────────────────────────────────────────

_FETCH_QUERY = """
query FetchArticle($id: ID!) {
  article(id: $id) {
    id
    title
    body
  }
}
"""


def fetch_article_from_shopify(article_id: str) -> dict | None:
    """Fetch the current article body from Shopify. Returns the node dict or None."""
    try:
        result = call(_FETCH_QUERY, {"id": article_id})
        return (result.get("data") or {}).get("article") or None
    except Exception as exc:  # noqa: BLE001
        print(f"  [fetch] error fetching {article_id}: {exc}")
        return None


_UPDATE_MUTATION = """
mutation ArticleUpdate($id: ID!, $body: String!, $summary: String!) {
  articleUpdate(id: $id, article: {body: $body, summary: $summary}) {
    article {
      id
      updatedAt
    }
    userErrors {
      field
      message
    }
  }
}
"""


def update_article(article_id: str, body_html: str, summary: str) -> bool:
    """Push updated body HTML and summary back to Shopify.

    Returns True on success, False if Shopify returned userErrors or the
    call raised an exception.
    """
    try:
        result = call(
            _UPDATE_MUTATION,
            {"id": article_id, "body": body_html, "summary": summary},
        )
        errors = (
            (result.get("data") or {})
            .get("articleUpdate", {})
            .get("userErrors", [])
        )
        if errors:
            for err in errors:
                print(f"  [update] userError: {err.get('field')} — {err.get('message')}")
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  [update] exception: {exc}")
        return False


# ── core refresh logic ───────────────────────────────────────────────────────

def refresh_article(entry: dict) -> bool:
    """Regenerate and re-publish one article entry.

    Mutates *entry* in place on success (updates ts and signature) so the
    caller can persist state immediately after.
    """
    article_id: str = entry["article_id"]
    title: str = entry.get("title", "")
    print(f"\n  Refreshing: {title}")
    print(f"  article_id: {article_id}")

    # 1. Re-generate markdown content.
    topic = {"title": title, "subreddit": entry.get("subreddit")}
    try:
        md: str = generate_article(topic, [], [])
    except Exception as exc:  # noqa: BLE001
        print(f"  [generate] failed: {exc}")
        return False

    # 2. Validate.
    ok, errors = validate(md)
    if not ok:
        print(f"  [validate] failed with {len(errors)} error(s):")
        for err in errors:
            print(f"    - {err}")
        return False

    # 3. Build body HTML + summary.
    # Use live_url slug as the handle hint for internal-link generation.
    live_url: str = entry.get("live_url", "")
    handle_hint = live_url.rstrip("/").split("/")[-1] if live_url else slugify(title)
    try:
        body_html, summary = build_article_body_html(md, "", handle_hint)
    except Exception as exc:  # noqa: BLE001
        print(f"  [build_html] failed: {exc}")
        return False

    # 4. Push to Shopify.
    if not update_article(article_id, body_html, summary):
        return False

    # 5. Update state entry (reset refresh timer; re-sign content).
    entry["ts"] = time.time()
    entry["signature"] = signature(md)
    print(f"  Done — next refresh due in {REFRESH_CADENCE_DAYS} days.")
    return True


# ── entrypoint ───────────────────────────────────────────────────────────────

def main() -> int:
    load_env()

    state = load_state()
    candidates = pick_articles_to_refresh(state)

    if not candidates:
        print("No articles due for refresh.")
        return 0

    print(f"Articles due for refresh: {len(candidates)}")

    # Build a fast lookup so we can update entries in place.
    # Keyed by article_id since that is guaranteed to be present.
    index: dict[str, dict] = {
        e["article_id"]: e
        for e in state.get("covered_topics", [])
        if e.get("article_id")
    }

    refreshed = 0
    failed = 0
    for entry in candidates:
        success = refresh_article(entry)
        if success:
            # entry was mutated in place; sync back into state via index
            index[entry["article_id"]].update(entry)
            refreshed += 1
        else:
            failed += 1

        # Persist after every article so a mid-run crash doesn't lose progress.
        save_state(state)

    print(f"\nSummary: {refreshed} refreshed, {failed} failed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
