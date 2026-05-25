"""
POST /api/waitlist
Body: { email, product_id, product_handle, product_title }

Creates a product-specific, 50% off, single-use Shopify discount code
for the demand-test product the shopper tried to buy.
Fires a Klaviyo "Demand Test Checkout Attempt" event with the code attached.
Returns: { code: "WL-XXXXXXXX" }
"""
from __future__ import annotations

import json
import os
import random
import string
import urllib.request
from http.server import BaseHTTPRequestHandler


# ── Shopify helpers ───────────────────────────────────────────────────────────

SHOPIFY_STORE   = os.environ["SHOPIFY_STORE"]          # elmandrye.myshopify.com
SHOPIFY_TOKEN   = os.environ["SHOPIFY_ACCESS_TOKEN"]
SHOPIFY_API_VER = os.environ.get("SHOPIFY_API_VERSION", "2025-01")


def _gql(query: str, variables: dict) -> dict:
    url  = f"https://{SHOPIFY_STORE}/admin/api/{SHOPIFY_API_VER}/graphql.json"
    body = json.dumps({"query": query, "variables": variables}).encode()
    req  = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


_CREATE_DISCOUNT = """
mutation discountCodeBasicCreate($basicCodeDiscount: DiscountCodeBasicInput!) {
  discountCodeBasicCreate(basicCodeDiscount: $basicCodeDiscount) {
    codeDiscountNode {
      codeDiscount {
        ... on DiscountCodeBasic {
          codes(first: 1) { nodes { code } }
        }
      }
    }
    userErrors { field message }
  }
}
"""


def _unique_code() -> str:
    chars = random.choices(string.ascii_uppercase + string.digits, k=8)
    return "WL-" + "".join(chars)


def create_shopify_code(product_id: str, code: str) -> str:
    """Create a 50% off, single-use code valid only for product_id. Returns the code."""
    result = _gql(_CREATE_DISCOUNT, {
        "basicCodeDiscount": {
            "title": f"Waitlist code {code}",
            "code": code,
            "startsAt": "2026-01-01T00:00:00Z",
            "customerGets": {
                "value": {"percentage": 0.50},
                "items": {
                    "products": {
                        "productsToAdd": [product_id],
                    }
                },
            },
            "customerSelection": {"all": True},
            "usageLimit": 1,
            "appliesOncePerCustomer": True,
        }
    })
    errors = result.get("data", {}).get("discountCodeBasicCreate", {}).get("userErrors", [])
    if errors:
        raise RuntimeError(f"Shopify discount error: {errors}")
    return code


# ── Admin email (Resend) ──────────────────────────────────────────────────────

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL    = "aj@elmandrye.com"


def notify_admin_signup(email: str, product_title: str, product_handle: str, code: str) -> None:
    """Email aj@elmandrye.com whenever someone joins a coming-soon waitlist."""
    if not RESEND_API_KEY:
        return
    html = (
        f"<h2>New waitlist signup: {product_title}</h2>"
        f"<p><strong>Customer:</strong> {email}</p>"
        f"<p><strong>Product:</strong> {product_title}</p>"
        f"<p><strong>Coupon issued:</strong> {code} (50% off, single-use)</p>"
        f"<p><a href='https://elmandrye.com/products/{product_handle}'>"
        f"View product →</a></p>"
    )
    payload = json.dumps({
        "from":    "Elm & Rye Alerts <alerts@elmandrye.com>",
        "to":      [ADMIN_EMAIL],
        "subject": f"Waitlist signup — {product_title}",
        "html":    html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type":  "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass  # non-fatal


# ── Klaviyo helper ────────────────────────────────────────────────────────────

KLAVIYO_PRIVATE_KEY = os.environ.get("KLAVIYO_PRIVATE_KEY", "")


def klaviyo_track(email: str, product_title: str, product_handle: str, code: str) -> None:
    if not KLAVIYO_PRIVATE_KEY:
        return
    payload = {
        "data": {
            "type": "event",
            "attributes": {
                "profile": {"$email": email},
                "metric": {"name": "Demand Test Checkout Attempt"},
                "properties": {
                    "Product":        product_title,
                    "Handle":         product_handle,
                    "DiscountCode":   code,
                    "DiscountAmount": "50%",
                },
            }
        }
    }
    body = json.dumps(payload).encode()
    req  = urllib.request.Request(
        "https://a.klaviyo.com/api/events/",
        data=body,
        headers={
            "Authorization":  f"Klaviyo-API-Key {KLAVIYO_PRIVATE_KEY}",
            "revision":       "2024-02-15",
            "Content-Type":   "application/json",
        },
    )
    try:
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass  # non-fatal


# ── Vercel handler ────────────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def _json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type",                 "application/json")
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # CORS preflight
        self._json(204, {})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length))
        except Exception:
            self._json(400, {"error": "Invalid JSON"}); return

        email          = (data.get("email") or "").strip()
        product_id     = (data.get("product_id") or "").strip()
        product_handle = (data.get("product_handle") or "").strip()
        product_title  = (data.get("product_title") or "").strip()

        if not email or not product_id:
            self._json(400, {"error": "email and product_id are required"}); return

        try:
            code = _unique_code()
            create_shopify_code(product_id, code)
            klaviyo_track(email, product_title, product_handle, code)
            notify_admin_signup(email, product_title, product_handle, code)
            self._json(200, {"code": code})
        except Exception as e:
            self._json(500, {"error": str(e)})
