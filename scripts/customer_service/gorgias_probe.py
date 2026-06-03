"""One-shot probe: confirm Gorgias auth + dump account-level stats so
we know what we're auditing (tag taxonomy, channel split, total volume).

Run: python3 scripts/customer_service/gorgias_probe.py
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

env = load_env()
SUBDOMAIN = env["GORGIAS_SUBDOMAIN"]
AUTH = base64.b64encode(
    f"{env['GORGIAS_USERNAME']}:{env['GORGIAS_API_KEY']}".encode()
).decode()


def _get(path: str) -> dict:
    """Gorgias sits behind Cloudflare — Python's default User-Agent gets a
    403 page. Send a proper UA + Content-Type so we look like a normal API
    client, not a generic bot."""
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
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print(f"=== Gorgias probe ({SUBDOMAIN}) ===\n")

# 1. Auth check — fetch one ticket
try:
    t = _get("/tickets?limit=1")
    total = t.get("meta", {}).get("total_count")
    print(f"AUTH OK. Total tickets in account: {total:,}" if isinstance(total, int) else f"AUTH OK. Meta: {t.get('meta')}")
except urllib.error.HTTPError as e:
    print(f"AUTH FAILED: HTTP {e.code} — {e.read().decode(errors='replace')[:300]}")
    sys.exit(2)

# 2. Tag taxonomy — what tags do you actually use?
print("\n--- Tags (top 30) ---")
try:
    tags = _get("/tags?limit=100")
    items = tags.get("data", [])
    print(f"Total tags defined: {len(items)}")
    for t in items[:30]:
        print(f"  {t.get('name')!r:40s} (id={t.get('id')}, decoration={t.get('decoration')})")
except Exception as e:
    print(f"tags fetch failed: {e}")

# 3. Channels — where are tickets coming from?
print("\n--- Channels ---")
try:
    chs = _get("/channels?limit=50")
    for c in chs.get("data", []):
        print(f"  {c.get('type'):10s} address={c.get('address') or c.get('name')!r}")
except Exception as e:
    print(f"channels fetch failed: {e}")

# 4. Users (agents) — who's currently on the team?
print("\n--- Agents ---")
try:
    users = _get("/users?limit=20")
    for u in users.get("data", []):
        if u.get("role", {}).get("name") in (None, "customer", "anonymous"):
            continue
        print(f"  {u.get('email'):35s} role={u.get('role', {}).get('name'):15s} active={u.get('active')}")
except Exception as e:
    print(f"users fetch failed: {e}")

# 5. Open ticket count by status
print("\n--- Status breakdown (current) ---")
for status in ("open", "closed"):
    try:
        r = _get(f"/tickets?status={status}&limit=1")
        n = r.get("meta", {}).get("total_count")
        print(f"  status={status}: {n:,}" if isinstance(n, int) else f"  status={status}: {n}")
    except Exception as e:
        print(f"  status={status}: failed ({e})")

print("\nDone. Ready to run the 30-day audit.")
