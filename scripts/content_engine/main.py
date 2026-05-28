"""Daily orchestrator for the Elm & Rye content engine.

End-to-end flow:
  1. Load state (covered topics, last run)
  2. Fetch Reddit candidates; pick the highest-scoring undiscussed one,
     OR fall back to an evergreen topic
  3. Fetch top comments on the chosen Reddit thread (research context)
  4. Build the internal-link registry from current Shopify products + articles
  5. Generate the article via Claude
  6. Validate; retry once with feedback if invalid; exit if still invalid
  7. Generate a hero image via DALL-E and upload to Shopify Files
  8. Publish to /blogs/news (LIVE per user direction)
  9. Update state.json; print result; exit 0

Kill switch: set CONTENT_ENGINE_DRY_RUN=true to run the full pipeline
WITHOUT publishing. Useful for testing prompt/output changes safely.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from fetch_topics import fetch_topic_candidates, fetch_top_comments, summarize_for_topic  # noqa: E402
from evergreen_topics import get_evergreen_pool  # noqa: E402
from internal_links import build_link_registry, pick_relevant  # noqa: E402
from generate_article import generate as generate_article  # noqa: E402
from generate_image import generate_hero  # noqa: E402
from validate import validate  # noqa: E402
from publish import publish_article, extract_title, DuplicateArticleError  # noqa: E402
from originality import signature, find_most_similar, SIMILARITY_THRESHOLD  # noqa: E402
from formats import pick_format  # noqa: E402
from social import distribute  # noqa: E402

STATE_FILE = HERE / "state.json"
DRY_RUN = os.environ.get("CONTENT_ENGINE_DRY_RUN", "").lower() in ("true", "1", "yes")
DEDUP_WINDOW_DAYS = 90
MAX_ARTICLES_PER_7_DAYS = 5


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {"covered_topics": [], "evergreen_used": [], "run_log": []}
    return json.loads(STATE_FILE.read_text())


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def normalize(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', s.lower()).strip()


def is_duplicate(title: str, covered: list[dict], window_days: int = DEDUP_WINDOW_DAYS) -> bool:
    cutoff = time.time() - (window_days * 86400)
    norm_title = normalize(title)
    title_words = set(norm_title.split())
    for entry in covered:
        if entry.get("ts", 0) < cutoff:
            continue
        prior_words = set(normalize(entry["title"]).split())
        if not prior_words:
            continue
        overlap = len(title_words & prior_words) / max(len(title_words | prior_words), 1)
        if overlap > 0.55:  # significant title overlap = treat as covered
            return True
    return False


def pick_topic(state: dict) -> tuple[dict, list[str]]:
    """Return (topic_dict, top_comments). Reddit first, evergreen fallback."""
    covered = state.get("covered_topics", [])

    print("[1/9] Fetching Reddit candidates...")
    candidates = fetch_topic_candidates()
    print(f"      Got {len(candidates)} qualifying posts.")

    for c in candidates:
        title = c.get("title", "")
        if is_duplicate(title, covered):
            continue
        topic = summarize_for_topic(c)
        print(f"      Picked Reddit: r/{topic['subreddit']} — {topic['title'][:80]}")
        comments = fetch_top_comments(topic["permalink"])
        return topic, comments

    print("      No fresh Reddit topics — falling back to evergreen.")
    used = set(state.get("evergreen_used", []))
    pool = [t for t in get_evergreen_pool() if t not in used and not is_duplicate(t, covered)]
    if not pool:
        # fully cycled — reset and pick the oldest
        pool = get_evergreen_pool()
    topic_title = pool[0]
    print(f"      Picked evergreen: {topic_title}")
    return {"title": topic_title, "subreddit": None}, []


def keywords_for_topic(topic: dict) -> list[str]:
    """Naive keyword extraction for internal-link relevance."""
    text = topic.get("title", "") + " " + topic.get("selftext", "")
    words = re.findall(r'[a-zA-Z]{4,}', text.lower())
    stop = {"with", "that", "this", "what", "when", "from", "have", "would", "could",
            "should", "their", "there", "your", "they", "been", "than", "much",
            "more", "into", "about", "some", "like", "just", "best", "very"}
    return [w for w in words if w not in stop][:15]


def attempt_generate(topic: dict, comments: list[str], links: list[dict], format_prompt: str = ""):
    """Generate + validate. Returns (markdown, validation_result, attempts)."""
    md = generate_article(topic, comments, links, format_prompt=format_prompt)
    v = validate(md)
    if v.ok:
        return md, v, 1

    print(f"      Validation FAILED on attempt 1: {v.errors}")
    feedback = "\n".join(f"- {e}" for e in v.errors)
    md = generate_article(topic, comments, links, retry_feedback=feedback, format_prompt=format_prompt)
    v = validate(md)
    return md, v, 2


def recent_publish_count(state: dict, days: int = 7) -> int:
    cutoff = time.time() - (days * 86400)
    return sum(
        1
        for e in state.get("run_log", [])
        if e.get("status") == "published" and e.get("ts", 0) >= cutoff
    )


def check_originality(md: str, state: dict) -> tuple[float, dict | None, list[int]]:
    """Return (max_similarity_to_prior, matching_entry, new_signature)."""
    new_sig = signature(md)
    cutoff = time.time() - (DEDUP_WINDOW_DAYS * 86400)
    prior = [
        e for e in state.get("covered_topics", [])
        if e.get("ts", 0) >= cutoff and e.get("signature")
    ]
    sim, match = find_most_similar(new_sig, prior)
    return sim, match, new_sig


def email_failure(topic: dict, errors: list[str]) -> None:
    """Stub — logged to stdout for GitHub Actions visibility.
    Wire up Resend/SMTP later if you want a real inbox notification."""
    print("=" * 60)
    print("CONTENT ENGINE: GENERATION FAILED — NO ARTICLE PUBLISHED")
    print("=" * 60)
    print(f"Topic: {topic.get('title')}")
    print("Errors:")
    for e in errors:
        print(f"  - {e}")
    print("=" * 60)


def main() -> int:
    started = datetime.now(timezone.utc).isoformat()
    print(f"[content_engine] started {started}  dry_run={DRY_RUN}")
    state = load_state()

    # Cadence cap — skip the run entirely if we've already published the weekly max.
    # The dry-run path still proceeds so we can preview without affecting state.
    recent = recent_publish_count(state, days=7)
    if recent >= MAX_ARTICLES_PER_7_DAYS and not DRY_RUN:
        print(f"[skip] {recent} articles published in the last 7 days "
              f"(cap = {MAX_ARTICLES_PER_7_DAYS}). Exiting cleanly without publishing.")
        return 0
    if DRY_RUN and recent >= MAX_ARTICLES_PER_7_DAYS:
        print(f"[note] {recent} articles in last 7 days would trip cadence cap "
              f"({MAX_ARTICLES_PER_7_DAYS}) in a real run.")

    # Step 1-3: topic
    topic, comments = pick_topic(state)

    # Step 4: internal links
    print("[4/9] Building internal link registry...")
    registry = build_link_registry()
    print(f"      {len(registry['products'])} products, {len(registry['articles'])} articles")
    keywords = keywords_for_topic(topic)
    links = pick_relevant(keywords, registry, max_n=8)
    print(f"      Selected {len(links)} candidate links for the writer.")

    # Step 4b: pick article format
    fmt = pick_format(state)
    print(f"      Format: {fmt['id']}")

    # Step 5-6: generate + validate
    print("[5/9] Generating article with Claude...")
    md, v, attempts = attempt_generate(topic, comments, links, format_prompt=fmt["structure_prompt"])
    print(f"      Done. Attempts: {attempts}. Word count: {v.stats.get('word_count_main', '?')}.")

    if not v.ok:
        print(f"[6/9] Validation FAILED after retry. Errors: {v.errors}")
        email_failure(topic, v.errors)
        # still log to state so we don't retry the same topic tomorrow
        state.setdefault("run_log", []).append({
            "ts": time.time(), "topic": topic.get("title"),
            "status": "validation_failed", "errors": v.errors,
        })
        save_state(state)
        return 1
    print(f"[6/9] Validation OK. Warnings: {v.warnings}")

    # Originality check — Jaccard of MinHash signatures vs last 90 days of articles.
    sim, match, new_sig = check_originality(md, state)
    print(f"      Originality: max similarity to a prior article = {sim:.2f} "
          f"(threshold {SIMILARITY_THRESHOLD:.2f}).")
    if sim > SIMILARITY_THRESHOLD:
        prior_title = (match or {}).get("title", "<unknown>")
        msg = (f"Article too similar to prior post '{prior_title}' "
               f"(Jaccard {sim:.2f} > {SIMILARITY_THRESHOLD:.2f}). Skipping publish.")
        print(f"[6.5/9] ORIGINALITY FAIL — {msg}")
        email_failure(topic, [msg])
        state.setdefault("run_log", []).append({
            "ts": time.time(), "topic": topic.get("title"),
            "status": "originality_failed", "similarity": sim,
            "matched_title": prior_title,
        })
        save_state(state)
        return 1

    title = extract_title(md)
    print(f"      Title: {title}")

    # Step 7: hero image
    print("[7/9] Generating hero image via DALL-E...")
    hero_url = generate_hero(title)
    if hero_url:
        print(f"      Hero: {hero_url}")
    else:
        print("      Hero generation failed — publishing without image.")

    # Step 8: publish
    if DRY_RUN:
        print("[8/9] DRY RUN — skipping publish.")
        print("---ARTICLE PREVIEW---")
        print(md[:2000])
        print(f"...({len(md)} chars total)...")
        return 0

    print("[8/9] Publishing to Shopify...")
    try:
        article = publish_article(md, topic, hero_image_url=hero_url, publish_live=True)
    except DuplicateArticleError as e:
        print(f"[8/9] SKIP — {e}")
        state.setdefault("run_log", []).append({
            "ts": time.time(), "topic": topic.get("title"),
            "status": "duplicate_skipped", "reason": str(e),
        })
        save_state(state)
        return 0
    print(f"      LIVE: {article['live_url']}")

    print("[8b/9] Distributing to social...")
    distribute(article, md)

    # Step 9: state update
    state.setdefault("covered_topics", []).append({
        "ts": time.time(),
        "title": topic["title"],
        "subreddit": topic.get("subreddit"),
        "live_url": article["live_url"],
        "article_id": article["id"],
        "signature": new_sig,
    })
    if not topic.get("subreddit"):
        state.setdefault("evergreen_used", []).append(topic["title"])
    state.setdefault("run_log", []).append({
        "ts": time.time(),
        "topic": topic["title"],
        "status": "published",
        "live_url": article["live_url"],
        "word_count": v.stats.get("word_count_main"),
        "attempts": attempts,
    })
    save_state(state)
    print(f"[9/9] State saved. Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
