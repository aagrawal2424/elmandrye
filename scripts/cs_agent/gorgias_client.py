"""Thin Gorgias REST client. Handles the Cloudflare-friendly headers,
HTTP Basic auth, and the few operations the agent needs.

Read endpoints: list_new_tickets, get_ticket, get_messages.
Write endpoints: send_message, add_tags, set_status, assign_user.

Every write respects DRY_RUN — when DRY_RUN=true, mutating calls just
log the intended action and return a stub.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

_env = load_env()
_SUBDOMAIN = _env["GORGIAS_SUBDOMAIN"]
_AUTH = base64.b64encode(
    f"{_env['GORGIAS_USERNAME']}:{_env['GORGIAS_API_KEY']}".encode()
).decode()
_HEADERS = {
    "Authorization": f"Basic {_AUTH}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "elmandrye-cs-agent/1.0",
}

DRY_RUN = os.environ.get("CS_AGENT_DRY_RUN", "true").strip().lower() in {"1", "true", "yes"}


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _request(method: str, path: str, body: dict | None = None,
             _retry_429: int = 3) -> Any:
    url = f"https://{_SUBDOMAIN}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry_429 > 0:
            wait = float(e.headers.get("Retry-After", 2))
            time.sleep(wait)
            return _request(method, path, body, _retry_429 - 1)
        body_str = e.read().decode(errors="replace")[:400]
        raise RuntimeError(f"Gorgias HTTP {e.code} on {method} {path}: {body_str}")


# ── Reads ────────────────────────────────────────────────────────────────────

def list_tickets_since(since_iso: str, limit: int = 50) -> list[dict]:
    """Tickets created on/after since_iso. Gorgias's /tickets endpoint
    doesn't expose a server-side created_datetime__gte filter on the bare
    list view (validated against API error: 'Unknown field.'), so we pull
    DESC and stop when we hit one older than `since`. Returned list is
    ASC-sorted (oldest first) so the caller processes in arrival order."""
    from datetime import datetime
    try:
        cutoff = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except Exception:
        cutoff = None

    out: list[dict] = []
    cursor = None
    while True:
        path = f"/tickets?limit={limit}&order_by=created_datetime:desc"
        if cursor:
            path += f"&cursor={cursor}"
        resp = _request("GET", path)
        batch = resp.get("data", [])
        if not batch:
            break

        stop = False
        for t in batch:
            if cutoff:
                try:
                    ts = datetime.fromisoformat(
                        (t.get("created_datetime") or "").replace("Z", "+00:00")
                    )
                    if ts < cutoff:
                        stop = True
                        continue
                except Exception:
                    pass
            out.append(t)

        cursor = resp.get("meta", {}).get("next_cursor")
        if not cursor or stop or len(out) >= 500:
            break
        time.sleep(0.2)

    # Sort ascending so the caller processes oldest first.
    out.sort(key=lambda t: t.get("created_datetime") or "")
    return out


def get_ticket(ticket_id: int) -> dict:
    return _request("GET", f"/tickets/{ticket_id}/")


def get_messages(ticket_id: int) -> list[dict]:
    """Messages for a ticket, oldest first."""
    resp = _request("GET", f"/tickets/{ticket_id}/messages?limit=100&order_by=created_datetime:asc")
    return resp.get("data", [])


def get_recent_replies_to_customer(customer_id: int, since_iso: str) -> int:
    """Count how many agent messages we've sent to this customer in the
    window. Used for the per-customer rate limit safety rail."""
    resp = _request(
        "GET",
        f"/messages?limit=100&customer_id={customer_id}"
        f"&from_agent=true&created_datetime__gte={since_iso}",
    )
    return len(resp.get("data", []))


# ── Writes (DRY_RUN aware) ───────────────────────────────────────────────────

def _maybe(method: str, path: str, body: dict, action_label: str) -> Any:
    """Wrapped mutator. Always log the intended action. Only execute if
    not DRY_RUN."""
    summary = f"[gorgias] {action_label}: {method} {path} body={json.dumps(body)[:200]}"
    if DRY_RUN:
        print(f"[DRY_RUN] {summary}")
        return {"_dry_run": True}
    print(summary)
    return _request(method, path, body)


def send_message(ticket_id: int, body_text: str, body_html: str | None = None) -> dict:
    """Post an outbound message from the support agent on this ticket.
    Gorgias automatically delivers it to the customer via the original
    channel (email/chat/contact_form)."""
    payload = {
        "channel": "email",
        "via": "api",
        "from_agent": True,
        "body_text": body_text,
        "body_html": body_html or f"<div>{body_text.replace(chr(10), '<br>')}</div>",
        "source": {
            "type": "email",
            "to": [{"address": None}],  # Gorgias infers from ticket
        },
    }
    return _maybe("POST", f"/tickets/{ticket_id}/messages/", payload, "send_message")


def add_tags(ticket_id: int, tags: list[str]) -> dict:
    """Append tags to a ticket.

    Gorgias's PUT /tickets/{id}/ accepts a `tags` field but REPLACES the
    full tag list (verified 2026-06-04 after first live cs-agent run
    crashed with `POST /tickets/{id}/tags/` returning 400 'Unknown field:
    tags'). So we read-merge-write: fetch current tags, union with new,
    PUT the merged list. Idempotent.
    """
    # Read current — even in DRY_RUN we want to log what the resulting
    # tag set would be.
    try:
        current = _request("GET", f"/tickets/{ticket_id}/")
        existing = [tag.get("name") for tag in (current.get("tags") or []) if tag.get("name")]
    except Exception as e:
        print(f"[gorgias] add_tags: failed to read current tags for {ticket_id}: {e}")
        existing = []
    merged = list({*existing, *tags})
    return _maybe("PUT", f"/tickets/{ticket_id}/",
                  {"tags": [{"name": t} for t in merged]},
                  f"add_tags new={tags} merged={merged}")


def set_status(ticket_id: int, status: str) -> dict:
    """status ∈ {'open', 'closed'}"""
    if status not in ("open", "closed"):
        raise ValueError(f"invalid status: {status}")
    return _maybe("PUT", f"/tickets/{ticket_id}/", {"status": status}, f"set_status={status}")


def assign_user(ticket_id: int, user_id: int) -> dict:
    return _maybe("PUT", f"/tickets/{ticket_id}/",
                  {"assignee_user": {"id": user_id}}, f"assign_user={user_id}")


def add_internal_note(ticket_id: int, body_text: str) -> dict:
    """Internal-only message, customer doesn't see it. Use to log the
    agent's reasoning ('matched WHERE-IS-MY-ORDER intent, sent tracking
    link for order #12345')."""
    payload = {
        "channel": "internal-note",
        "via": "api",
        "from_agent": True,
        "body_text": body_text,
        "body_html": f"<div>{body_text}</div>",
        "source": {"type": "internal-note"},
    }
    return _maybe("POST", f"/tickets/{ticket_id}/messages/", payload,
                  "internal_note")
