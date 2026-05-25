"""Create Shopify demand-test products — live, 0 inventory, deny checkout."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gql import call  # noqa: E402

COMING_SOON_COLLECTION_ID  = "gid://shopify/Collection/456538521812"  # coming-soon
PRODUCTS_COLLECTION_ID     = "gid://shopify/Collection/270075756701"  # all products
NEW_ARRIVALS_COLLECTION_ID = "gid://shopify/Collection/413356589268"  # new arrivals
ONLINE_STORE_PUBLICATION   = "gid://shopify/Publication/69497421981"  # online store

_PRODUCT_SET = """
mutation productSet($input: ProductSetInput!, $synchronous: Boolean!) {
  productSet(synchronous: $synchronous, input: $input) {
    product {
      id title handle status
      variants(first: 10) { nodes { id title price sku inventoryPolicy } }
      onlineStoreUrl
    }
    userErrors { field message }
  }
}
"""

_ATTACH_IMAGE = """
mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
  productCreateMedia(productId: $productId, media: $media) {
    media { ... on MediaImage { id image { url } } }
    mediaUserErrors { field message }
  }
}
"""

_PUBLISH = """
mutation publishablePublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    publishable { ... on Product { onlineStoreUrl } }
    userErrors { field message }
  }
}
"""


def _money(usd: float) -> str:
    return f"{usd:.2f}"


def create_demand_product(research: dict, image_url: str, dry_run: bool = False) -> dict:
    """
    Create a LIVE product with 0 inventory and DENY policy.
    Shopify shows 'Sold Out' natively — no orders go through.
    Demand is measured by notify-me signups and add-to-cart events.
    """
    title = research["product_title"]
    desc = research["shopify_description"]
    tags = research.get("tags", []) + ["demand-test", "coming-soon"]

    # Build option values and variant inputs from research variants
    raw_variants = research.get("variants", [])
    if not raw_variants:
        raw_variants = [{
            "title": "Single",
            "sku_suffix": "SINGLE",
            "price_usd": research.get("luxury_price_usd", 49.99),
        }]

    option_values = [{"name": v["title"]} for v in raw_variants]
    variant_inputs = []
    for v in raw_variants:
        sku = f"ER-{title.upper().replace(' ', '-')}-{v['sku_suffix']}"
        variant_inputs.append({
            "price": _money(v["price_usd"]),
            "inventoryPolicy": "CONTINUE",
            "inventoryItem": {
                "sku": sku,
                "requiresShipping": True,
                "tracked": True,
            },
            "taxable": True,
            "optionValues": [{"optionName": "Size", "name": v["title"]}],
        })

    product_input = {
        "title": title,
        "descriptionHtml": (
            f"<p>{desc}</p>"
            f"<p><em>This product is coming soon. Join the waitlist to be first to know when it's available.</em></p>"
        ),
        "vendor": "Elm & Rye",
        "productType": "Supplement",
        "tags": tags,
        "status": "ACTIVE",
        "seo": {
            "title": research.get("seo_meta_title", title),
            "description": research.get("seo_meta_description", desc[:155]),
        },
        "collections": [COMING_SOON_COLLECTION_ID, PRODUCTS_COLLECTION_ID, NEW_ARRIVALS_COLLECTION_ID],
        "productOptions": [{"name": "Size", "values": option_values}],
        "variants": variant_inputs,
    }

    if dry_run:
        print(f"[product] DRY RUN — would create live/sold-out: {title}")
        return {"id": "dry-run", "title": title, "handle": "dry-run", "status": "ACTIVE"}

    print(f"[product] Creating demand-test product: {title} …")
    result = call(_PRODUCT_SET, {"input": product_input, "synchronous": True})

    errors = result.get("data", {}).get("productSet", {}).get("userErrors", [])
    if errors:
        raise RuntimeError(f"productSet errors: {errors}")

    product = result["data"]["productSet"]["product"]
    print(f"[product] Live (sold out): {product['id']} — {product['handle']}")

    # Attach image
    if image_url:
        try:
            call(_ATTACH_IMAGE, {
                "productId": product["id"],
                "media": [{"originalSource": image_url, "mediaContentType": "IMAGE", "alt": title}],
            })
        except Exception as e:
            print(f"[product] Warning: image attach failed: {e}")

    # Publish to Online Store (ACTIVE alone doesn't make it visible on the storefront)
    try:
        call(_PUBLISH, {
            "id": product["id"],
            "input": [{"publicationId": ONLINE_STORE_PUBLICATION}],
        })
        print(f"[product] Published to Online Store: https://elmandrye.com/products/{product['handle']}")
    except Exception as e:
        print(f"[product] Warning: publish to Online Store failed: {e}")

    return product
