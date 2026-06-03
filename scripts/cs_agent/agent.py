"""CS agent main loop. Polls Gorgias for new tickets every cron tick,
classifies each one, and either auto-handles, drafts, or escalates.

V1 scope (this file):
  - Skip tickets already auto-tagged as spam (Phase 1 rules handle them)
  - Skip tickets older than NEW_TICKET_MAX_AGE_HOURS
  - Per-customer rate limit
  - Always-escalate sentiment check
  - Intent routing (WHERE-IS-MY-ORDER, RETURN/EXCHANGE, ORDER-CHANGE/CANCEL,
    subscription cancel, damaged/missing)
  - Everything else → escalate to AJ for now

V2 will add:
  - Claude-driven freeform responses for ambiguous cases
  - Actual Shopify refund execution (within $200 cap)
  - Skio API integration for subscription mgmt
  - Brennan/Ben supplier auto-emails

Defaults to DRY_RUN=true. Set CS_AGENT_DRY_RUN=false to actually
send/mutate. The Gorgias client respects this on every write.

Usage:
    python3 scripts/cs_agent/agent.py                    # dry-run
    CS_AGENT_DRY_RUN=false python3 scripts/cs_agent/agent.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

# Use package-relative imports if invoked as a module, fall back to plain
# imports if invoked as a script.
try:
    from .gorgias_client import (
        list_tickets_since, get_messages, send_message, add_tags,
        set_status, add_internal_note, DRY_RUN,
    )
    from .shopify_lookup import (
        customer_orders_by_email, order_by_name, find_order_number_in_text,
    )
    from . import templates as T
    from . import policy as P
    from . import state as S
except ImportError:
    from gorgias_client import (  # type: ignore[no-redef]
        list_tickets_since, get_messages, send_message, add_tags,
        set_status, add_internal_note, DRY_RUN,
    )
    from shopify_lookup import (  # type: ignore[no-redef]
        customer_orders_by_email, order_by_name, find_order_number_in_text,
    )
    import templates as T  # type: ignore[no-redef]
    import policy as P  # type: ignore[no-redef]
    import state as S  # type: ignore[no-redef]


# Tag prefixes the Phase 1 spam rules apply — these are already auto-closed,
# we never touch them again.
SPAM_TAG_PREFIX = "auto-spam-"


# ── Per-ticket processing ────────────────────────────────────────────────────

def _is_spam(ticket: dict) -> bool:
    return any((t.get("name") or "").startswith(SPAM_TAG_PREFIX)
               for t in (ticket.get("tags") or []))


def _is_always_escalate(ticket: dict) -> bool:
    tag_names = {(t.get("name") or "") for t in (ticket.get("tags") or [])}
    return bool(tag_names & P.ALWAYS_ESCALATE_TAGS)


def _is_too_old(ticket: dict) -> bool:
    created = ticket.get("created_datetime")
    if not created:
        return False
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception:
        return False
    age = datetime.now(timezone.utc) - ts
    return age > timedelta(hours=P.NEW_TICKET_MAX_AGE_HOURS)


def _customer_firstname(ticket: dict) -> Optional[str]:
    c = ticket.get("customer") or {}
    return c.get("firstname")


def _customer_email(ticket: dict) -> Optional[str]:
    c = ticket.get("customer") or {}
    return c.get("email")


def _latest_inbound_text(messages: list[dict]) -> str:
    """The customer's most recent message, plain text."""
    for m in reversed(messages):
        if not m.get("from_agent"):
            return (m.get("body_text") or "").strip()
    return ""


