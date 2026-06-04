"""Pull up to 3000 most-recent Gorgias tickets + their first customer
message body so we can analyze patterns (CS improvements, revenue
opportunities, product perception).

Output to /tmp/cs_analysis/raw.jsonl — one ticket per line, ready for
the analysis pass to chunk + feed to Claude.
"""
from __future__ import annotations

import base64
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import load_env  # noqa: E402

env = load_env()
SUBDOMAIN = env["GORGIAS_SUBDOMAIN"]
AUTH = base64.b64encode(
    f"{env['GORGIAS_USERNAME']}:{env['GORGIAS_API_KEY']}".encode()
).decode()
HEADERS = {
    "Authorization": f"Basic {AUTH}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "elmandrye-cs-analysis/1.0",
}

OUT_DIR = Path("/tmp/cs_analysis")
OUT_DIR.mkdir(exist_ok=True)
OUT_FILE = OUT_DIR / "raw.jsonl"

TARGET = 3000


def _get(path: str, _retry: int = 3):
    url = f"https://{SUBDOMAIN}/api{path}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 429 and _retry > 0:
            wait = float(e.headers.get("Retry-After", 3))
            time.sleep(wait)
            return _get(path, _retry - 1)
        raise


def first_customer_message(tid: int) -> str:
    try:
        resp = _get(f"/tickets/{tid}/messages?limit=5&order_by=created_datetime:asc")
        for m in resp.get("data", []):
            if not m.get("from_agent"):
                return (m.get("body_text") or "").strip()[:2000]
        return ""
    except Exception as e:
        return f"<fetch_error: {e}>"


def main() -> int:
    print(f"=== pulling up to {TARGET} tickets + first customer message ===")
    start = time.time()
    out = OUT_FILE.open("w")
    cursor = None
    pulled = 0
    page = 0

    while pulled < TARGET:
        page += 1
        path = "/tickets?limit=100&order_by=created_datetime:desc"
        if cursor:
            path += f"&cursor={cursor}"
        resp = _get(path)
        batch = resp.get("data", [])
        if not batch:
            break

        for t in batch:
            if pulled >= TARGET:
                break
            tid = int(t["id"])
            customer = t.get("customer") or {}
            row = {
                "id": tid,
                "created": t.get("created_datetime"),
                "subject": (t.get("subject") or "")[:200],
                "status": t.get("status"),
                "channel": t.get("channel"),
                "from_agent": t.get("from_agent"),
                "customer_email": customer.get("email"),
                "customer_name": (customer.get("name") or "").strip(),
                "tags": [tag.get("name") for tag in (t.get("tags") or [])],
                "message": first_customer_message(tid),
            }
            out.write(json.dumps(row) + "\n")
            out.flush()
            pulled += 1
            if pulled % 100 == 0:
                elapsed = time.time() - start
                rate = pulled / elapsed if elapsed > 0 else 0
                eta = (TARGET - pulled) / rate if rate > 0 else float("inf")
                print(f"  {pulled}/{TARGET} pulled  ({rate:.1f}/s, eta {eta/60:.1f} min)")
            # Rate-limit gentle on Gorgias
            time.sleep(0.15)

        cursor = resp.get("meta", {}).get("next_cursor")
        if not cursor:
            print(f"  no more pages after {pulled} (account has fewer than {TARGET})")
            break

    out.close()
    elapsed = time.time() - start
    print(f"\nDone. {pulled} tickets in {elapsed:.0f}s → {OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
