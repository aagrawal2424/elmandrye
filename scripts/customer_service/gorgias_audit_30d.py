"""30-day Gorgias ticket audit for elmandrye CS systematization.

Pulls every ticket created in the last 30 days via cursor pagination,
collects tags / status / channel / customer / first+last message snippet,
classifies by issue type (using the existing 46-tag taxonomy + content
keywords), and writes a structured summary to /tmp/cs_audit/.

Output files (for me to read after the run):
  /tmp/cs_audit/raw_tickets.json    — all tickets with their tag ids
  /tmp/cs_audit/summary.json        — counts by category, sentiment, channel, agent, day
  /tmp/cs_audit/manufacturer_stuck.json — tickets likely blocked on manufacturer comms

Designed to be safe to re-run (read-only, no mutations). Rate-limited to
~5 req/sec by a 200ms sleep between cursor pages.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

env = load_env()
SUBDOMAIN = env["GORGIAS_SUBDOMAIN"]
AUTH = base64.b64encode(
    f"{env['GORGIAS_USERNAME']}:{env['GORGIAS_API_KEY']}".encode()
).decode()

OUT = Path("/tmp/cs_audit")
OUT.mkdir(exist_ok=True)

# Lookback window
LOOKBACK_DAYS = 30
CUTOFF = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)


def _get(path: str, retries: int = 3) -> dict:
    url = f"https://{SUBDOMAIN}/api{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Basic {AUTH}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "elmandrye-cs-audit/1.0 (Python)",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = float(e.headers.get("Retry-After", 5))
                print(f"[audit] 429, sleeping {wait}s")
                time.sleep(wait)
                continue
            if e.code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"out of retries: {path}")


# ── 1. Tag id → name lookup
print("[1/5] Building tag-id → name map...")
tag_map: dict[int, str] = {}
cursor = None
while True:
    path = "/tags?limit=100" + (f"&cursor={cursor}" if cursor else "")
    resp = _get(path)
    for t in resp.get("data", []):
        tag_map[t["id"]] = t["name"]
    cursor = resp.get("meta", {}).get("next_cursor")
    if not cursor:
        break
print(f"      {len(tag_map)} tags mapped.")


# ── 2. Pull tickets created in window via cursor pagination
print(f"\n[2/5] Pulling tickets created since {CUTOFF.isoformat()}...")
all_tickets: list[dict] = []
cursor = None
page = 0
while True:
    page += 1
    path = "/tickets?limit=100&order_by=created_datetime:desc"
    if cursor:
        path += f"&cursor={cursor}"
    resp = _get(path)
    batch = resp.get("data", [])
    if not batch:
        break

    # Filter to in-window tickets — API sorts desc, so once we hit one
    # older than the cutoff we can stop.
    stop = False
    for t in batch:
        try:
            ts = datetime.fromisoformat(t["created_datetime"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < CUTOFF:
            stop = True
            continue
        all_tickets.append(t)

    print(f"      page {page}: +{len(batch)} (total in-window: {len(all_tickets)})")
    cursor = resp.get("meta", {}).get("next_cursor")
    if not cursor or stop:
        break
    time.sleep(0.2)

print(f"      Collected {len(all_tickets)} tickets in last {LOOKBACK_DAYS}d.")
(OUT / "raw_tickets.json").write_text(json.dumps(all_tickets, indent=2, default=str))


# ── 3. Classify
print(f"\n[3/5] Classifying...")

ISSUE_TAGS = {
    "where_is_my_order": {"where-is-my-order"},
    "order_wrong": {"order-wrong"},
    "return_request": {"return-portal"},
    "refund_full": {"partial-refund"},  # also flag content-side full refunds
    "discount_request": {"discount-request"},
    "product_question": {"product", "PRODUCT"},
    "social_inbound": {"social-lead", "social-question"},
    "non_support": {"non-support-related", "spam"},
}

SENTIMENT_TAGS = {
    "negative": {"sentiments-negative", "negative"},
    "urgent": {"sentiments-urgent", "urgent"},
    "threatening": {"sentiments-threatening"},
    "offensive": {"sentiments-offensive"},
    "positive": {"sentiments-positive", "positive", "sentiments-promoter"},
}

VIP_TAG = "VIP"

# Keyword fallbacks (for tickets the bot didn't auto-tag)
KEYWORD_PATTERNS = {
    "where_is_my_order": re.compile(
        r"\b(where('s|s|\s+is)\s+(my|the)\s+(order|package|shipment)|haven['']?t\s+received|tracking\s+(number|info|update)|order\s+status|when\s+will\s+(I|my)\s+(get|receive))\b",
        re.IGNORECASE,
    ),
    "missing_item": re.compile(
        r"\b(missing\s+(item|bottle|product)|short(\s+|-)shipped|incomplete\s+order|didn['']?t\s+get|never\s+received)\b",
        re.IGNORECASE,
    ),
    "damaged_item": re.compile(
        r"\b(damaged|broken|leak(ed|ing)?|spilled|crushed|smashed|defective|cracked)\b",
        re.IGNORECASE,
    ),
    "wrong_item": re.compile(
        r"\b(wrong\s+(item|product|bottle|order)|sent\s+the\s+wrong|received\s+the\s+wrong)\b",
        re.IGNORECASE,
    ),
    "refund_demand": re.compile(
        r"\b(refund|chargeback|money\s+back|cancel\s+(my\s+)?order|return\s+(my\s+)?money)\b",
        re.IGNORECASE,
    ),
    "quality_complaint": re.compile(
        r"\b(taste(s)?\s+(bad|awful|terrible|weird|funny|off)|smell(s)?\s+(bad|funny|off)|expired|moldy|stale)\b",
        re.IGNORECASE,
    ),
    "subscription_issue": re.compile(
        r"\b(cancel\s+(my\s+)?subscription|unsubscribe|pause\s+(my\s+)?subscription|skio|subscription\s+(charge|billing))\b",
        re.IGNORECASE,
    ),
    "manufacturer_blocked": re.compile(
        r"\b(manufactur(er|ing)|warehouse|fulfillment\s+(center|partner)|3PL|supplier|batch\s+(number|code)|lot\s+(number|code))\b",
        re.IGNORECASE,
    ),
}


def classify(t: dict) -> dict:
    """Map a ticket to a structured row for the summary."""
    tag_names = {tag_map.get(tag.get("id") if isinstance(tag, dict) else tag, "")
                 for tag in (t.get("tags") or [])}
    tag_names.discard("")

    issue_cats = [cat for cat, tags in ISSUE_TAGS.items() if tag_names & tags]
    sentiment_cats = [s for s, tags in SENTIMENT_TAGS.items() if tag_names & tags]

    # Keyword fallback against ticket subject (we don't pull message bodies
    # here to keep request volume bounded; subject is a useful signal alone).
    subject = (t.get("subject") or "")
    keyword_hits = [name for name, pat in KEYWORD_PATTERNS.items() if pat.search(subject)]

    # Merge tag-based + keyword-based categories
    all_issues = sorted(set(issue_cats + keyword_hits))

    return {
        "id": t["id"],
        "created": t.get("created_datetime"),
        "updated": t.get("updated_datetime"),
        "status": t.get("status"),
        "channel": t.get("channel"),
        "via": t.get("via"),
        "customer_email": (t.get("customer") or {}).get("email"),
        "subject": subject[:120],
        "tags": sorted(tag_names),
        "issue_categories": all_issues,
        "sentiment": sentiment_cats,
        "vip": VIP_TAG in tag_names,
        "assignee": (t.get("assignee_user") or {}).get("email"),
        "messages_count": t.get("messages_count", 0),
    }


classified = [classify(t) for t in all_tickets]


# ── 4. Aggregate
print(f"\n[4/5] Aggregating...")

total = len(classified)
by_status = Counter(c["status"] for c in classified)
by_channel = Counter(c["channel"] for c in classified)
by_issue = Counter()
for c in classified:
    for iss in c["issue_categories"]:
        by_issue[iss] += 1
    if not c["issue_categories"]:
        by_issue["_uncategorized"] += 1
by_sentiment = Counter()
for c in classified:
    for s in c["sentiment"]:
        by_sentiment[s] += 1
by_assignee = Counter(c["assignee"] or "_unassigned" for c in classified)
by_day = defaultdict(int)
for c in classified:
    if c["created"]:
        day = c["created"][:10]
        by_day[day] += 1

# Open + stale
open_tickets = [c for c in classified if c["status"] == "open"]
stale = [c for c in open_tickets
         if c["updated"]
         and datetime.fromisoformat(c["updated"].replace("Z", "+00:00"))
         < datetime.now(timezone.utc) - timedelta(days=3)]

# Manufacturer-flagged: tickets matching damaged / missing / wrong-item OR
# the manufacturer keyword regex — these are the ones the workflow needs to
# auto-route to the supplier.
manufacturer_candidates = [c for c in classified if (
    "damaged_item" in c["issue_categories"]
    or "missing_item" in c["issue_categories"]
    or "wrong_item" in c["issue_categories"]
    or "order_wrong" in c["issue_categories"]
    or "manufacturer_blocked" in c["issue_categories"]
)]

(OUT / "manufacturer_stuck.json").write_text(json.dumps(manufacturer_candidates, indent=2, default=str))

vip = [c for c in classified if c["vip"]]
threatening = [c for c in classified if "threatening" in c["sentiment"] or "offensive" in c["sentiment"]]

summary = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "window": {"days": LOOKBACK_DAYS, "since": CUTOFF.isoformat()},
    "totals": {
        "tickets": total,
        "open": by_status.get("open", 0),
        "closed": by_status.get("closed", 0),
        "stale_open_3d_plus": len(stale),
        "vip": len(vip),
        "threatening_or_offensive": len(threatening),
        "manufacturer_blocked_candidates": len(manufacturer_candidates),
    },
    "by_channel": dict(by_channel.most_common()),
    "by_issue_category": dict(by_issue.most_common()),
    "by_sentiment": dict(by_sentiment.most_common()),
    "by_assignee": dict(by_assignee.most_common()),
    "by_day": dict(sorted(by_day.items())),
    "stale_open_sample": [
        {"id": c["id"], "subject": c["subject"], "tags": c["tags"], "updated": c["updated"]}
        for c in sorted(stale, key=lambda c: c["updated"] or "")[:10]
    ],
    "threatening_sample": [
        {"id": c["id"], "subject": c["subject"], "tags": c["tags"], "customer_email": c["customer_email"]}
        for c in threatening[:10]
    ],
}

(OUT / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
(OUT / "classified.json").write_text(json.dumps(classified, indent=2, default=str))


# ── 5. Print high-level
print(f"\n[5/5] Done. Headline numbers:")
print(f"  Total tickets:           {summary['totals']['tickets']}")
print(f"  Open right now:          {summary['totals']['open']}")
print(f"  Stale (open >3d no upd): {summary['totals']['stale_open_3d_plus']}")
print(f"  Manufacturer-candidate:  {summary['totals']['manufacturer_blocked_candidates']}")
print(f"  Threatening/offensive:   {summary['totals']['threatening_or_offensive']}")
print(f"  VIP:                     {summary['totals']['vip']}")
print()
print(f"Top channels: {dict(list(summary['by_channel'].items())[:5])}")
print(f"Top issues  : {dict(list(summary['by_issue_category'].items())[:8])}")
print()
print(f"Full outputs in {OUT}/")