def _detect_intent(subject: str, body: str, tag_names: set[str]) -> str:
    """Return one of: where_is_my_order, return_exchange, order_cancel,
    subscription_cancel, damaged_or_missing, refund_request, unknown.

    Combines Gorgias's existing intent tags + content keywords. This is
    deliberately simple; Claude-driven classification is V2."""
    import re

    text = f"{subject}\n{body}".lower()

    if "WHERE-IS-MY-ORDER" in tag_names or "ORDER-STATUS" in tag_names:
        return "where_is_my_order"
    if "RETURN/EXCHANGE" in tag_names or "return-portal" in tag_names:
        return "return_exchange"
    if "ORDER-CHANGE/CANCEL" in tag_names:
        return "order_cancel"

    if re.search(r"\b(where(.?s|\sis)\s+my\s+(order|package)|tracking|haven.?t\s+received)\b", text):
        return "where_is_my_order"
    if re.search(r"\b(return|exchange)\b", text):
        return "return_exchange"
    if re.search(r"\bcancel\s+(my\s+)?subscription|unsubscribe|skio\b", text):
        return "subscription_cancel"
    if re.search(r"\bcancel\s+(my\s+)?order\b", text):
        return "order_cancel"
    if re.search(r"\b(damaged|broken|leak(ed|ing)?|missing|wrong\s+(item|product))\b", text):
        return "damaged_or_missing"
    if re.search(r"\b(refund|chargeback|money\s+back)\b", text):
        return "refund_request"

    return "unknown"


def process_ticket(ticket: dict) -> str:
    """Decide + take action on a single ticket. Returns the action label
    we recorded (for logging).

    `state` isn't threaded through V1 — per-customer rate limits + the
    daily refund running total will read/write it in V2."""
    tid = ticket["id"]
    subject = ticket.get("subject") or ""
    tag_names = {(t.get("name") or "") for t in (ticket.get("tags") or [])}
    firstname = _customer_firstname(ticket)
    email = _customer_email(ticket)

    # 1. Skip auto-spam (Phase 1 rules already closed these)
    if _is_spam(ticket):
        return "skip_spam"

    # 2. Always-escalate sentiments / manual review
    if _is_always_escalate(ticket):
        add_tags(tid, ["agent-escalated-sensitive"])
        return "escalate_sensitive"

    # 3. Skip ancient tickets (backlog cleanup is a separate pass)
    if _is_too_old(ticket):
        return "skip_too_old"

    # 4. Skip if no customer email (can't lookup orders)
    if not email:
        return "skip_no_email"

    # 5. Per-customer rate limit
    # (Skipped for V1 simplicity — Gorgias rate-counter call doubles the API
    # load. Add if we see customer reply-loops in the wild.)

    # 6. Fetch latest message body
    try:
        messages = get_messages(tid)
    except Exception as e:
        print(f"[agent] tid={tid} couldn't fetch messages: {e}")
        return "error_fetch_messages"
    body = _latest_inbound_text(messages)

    intent = _detect_intent(subject, body, tag_names)
    print(f"[agent] tid={tid} intent={intent} subject={subject[:60]!r}")

    # 7. Look up the customer's recent orders
    try:
        orders = customer_orders_by_email(email, limit=5)
    except Exception as e:
        print(f"[agent] tid={tid} order lookup failed: {e}")
        orders = []

    # 8. Try to pin to a specific order if the customer mentioned a #
    order_name_hint = find_order_number_in_text(f"{subject}\n{body}")
    target_order = None
    if order_name_hint:
        target_order = next((o for o in orders if (o.get("name") or "").lstrip("#") == order_name_hint), None)
        if not target_order:
            # Fall back to direct lookup (different email on file possible)
            try:
                fetched = order_by_name(order_name_hint)
                if fetched:
                    target_order = {
                        "name": fetched.get("name"),
                        "fulfillment_status": fetched.get("fulfillment_status"),
                        "tracking": [],
                    }
            except Exception:
                pass
    if not target_order and orders:
        target_order = orders[0]   # most recent

    # 9. Route by intent
    action_label = "unhandled"

    if intent == "where_is_my_order":
        if not target_order:
            reply = T.need_more_info(firstname, "your order number (looks like #84875)")
            send_message(tid, reply)
            add_tags(tid, ["agent-needs-info"])
            action_label = "wismo_no_order_asked_for_number"
        else:
            tracking = (target_order.get("tracking") or [None])[0] if target_order.get("tracking") else None
            reply = T.where_is_my_order(
                firstname,
                target_order.get("name") or f"#{order_name_hint or '?'}",
                (tracking or {}).get("url"),
                (tracking or {}).get("number"),
                target_order.get("fulfillment_status"),
            )
            send_message(tid, reply)
            add_internal_note(tid, f"agent: WHERE-IS-MY-ORDER auto-reply for {target_order.get('name')}")
            add_tags(tid, ["agent-auto-replied", "WHERE-IS-MY-ORDER"])
            set_status(tid, "closed")
            action_label = f"wismo_replied_with_tracking_for_{target_order.get('name')}"

    elif intent == "return_exchange":
        reply = T.return_or_exchange(firstname)
        send_message(tid, reply)
        add_internal_note(tid, "agent: RETURN/EXCHANGE → portal link, closed")
        add_tags(tid, ["agent-auto-replied", "RETURN-PORTAL-REDIRECT"])
        set_status(tid, "closed")
        action_label = "return_portal_link_sent"

    elif intent == "subscription_cancel":
        reply = T.subscription_cancel(firstname)
        send_message(tid, reply)
        add_internal_note(tid, "agent: subscription_cancel → self-serve portal link, closed")
        add_tags(tid, ["agent-auto-replied", "SUBSCRIPTION-SELF-SERVE"])
        set_status(tid, "closed")
        action_label = "subscription_self_serve_link_sent"

    elif intent == "order_cancel":
        # V1: don't actually mutate the order — draft for AJ. Real cancellation
        # (financialStatus=pending + fulfillment_status null → safe to cancel)
        # is V2 once we've watched the agent for a few days.
        add_tags(tid, ["agent-needs-aj", "ORDER-CHANGE/CANCEL"])
        action_label = "order_cancel_escalated_for_aj"

    elif intent == "damaged_or_missing":
        # Acknowledge to customer immediately + tag for warehouse handoff.
        reply = T.damaged_or_missing_acknowledgment(firstname)
        send_message(tid, reply)
        add_internal_note(tid,
            "agent: damaged_or_missing → customer acknowledged; needs warehouse/supplier handoff "
            f"(supplier email: {P.SUPPLIER_EMAIL}; warehouse email: {P.WAREHOUSE_EMAIL or 'TBD'})")
        add_tags(tid, ["agent-needs-warehouse-handoff", "DAMAGED-OR-MISSING"])
        action_label = "damaged_missing_acknowledged_pending_handoff"

    elif intent == "refund_request":
        # V1: always escalate to AJ. Refund auto-execution comes in V2 once
        # we've verified intent detection is reliable.
        add_tags(tid, ["agent-needs-aj", "REFUND-REQUEST"])
        action_label = "refund_escalated_for_aj"

    else:
        # Unknown intent. Add a tag so it surfaces in AJ's queue but don't
        # reply / close.
        add_tags(tid, ["agent-needs-aj", "UNKNOWN-INTENT"])
        action_label = "unknown_escalated_for_aj"

    return action_label


