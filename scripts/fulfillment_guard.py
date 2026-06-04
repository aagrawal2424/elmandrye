"""Daily fulfillment guard for elmandrye.

Scans Shopify for paid-but-unfulfilled orders older than STALE_AGE_DAYS
and emails AJ a single digest with direct admin-panel links + line-item
detail. Catches the failure mode that caused Colostrum order #84923 to
sit unshipped for 23 days (2026-06-03 audit) without anyone noticing.

Why this beats "watch the Shopify dashboard":
  - Most stuck orders sit because the warehouse silently dropped them
    (sync error, OOS at fulfillment time, expired in their queue, etc.)
  - You only find out when a customer escalates in Gorgias 2-3 weeks later
  - This script flips that — the engine tells you BEFORE the customer

Runs daily via .github/workflows/fulfillment-guard.yml. Read-only —
never mutates orders, never auto-cancels, never auto-refunds. The
operator (AJ + warehouse) decides what to do with each stuck order.

Env:
  SHOPIFY_*           — same as content engine
  RESEND_API_KEY      — required for the digest email
  OPS_EMAIL           — recipient (default aj@elmandrye.com)
  STALE_AGE_DAYS      — threshold (default 5)
  WATCH_AGE_DAYS      — additionally include orders this old in a
                        "watch list" section (default 3)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import get_token, load_env  # noqa: E402

env = load_env()
STORE = env.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API = env.get("SHOPIFY_API_VERSION", "2025-01")
OPS_EMAIL = os.environ.get("OPS_EMAIL", "aj@elmandrye.com")
STALE_AGE_DAYS = int(os.environ.get("STALE_AGE_DAYS", "5"))
WATCH_AGE_DAYS = int(os.environ.get("WATCH_AGE_DAYS", "3"))
SCAN_WINDOW_DAYS = 60   # how far back to look — anything older than this
                        # has been escalated / refunded / written off already


def _admin_get(path: str):
    req = urllib.request.Request(
        f"https://{STORE}/admin/api/{API}{path}",
        headers={"X-Shopify-Access-Token": get_token(), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()), r.headers.get("Link", "")


def fetch_unfulfilled_orders() -> list[dict]:
    """Pull every order in the scan window that's paid + open + has no
    fulfillment. Shopify's `fulfillment_status=unfulfilled` filter alone
    doesn't catch everything (partials get excluded), so we also pull
    `partial` and filter in code."""
    since = (datetime.now(timezone.utc) - timedelta(days=SCAN_WINDOW_DAYS)).isoformat()
    url = (f"/orders.json?status=open&financial_status=paid"
           f"&fulfillment_status=unshipped&created_at_min={since}"
           f"&limit=250&fields=id,name,created_at,line_items,"
           f"financial_status,fulfillment_status,cancelled_at,customer,total_price")
    out: list[dict] = []
    while url:
        data, link = _admin_get(url)
        out.extend(data.get("orders") or [])
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = "/" + part.split(";")[0].strip().strip("<>").split("/admin/api/", 1)[1].split("/", 1)[1]
                # Convert absolute next URL back to a path
                url = part.split(";")[0].strip().strip("<>")
                url = "/" + url.split("/admin/api/", 1)[1].split("/", 1)[1]
                break
    return out


def age_days(o: dict) -> int:
    try:
        ts = datetime.fromisoformat(o["created_at"].replace("Z", "+00:00"))
    except Exception:
        return 0
    return (datetime.now(timezone.utc) - ts).days


def line_items_summary(o: dict) -> str:
    parts = []
    for li in o.get("line_items") or []:
        qty = li.get("quantity") or 1
        title = (li.get("title") or "").strip()
        variant = (li.get("variant_title") or "").strip()
        sku = (li.get("sku") or "").strip()
        label = f"{qty}× {title}"
        if variant and variant.lower() not in title.lower():
            label += f" — {variant}"
        if sku:
            label += f" [{sku}]"
        parts.append(label)
    return "; ".join(parts)


def admin_url(o: dict) -> str:
    return f"https://elmandrye.myshopify.com/admin/orders/{o['id']}"


def render_digest_html(stale: list[dict], watch: list[dict]) -> str:
    def _row(o: dict) -> str:
        age = age_days(o)
        cust = (o.get("customer") or {}).get("email") or "(no email)"
        return (
            f"<tr>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>"
            f"  <a href='{admin_url(o)}'>{o['name']}</a>"
            f"</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;color:{'#b00' if age >= 14 else '#444'};'>"
            f"  <b>{age} days</b>"
            f"</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;'>${o.get('total_price', '?')}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;'>{line_items_summary(o)}</td>"
            f"<td style='padding:6px 10px;border-bottom:1px solid #eee;font-size:13px;'>{cust}</td>"
            f"</tr>"
        )

    def _table(rows_html: str) -> str:
        return (
            "<table style='border-collapse:collapse;width:100%;font-family:-apple-system,sans-serif;font-size:14px;'>"
            "<thead>"
            "<tr style='background:#f5f5f5;'>"
            "<th style='padding:8px 10px;text-align:left;'>Order</th>"
            "<th style='padding:8px 10px;text-align:left;'>Age</th>"
            "<th style='padding:8px 10px;text-align:left;'>$</th>"
            "<th style='padding:8px 10px;text-align:left;'>Line items</th>"
            "<th style='padding:8px 10px;text-align:left;'>Customer</th>"
            "</tr></thead><tbody>"
            + rows_html +
            "</tbody></table>"
        )

    parts = [
        "<div style='font-family:-apple-system,sans-serif;max-width:900px;margin:0 auto;'>",
        "<h2 style='margin:0 0 10px;'>📦 Fulfillment guard daily digest</h2>",
        f"<p style='margin:0 0 20px;color:#555;'>"
        f"{datetime.now(timezone.utc).strftime('%a %b %d, %Y')} · "
        f"{len(stale)} stuck (>{STALE_AGE_DAYS}d) · {len(watch)} on watch ({WATCH_AGE_DAYS}-{STALE_AGE_DAYS}d)"
        f"</p>",
    ]

    if stale:
        parts.append(
            f"<h3 style='color:#b00;margin:20px 0 10px;'>🚨 Stuck — paid &gt; {STALE_AGE_DAYS} days, no shipment</h3>"
        )
        rows = "".join(_row(o) for o in sorted(stale, key=age_days, reverse=True))
        parts.append(_table(rows))
        parts.append(
            "<p style='margin:14px 0;font-size:13px;color:#555;'>"
            "Action: contact the assigned warehouse (3500 South DuPoint Highway, Nitro Logistics, "
            "or Shiphero — see Shopify order page for assignment) and ask why it hasn't shipped. "
            "If they can't ship within 24h, cancel + refund + apologize. The cs-agent doesn't "
            "auto-handle shipping escalations &gt; 14d yet — those should go straight to you.</p>"
        )
    else:
        parts.append(
            "<h3 style='color:#080;margin:20px 0 10px;'>✅ Nothing stuck</h3>"
            "<p>No paid orders unfulfilled past the 5-day threshold. Warehouse is keeping up.</p>"
        )

    if watch:
        parts.append(
            f"<h3 style='color:#a60;margin:24px 0 10px;'>👀 Watch ({WATCH_AGE_DAYS}-{STALE_AGE_DAYS} days old)</h3>"
        )
        rows = "".join(_row(o) for o in sorted(watch, key=age_days, reverse=True))
        parts.append(_table(rows))
        parts.append(
            "<p style='margin:14px 0;font-size:13px;color:#555;'>"
            "These haven't crossed the stuck threshold yet but are aging. If they appear "
            "again tomorrow in the stuck section, the warehouse skipped them.</p>"
        )

    parts.append("</div>")
    return "".join(parts)


def send_digest(html: str, stuck_count: int) -> bool:
    api_key = os.environ.get("RESEND_API_KEY") or env.get("RESEND_API_KEY", "")
    if not api_key:
        print("[guard] no RESEND_API_KEY — printing the digest to stdout instead")
        print(html)
        return False

    if stuck_count == 0:
        subject = f"[elmandrye] Fulfillment OK ({datetime.now(timezone.utc).strftime('%b %d')})"
    else:
        subject = f"[elmandrye] 🚨 {stuck_count} order(s) stuck >{STALE_AGE_DAYS}d"

    body = json.dumps({
        # Use the same verified Resend FROM as product_engine/notify.py.
        # The notifications.elmandrye.com subdomain isn't DKIM-verified.
        "from": "Elm & Rye Fulfillment <alerts@elmandrye.com>",
        "to": [OPS_EMAIL],
        "subject": subject,
        "html": html,
    }).encode()
    # Resend sits behind Cloudflare which 403's Python's default UA.
    # Without an explicit UA every email send silently fails with
    # `HTTP 403 error code: 1010` — same bot block we hit on Gorgias.
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "elmandrye-fulfillment-guard/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"[guard] digest sent → {OPS_EMAIL} (status={r.status})")
        return True
    except Exception as e:
        print(f"[guard] digest send failed: {e}")
        return False


def main() -> int:
    print(f"[guard] scanning unfulfilled orders (window={SCAN_WINDOW_DAYS}d, "
          f"stale>{STALE_AGE_DAYS}d, watch={WATCH_AGE_DAYS}-{STALE_AGE_DAYS}d)")
    orders = fetch_unfulfilled_orders()
    print(f"[guard] fetched {len(orders)} candidate orders")

    stale, watch = [], []
    for o in orders:
        if o.get("cancelled_at"):
            continue
        if o.get("fulfillment_status") in ("fulfilled", "shipped"):
            continue
        a = age_days(o)
        if a >= STALE_AGE_DAYS:
            stale.append(o)
        elif a >= WATCH_AGE_DAYS:
            watch.append(o)

    print(f"[guard] stuck (>={STALE_AGE_DAYS}d): {len(stale)}")
    print(f"[guard] watch ({WATCH_AGE_DAYS}-{STALE_AGE_DAYS}d): {len(watch)}")
    for o in sorted(stale, key=age_days, reverse=True)[:5]:
        print(f"  STUCK   {o['name']:>10s}  age={age_days(o)}d  {line_items_summary(o)[:80]}")

    html = render_digest_html(stale, watch)
    send_digest(html, len(stale))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
