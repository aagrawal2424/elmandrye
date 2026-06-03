"""Shopify Admin lookups the CS agent needs: customer's recent orders,
tracking info, subscription status. Read-only. Mutations (refund,
cancel) live in their own module so DRY_RUN gating is harder to bypass.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import get_token, load_env  # noqa: E402

_env = load_env()
STORE = _env.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API = _env.get("SHOPIFY_API_VERSION", "2025-01")


def _admin_get(path: str) -> dict:
    req = urllib.request.Request(
        f"https://{STORE}/admin/api/{API}{path}",
        headers={
            "X-Shopify-Access-Token": get_token(),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def customer_orders_by_email(email: str, limit: int = 5) -> list[dict]:
    """Return the customer's most recent orders (any status). We don't
    rely on Shopify's customer search; we go straight to /orders with
    email filter so we don't need to resolve a customer ID first.

    Returns: [{id, name, financial_status, fulfillment_status, total_price,
               created_at, line_items: [...], tracking_numbers: [...]}]
    """
    if not email:
        return []
    # /orders.json supports `email=...` and `status=any` so we see open + closed.
    resp = _admin_get(
        f"/orders.json?email={urllib.request.quote(email)}&status=any&limit={limit}"
    )
    orders = resp.get("orders") or []
    out = []
    for o in orders:
        # Extract tracking numbers from each fulfillment
        tracking = []
        for f in (o.get("fulfillments") or []):
            for tn in (f.get("tracking_numbers") or []):
                if tn:
                    tracking.append({
                        "number": tn,
                        "url": (f.get("tracking_urls") or [None])[0],
                        "company": f.get("tracking_company"),
                    })
        out.append({
            "id": o["id"],
            "name": o.get("name"),  # like "#84875"
            "financial_status": o.get("financial_status"),
            "fulfillment_status": o.get("fulfillment_status"),
            "total_price": o.get("total_price"),
            "currency": o.get("currency"),
            "created_at": o.get("created_at"),
            "line_items": [
                {
                    "title": li.get("title"),
                    "quantity": li.get("quantity"),
                    "sku": li.get("sku"),
                    "price": li.get("price"),
                }
                for li in (o.get("line_items") or [])
            ],
            "tracking": tracking,
            "shipping_address": (o.get("shipping_address") or {}).get("city"),
            "cancelled_at": o.get("cancelled_at"),
        })
    return out


def order_by_name(name: str) -> dict | None:
    """Look up by order name like '#84875' or '84875'. Returns the full
    order dict or None."""
    norm = name.lstrip("#").strip()
    resp = _admin_get(f"/orders.json?name={urllib.request.quote(norm)}&status=any")
    orders = resp.get("orders") or []
    return orders[0] if orders else None


def find_order_number_in_text(text: str) -> str | None:
    """Cheap extractor: look for #?\\d{4,} that's likely an elmandrye order
    number. elmandrye orders look like #84875 in the audit data."""
    import re
    m = re.search(r"#?\s*(\d{4,7})\b", text)
    return m.group(1) if m else None