# ── Main loop ────────────────────────────────────────────────────────────────

def main() -> int:
    state = S.load()
    S.reset_daily_refund_if_new_day(state)

    # Default lookback: last hour (cron runs every 5 min so a 1h window
    # gives us a lot of catch-up margin on first runs / after outages).
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    if state.get("last_run_at"):
        try:
            last = datetime.fromisoformat(state["last_run_at"])
            # Look back 30s further to handle clock skew + Gorgias write lag.
            since = min(since, last - timedelta(seconds=30))
        except Exception:
            pass
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[agent] DRY_RUN={DRY_RUN} since={since_iso}")

    try:
        tickets = list_tickets_since(since_iso, limit=50)
    except Exception as e:
        print(f"[agent] FATAL: ticket listing failed: {e}")
        return 2

    last_processed = state.get("last_processed_ticket_id", 0)
    new_tickets = [t for t in tickets if int(t["id"]) > int(last_processed)]
    print(f"[agent] fetched {len(tickets)} tickets in window, {len(new_tickets)} are new")

    action_counts: dict[str, int] = {}
    for t in new_tickets:
        try:
            label = process_ticket(t)
        except Exception as e:
            print(f"[agent] tid={t['id']} crashed: {e}\n{traceback.format_exc()[:400]}")
            label = "error_crash"
        action_counts[label] = action_counts.get(label, 0) + 1
        S.record_action(state, int(t["id"]), label, t.get("subject", "")[:120])
        state["last_processed_ticket_id"] = max(int(t["id"]), state["last_processed_ticket_id"])

    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    S.save(state)

    print(f"[agent] action summary: {action_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
