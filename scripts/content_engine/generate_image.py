"""Generate a hero image via OpenAI's image API and upload it to Shopify Files.

Returns the Shopify CDN URL for the image, which gets attached to the
article via the articleCreate mutation's `image` field.

Prompts explicitly forbid text and faces — bad for hero images.

Model note: DALL-E 3 was retired; we use `gpt-image-1`, OpenAI's current
image model. It returns base64 PNG only (no `url`, no `response_format`,
no `style`). Landscape size is `1536x1024`.
"""
from __future__ import annotations

import base64
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402

# Use the existing Shopify file uploader
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from upload_file import staged_upload, post_multipart, file_create, wait_for_url  # noqa: E402


def build_image_prompt(article_title: str) -> str:
    return (
        f"Editorial hero image for a wellness article titled '{article_title}'. "
        "Style: clean, minimalist, soft warm pastel color palette (cream, beige, soft sage, "
        "muted ochre). Abstract botanical or supplement-adjacent imagery — think soft "
        "natural-light photography of leaves, stones, glass jars, droplets, mortar and "
        "pestle, abstract gradients. NO text, NO words, NO letters, NO faces of any people, "
        "NO recognizable brand logos. Suitable for premium supplement blog hero. "
        "16:9 horizontal aspect."
    )


def generate_image_bytes(prompt: str) -> bytes:
    """Call the OpenAI Images API (gpt-image-1) and return PNG bytes."""
    env = load_env()
    api_key = env["OPENAI_API_KEY"]
    model = env.get("OPENAI_IMAGE_MODEL", "gpt-image-1")

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",  # landscape — closest to 16:9 supported
        "quality": "medium",  # low / medium / high (gpt-image-1)
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read())

    if "error" in result:
        raise SystemExit(f"OpenAI Images error: {result['error']}")
    return base64.b64decode(result["data"][0]["b64_json"])


def save_image_bytes(data: bytes) -> Path:
    """Write PNG bytes to a tempfile and return its path."""
    tmp = Path(tempfile.mkstemp(suffix=".png", prefix="hero_")[1])
    tmp.write_bytes(data)
    return tmp


def slug_from_title(title: str) -> str:
    """Use the article title as a hint in the uploaded filename."""
    s = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')
    return s[:60]


def upload_to_shopify(local_path: Path, alt: str) -> str:
    """Reuse the existing scripts/upload_file.py flow. Returns CDN URL."""
    mime = "image/png"
    size = local_path.stat().st_size
    target = staged_upload(local_path.name, mime, size)
    fields = [{"name": p["name"], "value": p["value"]} for p in target["parameters"]]
    post_multipart(target["url"], fields, local_path, mime)
    f = file_create(target["resourceUrl"], alt)
    url = f.get("image", {}).get("url") if f.get("image") else None
    if not url:
        url = wait_for_url(f["id"])
    return url


def generate_hero(article_title: str) -> str:
    """Full pipeline: DALL-E → download → upload to Shopify → return CDN URL.
    Returns empty string on failure (safe-degrade — article still publishes)."""
    try:
        prompt = build_image_prompt(article_title)
        png_bytes = generate_image_bytes(prompt)
        local = save_image_bytes(png_bytes)
        # rename for cleaner Shopify Files listing
        renamed = local.with_name(f"blog-hero-{slug_from_title(article_title)}.png")
        local.rename(renamed)
        cdn_url = upload_to_shopify(renamed, f"Hero image: {article_title}")
        renamed.unlink(missing_ok=True)
        return cdn_url
    except Exception as e:
        print(f"[warn] hero image generation failed: {e}", file=sys.stderr)
        return ""


if __name__ == "__main__":
    title = " ".join(sys.argv[1:]) or "Magnesium glycinate for sleep — what we know"
    url = generate_hero(title)
    print(url)
