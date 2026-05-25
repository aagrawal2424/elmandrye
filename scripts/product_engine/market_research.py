"""Research competitor pricing and generate product spec via Claude.

Generates products that match the exact Elm & Rye branding conventions:
- Vendor: Elm & Rye
- Variants: {Form} / {Subtype if applicable} / Single, 2-Pack, 4-Pack
- Pricing: ~$69-99 single, scaled for packs
- Description: third-person, specific mechanism, press-mention style
- Copy voice: evidence-based, no hype, no em dashes
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402

_SYSTEM = """\
You are a product strategist for Elm & Rye, a premium supplement, skincare, \
and cleaning brand. You answer ONLY in valid JSON — no prose, no markdown fences.\
"""

_USER_TMPL = """\
Create a product spec for Elm & Rye to demand-test this emerging ingredient:

PRODUCT NAME: {product_name}
INGREDIENT: {ingredient}
CATEGORY: {category}
WHY INTERESTING: {why_interesting}

EXISTING ELM & RYE PRODUCTS FOR BRANDING REFERENCE:
{existing_products}

BRANDING RULES — follow exactly:
1. Vendor is always "Elm & Rye"
2. Variant structure depends on product type:
   - Capsules/softgels: ["Capsules / Single", "Capsules / 2-Pack", "Capsules / 4-Pack"]
   - Powders: ["Powder / [Flavor] / Single", "Powder / [Flavor] / 2-Pack", "Powder / [Flavor] / 4-Pack"]
   - Gummies: ["Gummies / Single", "Gummies / 2-Pack", "Gummies / 4-Pack"]
   - Topical (skincare): ["[Form e.g. Serum] / Single", "[Form] / 2-Pack"]
   - Liquid (tincture/drops): ["Drops / Single", "Drops / 2-Pack"]
   - Cleaning: ["Single Bottle", "3-Pack", "6-Pack"]
3. Pricing: Single ~$69-99, 2-Pack ~$129-159, 4-Pack ~$199-249 (supplements/skincare)
   Cleaning: Single ~$14-24, 3-Pack ~$34-44, 6-Pack ~$59-69
4. Description MUST start with "Description: " and be third-person, 2-3 sentences.
   Specific mechanism and dosage. May reference third-party recognition if honest.
   Never use em dashes (—). No hype words.
5. Product title: clean, 2-5 words, sentence case matching existing catalog style

Return this exact JSON structure:
{{
  "product_title": "string",
  "vendor": "Elm & Rye",
  "shopify_description": "Description: [third-person, mechanism-specific, 2-3 sentences. No em dashes.]",
  "luxury_price_usd": <single unit price as number>,
  "variants": [
    {{"title": "exact variant title string", "sku_suffix": "SINGLE", "price_usd": <number>}},
    {{"title": "exact variant title string", "sku_suffix": "2PK", "price_usd": <number>}},
    {{"title": "exact variant title string", "sku_suffix": "4PK", "price_usd": <number>}}
  ],
  "form": "Capsules|Powder|Gummies|Serum|Drops|Spray|etc",
  "dose_per_serving": "e.g. 500 mg or 2 capsules",
  "descriptor_line": "e.g. '60 Capsules · 500 mg' for bottle label",
  "seo_meta_title": "under 60 chars",
  "seo_meta_description": "under 155 chars, no hype",
  "tags": ["supplement|skincare|cleaning", "<ingredient-slug>", "<benefit-slug>"],
  "competitor_price_range": "$XX–$XX",
  "why_we_win": "one sentence on Elm & Rye's angle vs competitors"
}}
"""

_EXISTING_PRODUCT_SUMMARY = """
- Libido (Capsules / Male / Single $69.99, 2-Pack $109.99, 4-Pack $129.99)
- Collagen Protein Powder (Powder / Chocolate / Single $69.99, 2-Pack $109.99, 4-Pack $134.99)
- Testosterone Support (Capsules / Single $69.99, 2-Pack $109.99, 4-Pack $129.99)
- Colostrum (Capsules / Single $84.99, 2-Pack ~$149.99, 4-Pack ~$219.99)
- Creatine (Capsules / Single $84.99, 2-Pack $129.99, 4-Pack $159.99)
- Sea Moss (Capsules / Single $52.20 avg)
- Pre-Workout (Powder / Strawberry / Single $99.99, 2-Pack $149.99, 4-Pack $159.99)
- Mushroom Complex (Capsules / Single ~$44.76 avg)
"""


def research_topic(opportunity: dict) -> dict:
    """Takes an opportunity dict from topics.py, returns a full product spec."""
    env = load_env()

    prompt = _USER_TMPL.format(
        product_name=opportunity.get("product_name", ""),
        ingredient=opportunity.get("ingredient", ""),
        category=opportunity.get("category", "supplements"),
        why_interesting=opportunity.get("why_interesting", ""),
        existing_products=_EXISTING_PRODUCT_SUMMARY,
    )

    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1024,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": env["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())

    raw = data["content"][0]["text"].strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
