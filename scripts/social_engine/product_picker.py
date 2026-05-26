"""Pick a random active Elm & Rye product with an image."""
import json
import random
import urllib.request
from . import config


def get_product() -> dict:
    req = urllib.request.Request(
        f"https://{config.SHOPIFY_STORE}/admin/api/2025-01/products.json"
        "?limit=250&status=active&vendor=Elm+%26+Rye"
        "&fields=id,title,body_html,handle,images,variants,product_type",
        headers={"X-Shopify-Access-Token": config.SHOPIFY_TOKEN},
    )
    with urllib.request.urlopen(req) as r:
        products = json.loads(r.read())["products"]

    # Filter: has image, has description, not a bundle/gift
    skip = {"bundle", "gift", "starter", "stack"}
    eligible = [
        p for p in products
        if p.get("images")
        and p.get("body_html")
        and not any(s in p["title"].lower() for s in skip)
    ]
    if not eligible:
        eligible = [p for p in products if p.get("images")]

    return random.choice(eligible)
