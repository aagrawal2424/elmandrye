"""Analyze /tmp/cs_analysis/raw.jsonl (3000 tickets) to produce 3 reports:
  1. CS improvements — recurring complaints, gaps, what to auto-pre-empt
  2. Revenue opportunities — upsell/cross-sell asks, product gaps
  3. Product perception — which SKUs help vs hurt us

Pipeline:
  Step 1. Filter: drop B2B spam + non-customer noise using the same
          patterns the audit identified.
  Step 2. Chunk real-customer tickets into batches of 50.
  Step 3. For each batch, call Claude (Anthropic Messages API) with a
          tight JSON schema to extract structured per-ticket signals.
  Step 4. Aggregate across all batches → 3 markdown reports.

Outputs:
  /tmp/cs_analysis/classified.jsonl    — one row per real-customer ticket
  /tmp/cs_analysis/report_cs.md
  /tmp/cs_analysis/report_revenue.md
  /tmp/cs_analysis/report_products.md
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

env = load_env()
ANTHROPIC_KEY = env["ANTHROPIC_API_KEY"]
ANTHROPIC_MODEL = env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

IN_FILE = Path("/tmp/cs_analysis/raw.jsonl")
OUT_DIR = Path("/tmp/cs_analysis")
CLASSIFIED_FILE = OUT_DIR / "classified.jsonl"

BATCH_SIZE = 50


# ── Step 1: filter noise ─────────────────────────────────────────────────────

B2B_NOISE = re.compile(
    r"\b(supplement\s+manufactur|private\s+label|copacker?|matcha\s+(powder\s+)?supply|"
    r"OEM|ODM|contract\s+manufactur|ingredient\s+(source|supply)|tablet\s+press|"
    r"softgel|gummy\s+production|white\s+label|"
    r"strategic\s+(external\s+)?SEO|SEO\s+audit|backlink|guest\s+post|sponsored\s+post|"
    r"lead\s+(generation|list)|leads?\s+for|shopify\s+(expert|partner|agency)|"
    r"website\s+(redesign|design)|digital\s+marketing\s+agency|"
    r"reduce\s+(cart\s+)?abandonment|broken\s+links?|cart\s+recovery|"
    r"complementary\s+retouching|product\s+photo|photoshoot|video\s+production|"
    r"influencer\s+(marketing|outreach)|content\s+creator|"
    r"affiliate\s+(network|signed\s+up|signup|notification)|new\s+publisher|"
    r"awin|coupon\s+(partner|site)|coupert|"
    r"(geek|pulse|beri|cliq|aerostar)\s+(bar|pro|x\s*\d)|disposable\s+vape|vape\s+(pod|pen)|"
    r"ejuice|e-juice|kratom|delta[\s-]?[89]|HHC|THCa?|"
    r"interzoo|supplyside|natural\s+products\s+expo|cosmoprof|cannabis\s+conference|"
    r"merchant\s+settlement|class\s+action|reconsideration\s+request|"
    r"marijuana\s+herald|tiktok\s+sellers|LIVE\s+selling|"
    r"hello\s+elmandrye|new\s+message\s+for\s+elmandrye|hi\s+there.+i.+represent|"
    r"automatic\s+reply|out\s+of\s+office|"
    r"payout\s+for|your\s+receipt\s+from|klaviyo\s+payment)\b",
    re.IGNORECASE,
)
NOISE_SENDER = re.compile(
    r"@(klaviyo|gorgias|shopify(plus)?|stripe|paypal|mailgun|sendgrid|hubspot|"
    r"awin|tiktokshop|substack|googlegroups|noreply|no-reply|newsletter|bounces?|"
    r"do.?not.?reply|automated|auto-reply|notifications?\.|discoverservices|"
    r"info@(themarijuanaherald|firstwireapp|seedlessmediagroup|buddify|"
    r"huskiapps|zipchatdtccloud|rankinglinks)\.)",
    re.IGNORECASE,
)


def is_noise(row: dict) -> tuple[bool, str]:
    text = (row.get("subject") or "") + "\n" + (row.get("message") or "")
    email = (row.get("customer_email") or "").lower()
    if NOISE_SENDER.search(email):
        return True, "sender"
    if B2B_NOISE.search(text):
        return True, "content"
    # Empty body + no subject = noise (auto-bounces, etc.)
    if not row.get("message") and not (row.get("subject") or "").strip():
        return True, "empty"
    return False, ""


# ── Step 2: Claude analysis per batch ────────────────────────────────────────

EXTRACTION_PROMPT = """You are analyzing customer-service tickets from elm & rye (elmandrye.com), a premium DTC supplement brand. For EACH ticket below, extract a structured signal. Return ONLY a JSON object with the schema:

