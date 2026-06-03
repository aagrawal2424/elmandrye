"""Phase 1: design Gorgias auto-rules for B2B/vendor spam categories
identified in the 30d audit.

For each rule:
  1. Spec the conditions (subject keywords, sender patterns)
  2. Backtest against /tmp/cs_audit/raw_tickets.json: how many tickets
     in the last 30d would have been caught, and how many of those
     were real customers (= FALSE POSITIVES we need to know about)
  3. Print a report so AJ can scan before any rule ships

Read-only — does NOT push anything to Gorgias. Push happens in a
separate step after AJ approves the backtest results.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("/tmp/cs_audit")
raw_tickets = json.load(open(OUT / "raw_tickets.json"))
real_customers = {c["id"] for c in json.load(open(OUT / "real_v2.json"))}


# ── Rule specs ──────────────────────────────────────────────────────────────
# Each rule is {name, subject_keywords, sender_keywords (substring in email)}
# Conditions are OR within a rule (subject OR sender match).
# The Gorgias DSL we'll emit: containsAny(ticket.subject, [...]) ||
#                              containsAny(message.sender.email, [...])

RULES = [
    {
        "name": "spam-supplier-pitches",
        "tag": "auto-spam-supplier",
        "subject_keywords": [
            "supplement manufactur", "supplement supply", "private label",
            "copacker", "co-packer", "matcha powder supply", "matcha supply",
            "ingredient source", "ingredient supply", "tablet press",
            "softgel manufactur", "capsule manufactur", "gummy production",
            "OEM", "ODM", "contract manufactur", "raw material supply",
            "white label",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-seo-leadgen-agencies",
        "tag": "auto-spam-agency",
        "subject_keywords": [
            "strategic external SEO", "SEO audit", "SEO service", "SEO optimi",
            "backlink", "guest post", "sponsored post", "lead generation",
            "lead list", "leads for", "Elmandrye Health Supplement Stores",
            "shopify expert", "shopify partner", "shopify agency",
            "website redesign", "website design",
            "digital marketing agency", "social media management",
            "email marketing audit", "complementary retouching",
            "Maximize Q1 Sales", "Coupert", "exclusive code",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-shopify-app-pitches",
        "tag": "auto-spam-shopify-app",
        "subject_keywords": [
            "reduce shopify support cost", "reduce cart abandonment",
            "increase conversion", "hide sold-out variants",
            "shopify stores lose buyers", "boost your conversion",
            "your product pages are missing",
        ],
        "sender_keywords": [
            "@buddify.", "@firstwireapp.", "@huskiapps.", "@zipchatdtccloud.",
            "@rankinglinks.",
        ],
    },
    {
        "name": "spam-vape-cannabis-products",
        "tag": "auto-spam-vape",
        "subject_keywords": [
            "Geek Bar", "Pulse X", "Beri Cliq", "Aerostar Pro",
            "disposable vape", "vape pod", "ejuice", "e-juice",
            "kratom", "delta-8", "delta 8", "HHC", "THCa",
            "Discover Your Perfect Beri",
        ],
        "sender_keywords": [
            "@vaperanger.", "@themarijuanaherald.",
        ],
    },
    {
        "name": "spam-trade-show-invites",
        "tag": "auto-spam-tradeshow",
        "subject_keywords": [
            "Walking Interzoo", "Walking SupplySide", "Walking Natural Products",
            "Walking Cosmoprof", "meet up at Interzoo", "meet at Interzoo",
            "Primetime TV Awards Red Carpet Swag",
            "red carpet swag bag",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-awin-affiliate-notifications",
        "tag": "auto-spam-affiliate",
        "subject_keywords": [
            "Awin new publisher notification",
            "URGENT Action Required – Account Balance Critical",
            "Affiliate Activity",
        ],
        "sender_keywords": [
            "@awin.",
        ],
    },
    {
        "name": "spam-marketing-reply-chains",
        "tag": "auto-spam-marketing-reply",
        "subject_keywords": [
            "Automatic reply:", "Out of Office", "Out of office:",
            "Re: Unlock Relaxation", "Re: Last Chance",
            "Re: Buy 1, Get 2 Free", "Re: Don't Miss Out",
            "Re: Your Opt-in",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-photo-video-services",
        "tag": "auto-spam-creative-services",
        "subject_keywords": [
            "product photo", "Photoshoot with Models",
            "complementary retouching", "video production",
            "influencer marketing", "content creator collab",
            "free packaging design", "packaging design",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-cold-outreach-openers",
        "tag": "auto-spam-cold-outreach",
        "subject_keywords": [
            "Can I ask Elmandrye", "Hello Elmandrye", "Hi Elmandrye",
            "NEW MESSAGE FOR Elmandrye", "Quick check", "Quick finding",
            "Can I share a quick finding", "Brief intro",
            "30-second", "30 second",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-merchant-settlement-legal",
        "tag": "auto-spam-legal-notice",
        "subject_keywords": [
            "Discover Merchant Settlement", "Merchant Settlement",
            "class action", "Reconsideration Request",
        ],
        "sender_keywords": [
            "@discoverservices.",
        ],
    },
    {
        "name": "spam-newsletters-payment-notifications",
        "tag": "auto-spam-newsletter",
        "subject_keywords": [
            "Marijuana Herald Daily News",
            "Payout for",
            "Your receipt from Klaviyo",
            "site notification",
            "ROAS in Cannabis Campaign",
        ],
        "sender_keywords": [
            "@klaviyo.", "@themarijuanaherald.",
            "@seedlessmediagroup.", "@route.com",
        ],
    },
    {
        "name": "spam-tiktok-shop-pitches",
        "tag": "auto-spam-tiktok-shop",
        "subject_keywords": [
            "LIVE selling", "TikTok sellers", "tiktok and ig",
            "Spring is peak season",
        ],
        "sender_keywords": [
            "@shop.tiktok.com",
        ],
    },
    # Added 2026-06-03 after V1 dry-run caught these slipping through.
    {
        "name": "spam-affiliate-signup-notifications",
        "tag": "auto-spam-affiliate-signup",
        "subject_keywords": [
            "A new affiliate", "new affiliate signed up",
            "has signed up for your program",
            "affiliate signup", "new publisher signed up",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-instagram-digest-notifications",
        "tag": "auto-spam-social-digest",
        "subject_keywords": [
            "catch up on moments that you",
            "see tipsytina",  # specific spammy IG digest format
            "you've missed",  # paired with the IG sender — case-insensitive
            "puppersupplements,",  # this format keeps recurring
            "elmandrye, catch up",
        ],
        "sender_keywords": [
            "@mail.instagram.com", "@e.facebook.com", "@facebookmail.com",
            "@mail.facebook.com",
        ],
    },
    {
        "name": "spam-vape-pods-lowercase",
        "tag": "auto-spam-vape",
        # Gorgias DSL containsAny appears to be case-sensitive on some
        # builds. Re-list common spam terms in lowercase to be safe.
        "subject_keywords": [
            "geek bar", "pulse x", "beri cliq", "510 hidden cartridge",
            "disposable vape", "vape pod", "ejuice", "e-juice",
        ],
        "sender_keywords": [],
    },
    {
        "name": "spam-broken-link-seo-pitches",
        "tag": "auto-spam-agency",
        "subject_keywords": [
            "broken link on your store",
            "broken link is costing you",
            "broken links are hurting",
            "broken links on your site",
        ],
        "sender_keywords": [],
    },
]


def matches(rule: dict, ticket: dict) -> bool:
    subj = ticket.get("subject") or ""
    email = (ticket.get("customer") or {}).get("email") or ""
    for kw in rule["subject_keywords"]:
        if kw.lower() in subj.lower():
            return True
    for sk in rule["sender_keywords"]:
        if sk.lower() in email.lower():
            return True
    return False


print("=" * 78)
print("PHASE 1 SPAM RULE BACKTEST")
print(f"Tickets in 30d window: {len(raw_tickets)}")
print(f"Real-customer tickets (per audit): {len(real_customers)}")
print("=" * 78)
print()

total_caught_ids = set()
all_false_positives = []

for rule in RULES:
    caught = [t for t in raw_tickets if matches(rule, t)]
    caught_ids = {t["id"] for t in caught}
    fp = [t for t in caught if t["id"] in real_customers]
    fp_ids = {t["id"] for t in fp}

    total_caught_ids.update(caught_ids)
    all_false_positives.extend(fp)

    pct = (100 * len(caught) / len(raw_tickets)) if raw_tickets else 0
    fp_pct = (100 * len(fp) / len(caught)) if caught else 0
    marker = "  ⚠️ FALSE POSITIVE" if fp else "  ✓ clean"
    print(f"{rule['name']:42s}  caught={len(caught):4d} ({pct:4.1f}%)  fp={len(fp):2d} ({fp_pct:4.1f}%){marker}")
    if fp:
        print(f"      FALSE POSITIVES:")
        for t in fp[:5]:
            print(f"        id={t['id']}  subj={(t.get('subject') or '')[:80]!r}")
        if len(fp) > 5:
            print(f"        ... and {len(fp)-5} more")

print()
print("-" * 78)
print(f"TOTAL unique tickets caught: {len(total_caught_ids)} / {len(raw_tickets)} "
      f"({100*len(total_caught_ids)/len(raw_tickets):.1f}%)")
print(f"TOTAL false positives:       {len(all_false_positives)}")
print()
print("Rules with FP=0 are safe to ship as DEACTIVATED → activate after review.")
print("Rules with FP>0 need pattern narrowing before activation.")
