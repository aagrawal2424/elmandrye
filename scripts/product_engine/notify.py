"""Admin email notifications for the product engine (uses Resend REST API)."""
from __future__ import annotations

import json
import os
import urllib.request

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
ADMIN_EMAIL    = "aj@elmandrye.com"


def _send(subject: str, html: str) -> None:
    if not RESEND_API_KEY:
        print("[notify] RESEND_API_KEY not set — skipping email.")
        return
    payload = json.dumps({
        "from":    "Elm & Rye Alerts <alerts@elmandrye.com>",
        "to":      [ADMIN_EMAIL],
        "subject": subject,
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
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"[notify] Email sent (HTTP {r.status}): {subject}")
    except Exception as e:
        print(f"[notify] Email failed (non-fatal): {e}")


def send_product_live(product_title: str, handle: str) -> None:
    url = f"https://elmandrye.com/products/{handle}"
    html = (
        f"<h2>New demand-test product is live 🚀</h2>"
        f"<p><strong>{product_title}</strong> was just published to the store.</p>"
        f"<p><a href='{url}'>{url}</a></p>"
        f"<p>The checkout intercept is active — signups will notify you at {ADMIN_EMAIL}.</p>"
    )
    _send(f"Product live: {product_title}", html)


def send_hero_failure(product_name: str, error: str) -> None:
    """Sent when image generation fails for a candidate product. Per AJ
    2026-06-02, products MUST ship with a hero image — never silently
    publish without one (was the legacy behavior). Engine skips this
    candidate and surfaces the failure so the operator can investigate
    the image provider (OpenAI, Replicate, Stability) without waiting
    for a customer to report a blank product card."""
    html = (
        f"<h2 style='color:#b00;'>Product engine: image generation FAILED</h2>"
        f"<p>Candidate: <strong>{product_name}</strong></p>"
        f"<p>Error:</p><pre>{error}</pre>"
        f"<p>The engine SKIPPED this candidate rather than publish a no-image "
        f"product. Investigation steps:</p>"
        f"<ol>"
        f"<li>Check OPENAI_API_KEY / REPLICATE_API_TOKEN / STABILITY_API_KEY in repo secrets</li>"
        f"<li>If transient (5xx, timeout), re-run the workflow via Actions UI</li>"
        f"<li>If consistent (provider returning 4xx), check provider dashboard for quota/billing</li>"
        f"</ol>"
    )
    _send(f"[product engine] image gen FAILED: {product_name}", html)
