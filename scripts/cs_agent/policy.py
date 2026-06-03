"""Policy boundaries for the elmandrye CS agent. Single source of truth
for every "what's the cap / when do we escalate / who do we email" decision.

Locked-in values from AJ 2026-06-02 — DO NOT change without his go.
See [[project_elmandrye_cs_takeover_policy]] in memory for the rationale.
"""
from __future__ import annotations

# ── Refunds ──────────────────────────────────────────────────────────────────

# Max $ I can auto-refund without AJ approval. Above this → draft + escalate.
AUTO_REFUND_CAP_USD = 200.0

# Hard ceiling on TOTAL auto-refunds I can issue per UTC day, across all
# tickets. Runaway-bug guard. If a single bug somehow triggers many refunds
# in a short window, we stop at this number and email AJ.
DAILY_REFUND_CEILING_USD = 1000.0


# ── Reply autonomy ───────────────────────────────────────────────────────────

# Auto-send replies for these intents/tags. Anything else → draft for AJ.
AUTO_SEND_INTENTS = {
    "where_is_my_order",
    "return_exchange_portal_redirect",
    "subscription_change_skio_redirect",
    "faq_response",
    "order_cancel_simple",     # order not yet shipped, simple cancel
}

# These ALWAYS escalate to AJ — never auto-handle even if the rest of the
# policy would allow it.
ALWAYS_ESCALATE_TAGS = {
    "sentiments-threatening",
    "sentiments-offensive",
    "legal-complaint",       # we'll set this tag manually for now
    "medical-complaint",
    "manual-review-please",  # operator-set escape hatch
}


# ── Routing ──────────────────────────────────────────────────────────────────

# Supplier / manufacturer — for wrong product / missing item / quality issues.
SUPPLIER_EMAIL = "Ben@trytenkara.com"
SUPPLIER_NAME = "Ben"

# Warehouse manager — for damaged-in-transit / shipping carrier issues.
# Brennan runs Nitro Logistics, elmandrye's 3PL.
WAREHOUSE_EMAIL = "brennan@nitrologistics.co"
WAREHOUSE_NAME = "Brennan"


# ── Brand voice ──────────────────────────────────────────────────────────────

BRAND_SIGNATURE = "— Elm & Rye support team"  # never AJ's personal name
# Per [[feedback_anonymous_to_customers]] in memory.


# ── Rate limits / safety ─────────────────────────────────────────────────────

# Don't auto-respond to the same customer more than this many times in 24h.
# Catches loops where my reply triggers a customer reply that re-triggers me.
PER_CUSTOMER_24H_REPLY_CAP = 3

# Don't process tickets older than this many days at first run — backlog work
# is a separate job. New tickets only.
NEW_TICKET_MAX_AGE_HOURS = 24

# Hard timeout per ticket processing (excluding API calls). If a single
# ticket takes longer, something's wrong; skip + alert.
TICKET_PROCESS_TIMEOUT_SEC = 60


# ── Ops alerts ───────────────────────────────────────────────────────────────

OPS_EMAIL = "aj@portraitpal.ai"
ALERT_FROM = "cs-agent@notifications.elmandrye.com"
