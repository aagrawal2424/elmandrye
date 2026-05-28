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
from generate_image import generate_hero, HeroPipelineFailure  # noqa: E402
from validate import validate  # noqa: E402
from publish import publish_article, extract_title, DuplicateArticleError  # noqa: E402
from video_summary import generate_video_for_article, build_video_block_html  # noqa: E402
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


def _alert_hero_failure(title: str, attempts_log: list[dict]) -> None:
    """Send an email via Resend when every image provider fails. Best-effort —
    if Resend isn't configured or fails, we just log and continue."""
    import urllib.request
    from get_token import load_env as _load
    env = _load()
    resend_key = env.get("RESEND_API_KEY", "")
    if not resend_key:
        print("[alert] no RESEND_API_KEY — skipping email alert")
        return

    rows = "".join(
        f"<tr><td>{a['provider']}</td><td>{a['attempt']}</td>"
        f"<td>{a['error_class']}</td><td>{a.get('latency_ms', 0)}ms</td>"
        f"<td>{(a['error_msg'] or '')[:200]}</td></tr>"
        for a in attempts_log
    )
    html = (
        "<h2>Content engine: hero image pipeline failed</h2>"
        f"<p><strong>Article topic:</strong> {title}</p>"
        "<p>All providers in the fallback chain exhausted. Article was NOT "
        "published (no imageless publish allowed).</p>"
        "<table border='1' cellpadding='6' style='border-collapse:collapse;font-family:monospace;font-size:12px;'>"
        "<tr><th>Provider</th><th>Attempt</th><th>Error class</th><th>Latency</th><th>Message</th></tr>"
        f"{rows}</table>"
        "<p>Most likely cause: stale/missing API key, OpenAI moderation, or "
        "the configured fallback providers aren't yet enabled (REPLICATE_API_TOKEN / STABILITY_API_KEY).</p>"
    )
    body = json.dumps({
        "from": "Elm & Rye content engine <support@elmandrye.com>",
        "to": ["aj@portraitpal.ai"],
        "subject": f"[content-engine] hero pipeline failed: {title[:60]}",
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {resend_key}",
            "Content-Type": "application/json",
            "User-Agent": "elmandrye-content-engine/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"[alert] email sent: {r.status}")
    except Exception as e:
        print(f"[alert] Resend send failed: {e}")


def _verify_and_repair_hero(article: dict, title: str, expected_hero_url: str) -> None:
    """Post-publish belt-and-suspenders: re-fetch the article, confirm Shopify
    actually attached the hero image. If not, regenerate via the chain and
    attach via articleUpdate. Idempotent — safe to no-op if image is fine.
    """
    import urllib.request
    import base64
    from get_token import load_env as _load, get_token as _gt
    env = _load()
    token = _gt()
    store = env.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
    api = env.get("SHOPIFY_API_VERSION", "2025-01")

    aid = article.get("id")
    if not aid:
        return
    # article['id'] from GraphQL is the GID; extract numeric for REST
    if isinstance(aid, str) and aid.startswith("gid://"):
        aid_num = aid.split("/")[-1]
    else:
        aid_num = str(aid)
    blog_gid = (article.get("blog") or {}).get("id")
    if isinstance(blog_gid, str) and blog_gid.startswith("gid://"):
        blog_id = blog_gid.split("/")[-1]
    else:
        # Fall back to the known elmandrye news blog id
        blog_id = "74623221917"

    # Step 1: fetch fresh
    url = f"https://{store}/admin/api/{api}/articles/{aid_num}.json?fields=id,title,image"
    req = urllib.request.Request(url, headers={"X-Shopify-Access-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"[8a/9] hero verifier: could not re-fetch article ({e})")
        return

    img = data.get("article", {}).get("image")
    if img and img.get("src"):
        print(f"[8a/9] hero verifier: image OK ({img['src']})")
        return

    print("[8a/9] hero verifier: image MISSING after publish — repairing…")

    # Step 2: regenerate (this raises HeroPipelineFailure if all providers fail
    # — we let that propagate up to the outer except in main loop)
    try:
        # Generate fresh bytes; reuse the chain runner directly so we get bytes,
        # not a Shopify-uploaded URL (we attach inline below).
        from generate_image import build_image_prompt, generate_hero_bytes, slug_from_title
        result = generate_hero_bytes(build_image_prompt(title))
    except HeroPipelineFailure as e:
        print(f"[8a/9] hero verifier: regenerate FAILED — {e}")
        try:
            _alert_hero_failure(f"[repair] {title}", e.attempts_log)
        except Exception:
            pass
        return

    # Step 3: attach via REST (uses base64 attachment field — no separate upload)
    attach_url = f"https://{store}/admin/api/{api}/blogs/{blog_id}/articles/{aid_num}.json"
    body = json.dumps({
        "article": {
            "id": int(aid_num),
            "image": {
                "attachment": base64.b64encode(result.png_bytes).decode(),
                "filename": f"blog-hero-{slug_from_title(title)}.png",
                "alt": f"Hero image: {title}",
            }
        }
    }).encode()
    req = urllib.request.Request(
        attach_url, data=body, method="PUT",
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read())
        new_src = (res.get("article") or {}).get("image", {}).get("src", "")
        print(f"[8a/9] hero verifier: REPAIRED — image now at {new_src}")
    except Exception as e:
        print(f"[8a/9] hero verifier: attach failed ({e})")


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

    # Step 7: hero image (provider chain with retries — FATAL if all fail)
    print("[7/9] Generating hero image via provider chain...")
    try:
        hero_url = generate_hero(title)
        print(f"      Hero: {hero_url}")
    except HeroPipelineFailure as e:
        # FAIL HARD — never publish imageless. This was the root cause of the
        # recurring "no photo on blog" bug. The previous code silently swallowed
        # this and let the article go live without a hero.
        print(f"[7/9] HERO PIPELINE FAILED — aborting publish for {title!r}")
        for entry in e.attempts_log:
            print(f"      {entry['provider']}#{entry['attempt']}  "
                  f"{entry['error_class']:<18} {entry.get('latency_ms', 0)}ms  "
                  f"{entry['error_msg'][:100]}")
        state.setdefault("run_log", []).append({
            "ts": time.time(),
            "topic": topic.get("title"),
            "status": "hero_pipeline_failed",
            "attempts_log": e.attempts_log,
        })
        save_state(state)
        # Best-effort alert via Resend
        try:
            _alert_hero_failure(title, e.attempts_log)
        except Exception as alert_err:
            print(f"      (alert send also failed: {alert_err})")
        return 2  # non-zero so GH Actions marks the run as failed

    # Step 7b: AI video summary (graceful — skips if no ElevenLabs key / no ffmpeg)
    print("[7b/9] Generating 90s video summary...")
    video_block_html = ""
    try:
        video = generate_video_for_article(md, title)
        if video:
            video_block_html = build_video_block_html(
                video_url=video.cdn_url,
                title=video.title,
                transcript=video.transcript,
                duration_sec=video.duration_sec,
                poster_url=hero_url or None,
            )
            print(f"      Video: {video.cdn_url} ({video.duration_sec:.1f}s)")
        else:
            print("      Video step skipped — publishing without summary video.")
    except Exception as e:
        print(f"      Video generation errored ({e}) — publishing without summary video.")

    # Step 8: publish
    if DRY_RUN:
        print("[8/9] DRY RUN — skipping publish.")
        print("---ARTICLE PREVIEW---")
        print(md[:2000])
        print(f"...({len(md)} chars total)...")
        return 0

    print("[8/9] Publishing to Shopify...")
    try:
        article = publish_article(
            md, topic,
            hero_image_url=hero_url,
            publish_live=True,
            video_block_html=video_block_html,
        )
    except DuplicateArticleError as e:
        print(f"[8/9] SKIP — {e}")
        state.setdefault("run_log", []).append({
            "ts": time.time(), "topic": topic.get("title"),
            "status": "duplicate_skipped", "reason": str(e),
        })
        save_state(state)
        return 0
    print(f"      LIVE: {article['live_url']}")

    # Step 8a: post-publish image health check.
    # Belt-and-suspenders — even though step 7 raises HeroPipelineFailure on
    # full chain failure, articleCreate could still drop the image (Shopify
    # transient, attachment encoding issue, race). Re-fetch the article and
    # if image is still missing, regenerate + articleUpdate-attach.
    try:
        _verify_and_repair_hero(article, title, hero_url)
    except Exception as e:
        print(f"[8a/9] hero verifier errored ({e}) — continuing")

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