{
  "tickets": [
    {
      "id": <int, the ticket id given>,
      "is_real_customer": <bool, true if this is a customer of elm & rye writing about their order/product/experience, false for vendor pitches/spam/B2B/automated/affiliate notifications>,
      "issue_category": <"where_is_my_order" | "shipping_delay" | "damaged_or_missing" | "wrong_item" | "refund_request" | "return_request" | "subscription_management" | "product_question" | "side_effect_or_health" | "discount_request" | "wholesale_or_b2b" | "praise_or_review" | "complaint_general" | "other">,
      "products_mentioned": [<list of product names, e.g. "Shilajit", "Ashwagandha", "Beard Oil"; empty list if none>],
      "sentiment": <"positive" | "neutral" | "negative" | "very_negative">,
      "key_signal": <string, one short sentence: what does the customer want?>,
      "revenue_opportunity": <string or null, e.g. "asked if we sell magnesium" or "wants larger size">,
      "improvement_signal": <string or null, e.g. "shipping email lacked tracking link" or "label confusion between forms">
    }
  ]
}

Be conservative: if is_real_customer is false, leave the other fields with reasonable defaults but mark sentiment as neutral. Lean negative on shipping/damaged/refund complaints. Lean positive on praise.

Tickets:
"""


def call_claude(batch: list[dict], retries: int = 3) -> list[dict] | None:
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8000,
        "messages": [{
            "role": "user",
            "content": EXTRACTION_PROMPT + json.dumps([
                {
                    "id": r["id"],
                    "subject": (r.get("subject") or "")[:200],
                    "message": (r.get("message") or "")[:1500],
                    "customer_email": r.get("customer_email"),
                }
                for r in batch
            ], indent=1),
        }],
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.loads(r.read())
            raw = resp["content"][0]["text"].strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start < 0 or end < 0:
                print(f"  [claude] no JSON in response (attempt {attempt+1})")
                continue
            parsed = json.loads(raw[start:end + 1])
            return parsed.get("tickets") or []
        except Exception as e:
            print(f"  [claude] attempt {attempt+1} failed: {e}")
            time.sleep(2 ** attempt)
    return None


# ── Step 3: aggregate ────────────────────────────────────────────────────────

def report_cs_improvements(classified: list[dict]) -> str:
    issue_counts = Counter()
    improvement_signals = []
    negative_complaints = []
    for c in classified:
        if not c.get("is_real_customer"):
            continue
        issue_counts[c.get("issue_category") or "other"] += 1
        if c.get("improvement_signal"):
            improvement_signals.append(c["improvement_signal"])
        if c.get("sentiment") in ("negative", "very_negative") and c.get("key_signal"):
            negative_complaints.append(c["key_signal"])

    sig_counts = Counter(s.lower().strip(".") for s in improvement_signals)
    comp_counts = Counter(s.lower().strip(".") for s in negative_complaints)

    md = ["# CS Improvement Report\n"]
    total_real = sum(issue_counts.values())
    md.append(f"_Based on {total_real} real-customer tickets (out of {len(classified)} total in the 3,000-ticket window)._\n")
    md.append("\n## Top issue categories\n")
    for cat, n in issue_counts.most_common(15):
        md.append(f"- **{cat}**: {n}")
    md.append("\n## Recurring improvement signals (verbatim, deduped)\n")
    for sig, n in sig_counts.most_common(25):
        md.append(f"- ({n}×) {sig}")
    md.append("\n## Top negative complaints (verbatim, deduped)\n")
    for sig, n in comp_counts.most_common(20):
        md.append(f"- ({n}×) {sig}")
    return "\n".join(md)


def report_revenue(classified: list[dict]) -> str:
    opportunities = []
    for c in classified:
        if c.get("is_real_customer") and c.get("revenue_opportunity"):
            opportunities.append(c["revenue_opportunity"])
    opp_counts = Counter(o.lower().strip(".") for o in opportunities)
    md = ["# Revenue Opportunity Report\n"]
    md.append(f"_{len(opportunities)} explicit revenue signals across {len(classified)} tickets._\n")
    md.append("\n## Top customer-asked opportunities (verbatim, deduped)\n")
    for opp, n in opp_counts.most_common(50):
        md.append(f"- ({n}×) {opp}")
    return "\n".join(md)


def report_products(classified: list[dict]) -> str:
    product_mentions = Counter()
    product_sentiment = defaultdict(Counter)
    product_complaints = defaultdict(list)
    product_praise = defaultdict(list)
    for c in classified:
        if not c.get("is_real_customer"):
            continue
        for p in (c.get("products_mentioned") or []):
            p_norm = p.strip().title()
            if not p_norm:
                continue
            product_mentions[p_norm] += 1
            product_sentiment[p_norm][c.get("sentiment") or "neutral"] += 1
            if c.get("sentiment") in ("negative", "very_negative") and c.get("key_signal"):
                product_complaints[p_norm].append(c["key_signal"])
            elif c.get("sentiment") == "positive" and c.get("key_signal"):
                product_praise[p_norm].append(c["key_signal"])

    md = ["# Product Perception Report\n"]
    md.append("\n## Product mention frequency + sentiment mix\n")
    md.append("| Product | Mentions | pos | neu | neg | v.neg | Net |\n|---|---:|---:|---:|---:|---:|---:|")
    for prod, n in product_mentions.most_common(30):
        s = product_sentiment[prod]
        net = (s.get("positive", 0)) - (s.get("negative", 0) + 2 * s.get("very_negative", 0))
        md.append(f"| {prod} | {n} | {s.get('positive',0)} | {s.get('neutral',0)} | "
                  f"{s.get('negative',0)} | {s.get('very_negative',0)} | {net:+d} |")
    md.append("\n## Top complaints by product\n")
    for prod in sorted(product_complaints.keys(),
                       key=lambda p: -len(product_complaints[p]))[:15]:
        md.append(f"\n### {prod} ({len(product_complaints[prod])} complaints)\n")
        for s in product_complaints[prod][:10]:
            md.append(f"- {s}")
    md.append("\n## Top praise by product\n")
    for prod in sorted(product_praise.keys(), key=lambda p: -len(product_praise[p]))[:10]:
        md.append(f"\n### {prod} ({len(product_praise[prod])} positive mentions)\n")
        for s in product_praise[prod][:5]:
            md.append(f"- {s}")
    return "\n".join(md)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"=== analyze_3000.py ===")
    rows = [json.loads(l) for l in IN_FILE.read_text().splitlines() if l.strip()]
    print(f"loaded {len(rows)} raw tickets")

    # Filter
    real_candidates = []
    noise_count = Counter()
    for r in rows:
        bad, reason = is_noise(r)
        if bad:
            noise_count[reason] += 1
        else:
            real_candidates.append(r)
    print(f"after noise filter: {len(real_candidates)} candidate real tickets "
          f"(noise filtered: {dict(noise_count)})")

    # Batch + classify with Claude
    classified: list[dict] = []
    batches = [real_candidates[i:i + BATCH_SIZE]
               for i in range(0, len(real_candidates), BATCH_SIZE)]
    print(f"sending {len(batches)} batches of up to {BATCH_SIZE} tickets to {ANTHROPIC_MODEL}")
    for i, batch in enumerate(batches, 1):
        print(f"  batch {i}/{len(batches)} ({len(batch)} tickets)...")
        result = call_claude(batch)
        if result is None:
            print(f"    skipping (call failed after retries)")
            continue
        classified.extend(result)
        # Save incrementally
        with CLASSIFIED_FILE.open("w") as f:
            for c in classified:
                f.write(json.dumps(c) + "\n")
        time.sleep(0.5)

    print(f"\nclassified {len(classified)} tickets")
    real_count = sum(1 for c in classified if c.get("is_real_customer"))
    print(f"  → {real_count} real customers")
    print(f"  → {len(classified) - real_count} flagged as not-real by Claude")

    # Generate reports
    print("\nwriting reports...")
    (OUT_DIR / "report_cs.md").write_text(report_cs_improvements(classified))
    (OUT_DIR / "report_revenue.md").write_text(report_revenue(classified))
    (OUT_DIR / "report_products.md").write_text(report_products(classified))
    print(f"  /tmp/cs_analysis/report_cs.md")
    print(f"  /tmp/cs_analysis/report_revenue.md")
    print(f"  /tmp/cs_analysis/report_products.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
