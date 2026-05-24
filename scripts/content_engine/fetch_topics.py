"""Fetch and score supplement topics from Reddit.

Pulls hot posts from r/Nootropics, r/Supplements, r/Biohackers, r/HubermanLab,
r/Longevity. Scores by (upvotes * 0.6) + (comments * 1.4) — comments weighted
higher because they signal active debate, which makes for better article
fodder than pure upvote count.

Uses Reddit's unauthenticated JSON endpoints. Sets a real User-Agent because
Reddit blocks default urllib UA.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from typing import Iterable

SUBREDDITS = ["Nootropics", "Supplements", "Biohackers", "HubermanLab", "Longevity"]
USER_AGENT = "Elm-And-Rye-Content-Bot/1.0 (https://elmandrye.com)"
MIN_UPVOTES = 30
MIN_COMMENTS = 8
MAX_AGE_HOURS = 36  # only consider posts from the last day-ish


def fetch_subreddit_hot(sub: str, limit: int = 25) -> list[dict]:
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}&t=day"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[warn] fetch failed for r/{sub}: {e}", file=sys.stderr)
        return []
    return [child["data"] for child in data.get("data", {}).get("children", [])]


def fetch_top_comments(permalink: str, limit: int = 8) -> list[str]:
    """Fetch top-level comment bodies for added topic context."""
    url = f"https://www.reddit.com{permalink}.json?limit={limit}&sort=top"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"[warn] comment fetch failed for {permalink}: {e}", file=sys.stderr)
        return []
    comments = []
    if len(data) > 1:
        for child in data[1].get("data", {}).get("children", []):
            body = child.get("data", {}).get("body", "")
            if body and body not in ("[deleted]", "[removed]") and len(body) > 60:
                comments.append(body[:800])
            if len(comments) >= limit:
                break
    return comments


def score_post(post: dict) -> float:
    return post.get("ups", 0) * 0.6 + post.get("num_comments", 0) * 1.4


def looks_supplement_related(post: dict) -> bool:
    """Light heuristic — these subs are already supplement-focused but
    filter out off-topic posts (memes, mod announcements, general chat)."""
    title = (post.get("title", "") + " " + post.get("selftext", ""))[:600].lower()
    if not title:
        return False

    bad = ["mod post", "weekly thread", "daily thread", "meme", "amazon link",
           "discount code", "where to buy", "is this legit", "fake reddit",
           "looking for a doctor"]
    if any(b in title for b in bad):
        return False

    good = ["mg", "mcg", "iu", "dose", "stack", "supplement", "extract", "form",
            "study", "research", "mechanism", "absorption", "bioavailab", "trial",
            "vitamin", "mineral", "amino", "nootropic", "ashwag", "rhodiola",
            "creatine", "magnesium", "tongkat", "berberine", "nac", "glycine",
            "theanine", "cortisol", "hpa", "sleep", "recovery", "stress",
            "longevity", "biohack", "huberman", "fadiman", "andrew", "peter attia"]
    return any(g in title for g in good)


def is_too_old(post: dict, max_hours: int = MAX_AGE_HOURS) -> bool:
    created = post.get("created_utc", 0)
    return (time.time() - created) > (max_hours * 3600)


def fetch_topic_candidates() -> list[dict]:
    """Return scored, filtered candidates across all subs, newest-first within score."""
    all_posts: list[dict] = []
    for sub in SUBREDDITS:
        posts = fetch_subreddit_hot(sub, limit=25)
        for p in posts:
            if p.get("stickied") or p.get("over_18"):
                continue
            if is_too_old(p):
                continue
            if p.get("ups", 0) < MIN_UPVOTES or p.get("num_comments", 0) < MIN_COMMENTS:
                continue
            if not looks_supplement_related(p):
                continue
            p["_score"] = score_post(p)
            p["_sub"] = sub
            all_posts.append(p)
        time.sleep(1)  # be polite to Reddit
    all_posts.sort(key=lambda p: p["_score"], reverse=True)
    return all_posts


def summarize_for_topic(post: dict) -> dict:
    """Trim a post down to what the writer needs."""
    return {
        "subreddit": post["_sub"],
        "title": post.get("title", "").strip(),
        "selftext": (post.get("selftext", "") or "")[:2000].strip(),
        "permalink": post.get("permalink", ""),
        "url": f"https://reddit.com{post.get('permalink', '')}",
        "ups": post.get("ups", 0),
        "num_comments": post.get("num_comments", 0),
        "score": post["_score"],
    }


if __name__ == "__main__":
    cands = fetch_topic_candidates()
    print(f"Found {len(cands)} qualifying candidates\n")
    for p in cands[:10]:
        print(f"  [{p['_score']:>6.1f}] r/{p['_sub']:14s} {p['title'][:90]}")
        print(f"           ups={p.get('ups')} comments={p.get('num_comments')}")
