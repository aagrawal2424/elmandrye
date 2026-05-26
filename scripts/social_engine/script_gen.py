"""Generate a 45-60 second product review script + social captions via Claude."""
import json
import re
import urllib.request
from . import config

SYSTEM = """You write authentic-sounding, first-person product review scripts for an AI influencer
promoting Elm & Rye supplements. The influencer is a young woman, relatable and direct.
No corporate speak. No excessive enthusiasm. Sound like a real person who genuinely uses the product."""


def generate(product: dict) -> dict:
    title = product["title"]
    desc = re.sub(r"<[^>]+>", " ", product.get("body_html", "")).strip()
    price = product["variants"][0]["price"] if product.get("variants") else "44.99"
    url = f"https://elmandrye.com/products/{product['handle']}"

    prompt = f"""Product: {title}
Price: ${price}
Description: {desc[:500]}
Product URL: {url}

Write a video review script that is 60-75 words (fits ~35-45 seconds of speech). Structure:
1. Hook (5 words max — grab attention immediately)
2. What the product does + one personal-sounding benefit
3. One thing that sets Elm & Rye apart (third-party tested, clean ingredients)
4. Soft CTA (link in bio)

Then write:
- INSTAGRAM_CAPTION: 2-3 sentences + 5 relevant hashtags. End with "🤖 AI-generated content"
- FACEBOOK_CAPTION: Same but no hashtags, slightly more detail
- TIKTOK_CAPTION: 1 punchy sentence + 5 trending hashtags. End with "#AIgenerated"
- TWITTER_CAPTION: Max 240 chars including the product URL. End with "(AI)"

Return as JSON:
{{
  "script": "...",
  "instagram_caption": "...",
  "facebook_caption": "...",
  "tiktok_caption": "...",
  "twitter_caption": "..."
}}"""

    payload = json.dumps({
        "model": "claude-sonnet-4-6",
        "max_tokens": 1000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": config.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as r:
        text = json.loads(r.read())["content"][0]["text"]

    match = re.search(r"\{[\s\S]+\}", text)
    return json.loads(match.group())
