"""Push the 12 Phase-1 spam-detection rules to Gorgias as TAG-ONLY
(no auto-close). Once we've watched them for 24-48h and confirmed
tagging accuracy, a follow-up script adds setStatus close.

All rules created here have name prefix '[Auto Tag] spam-*' so AJ can
find + bulk-disable them via UI search if anything looks wrong.

Idempotent: if a rule with the same name already exists, this script
UPDATES it (PUT) rather than creating a duplicate.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

# Import RULES from the designer so the two stay in sync.
sys.path.insert(0, str(ROOT / "scripts" / "customer_service"))
from design_spam_rules import RULES  # noqa: E402

env = load_env()
SUBDOMAIN = env["GORGIAS_SUBDOMAIN"]
AUTH = base64.b64encode(
    f"{env['GORGIAS_USERNAME']}:{env['GORGIAS_API_KEY']}".encode()
).decode()
HEADERS = {
    "Authorization": f"Basic {AUTH}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "elmandrye-cs-audit/1.0 (Python)",
}


def _admin(method: str, path: str, body: dict | None = None) -> dict:
    url = f"https://{SUBDOMAIN}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:600]
        raise RuntimeError(f"HTTP {e.code} on {method} {path}: {body}")


def _quote_for_dsl(s: str) -> str:
    """Quote a string literal for the Gorgias rule DSL.
    Single quotes wrap the value; escape internal single quotes."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def build_code(rule: dict, tag_only: bool = True) -> str:
    """Emit the Gorgias DSL code block for a rule. Always gated on
    message.from_agent == false so we don't auto-tag agent replies."""
    subj_kws = [_quote_for_dsl(k) for k in rule["subject_keywords"]]
    sender_kws = [_quote_for_dsl(k) for k in rule["sender_keywords"]]

    conditions = []
    if subj_kws:
        conditions.append(
            f"containsAny(ticket.subject, [{', '.join(subj_kws)}])"
        )
    if sender_kws:
        conditions.append(
            f"containsAny(message.sender.email, [{', '.join(sender_kws)}])"
        )
    if not conditions:
        raise ValueError(f"rule {rule['name']!r} has no conditions")

    inner = " || ".join(conditions)
    actions = [f"Action('addTags', {{ tags: '{rule['tag']}' }})"]
    if not tag_only:
        actions.append("Action('setStatus', { status: 'closed' })")

    return (
        "if (eq(message.from_agent, false)) {\n"
        f"    if ({inner}) {{\n"
        + "\n".join(f"        {a}" for a in actions) + "\n"
        "    }\n"
        "}"
    )


def find_existing(name: str, rules: list[dict]) -> dict | None:
    for r in rules:
        if r.get("name") == name:
            return r
    return None


def main(tag_only: bool = True) -> int:
    mode = "tag-only" if tag_only else "TAG + AUTO-CLOSE"
    # Pull all existing rules once for idempotency lookup.
    existing = _admin("GET", "/rules?limit=100").get("data", [])
    by_name = {r["name"]: r for r in existing}

    print(f"=== pushing {len(RULES)} Phase-1 spam rules ({mode}) ===\n")
    for r in RULES:
        rule_name = f"[Auto Tag] spam-{r['name'].replace('spam-', '')}"
        code = build_code(r, tag_only=tag_only)
        desc = (
            f"Phase-1 spam handler. Tags '{r['tag']}' on inbound tickets matching "
            f"the {r['name']} pattern."
        )
        if not tag_only:
            desc += " Also auto-closes the ticket (Phase 1B — activated after 1A tagging-accuracy review)."
        body = {
            "name": rule_name,
            "description": desc,
            "code": code,
            "event_types": "ticket-created",
            "priority": 10,
            "type": "user",
        }

        if rule_name in by_name:
            rid = by_name[rule_name]["id"]
            _admin("PUT", f"/rules/{rid}/", body)
            print(f"  UPDATED  id={rid}  {rule_name}")
        else:
            resp = _admin("POST", "/rules/", body)
            rid = resp.get("id")
            print(f"  CREATED  id={rid}  {rule_name}")

    print(f"\nAll {len(RULES)} rules pushed in {mode} mode + ACTIVE.")
    if tag_only:
        print("Next: watch Gorgias for 24-48h. Filter by tag prefix 'auto-spam-' to see hits.")
        print("When tagging looks accurate: python3 scripts/customer_service/push_spam_rules.py --add-close")
    else:
        print("Auto-close is now LIVE. New matching inbound tickets will be tagged + closed immediately.")
        print("Roll back: python3 scripts/customer_service/push_spam_rules.py  (re-runs in tag-only mode)")
    return 0


if __name__ == "__main__":
    add_close = "--add-close" in sys.argv
    raise SystemExit(main(tag_only=not add_close))
