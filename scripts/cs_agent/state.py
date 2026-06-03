"""Persistent state for the CS agent: which ticket was last processed,
when we last ran, daily refund total.

Stored as a JSON file in scripts/cs_agent/state.json (gitignored). In CI,
the GHA workflow commits this back to the repo after each run so the
next run picks up where we left off.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path(__file__).resolve().parent / "state.json"

_DEFAULT = {
    "last_processed_ticket_id": 0,
    "last_run_at": None,
    "daily_refund_total_usd": 0.0,
    "daily_refund_date": None,  # YYYY-MM-DD UTC
    "ticket_actions": [],       # ring buffer of last 200 actions for audit
}


def load() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            # backfill new keys
            for k, v in _DEFAULT.items():
                data.setdefault(k, v)
            return data
        except Exception:
            pass
    return dict(_DEFAULT)


def save(state: dict) -> None:
    # Trim ticket_actions to last 200 entries
    state["ticket_actions"] = state.get("ticket_actions", [])[-200:]
    STATE_FILE.write_text(json.dumps(state, indent=2))


def reset_daily_refund_if_new_day(state: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("daily_refund_date") != today:
        state["daily_refund_date"] = today
        state["daily_refund_total_usd"] = 0.0


def record_action(state: dict, ticket_id: int, action: str, detail: str = "") -> None:
    state.setdefault("ticket_actions", []).append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "ticket_id": ticket_id,
        "action": action,
        "detail": detail[:300],
    })
