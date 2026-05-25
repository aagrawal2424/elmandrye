"""Generate product images via OpenAI with label baked into the bottle."""
from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402

# ── Prompt ──────────────────────────────────────────────────────────────────

_PROMPT_TMPL = """\
Studio product photograph of a premium supplement bottle. \
Tall, sleek, matte black rectangular bottle positioned dead-center of frame \
on raw grey concrete stone slabs arranged at geometric angles. \
Shot from 15 degrees above horizontal. Hard directional lighting from upper left \
creating deep dramatic shadows. Monochromatic palette: black, charcoal, dark grey, \
off-white only. Zero color. Ultra minimalist editorial style — Saint Felix or Aesop \
campaign aesthetic. Square 1:1 composition. Ultra high resolution, commercial product photography.

The bottle has a printed label on its front face with the following text \
exactly as written, centered on the bottle surface:

Line 1 (large, bold, wide letter-spacing, white): {brand}
Line 2 (medium weight, wide letter-spacing, white): {product_name}
Line 3 (small, light weight, white): {descriptor}

The text must look screen-printed or embossed directly onto the matte black bottle \
surface — part of the physical label, not floating. Clean sans-serif typeface. \
All caps. The label text should be clearly legible and centered on the bottle face.\
"""


def _build_prompt(brand: str, product_name: str, descriptor: str) -> str:
    return _PROMPT_TMPL.format(
        brand=brand.upper(),
        product_name=product_name.upper(),
        descriptor=descriptor.upper(),
    )


def _call_openai_image(prompt: str, env: dict) -> bytes:
    body = json.dumps({
        "model": env.get("OPENAI_IMAGE_MODEL", "gpt-image-1"),
        "prompt": prompt,
        "n": 1,
        "size": "1024x1024",
        "output_format": "png",
        "quality": "high",
    }).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations",
        data=body,
        headers={
            "Authorization": f"Bearer {env['OPENAI_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read())

    item = data["data"][0]
    if "b64_json" in item:
        return base64.b64decode(item["b64_json"])
    with urllib.request.urlopen(item["url"], timeout=30) as r:
        return r.read()


# ── Upload to Shopify ─────────────────────────────────────────────────────────

_STAGE_MUTATION = """
mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets {
      url
      resourceUrl
      parameters { name value }
    }
    userErrors { field message }
  }
}
"""


def _shopify_upload(img_bytes: bytes, filename: str, env: dict) -> str:
    """Upload PNG bytes to Shopify via GCS PUT, return resourceUrl for productCreateMedia."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from gql import call  # noqa: E402

    stage = call(_STAGE_MUTATION, {"input": [{
        "filename": filename,
        "mimeType": "image/png",
        "resource": "FILE",
        "fileSize": str(len(img_bytes)),
    }]})
    target = stage["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    upload_url = target["url"]
    resource_url = target["resourceUrl"]

    put_req = urllib.request.Request(
        upload_url, data=img_bytes,
        headers={"Content-Type": "image/png"},
        method="PUT",
    )
    urllib.request.urlopen(put_req, timeout=60)
    return resource_url


# ── Public entry point ────────────────────────────────────────────────────────

def generate_product_image(
    product_name: str,
    descriptor: str = "60 capsules · 500 mg",
    brand: str = "ELM & RYE",
    dry_run: bool = False,
) -> str:
    """Return a Shopify resourceUrl for the generated product image."""
    env = load_env()

    print(f"[image] Generating product photo for '{product_name}' …")
    if dry_run:
        print("[image] DRY RUN — skipping OpenAI call.")
        return ""

    prompt = _build_prompt(brand, product_name, descriptor)
    img_bytes = _call_openai_image(prompt, env)
    print(f"[image] Generated ({len(img_bytes):,} bytes). Uploading to Shopify …")

    slug = product_name.lower().replace(" ", "-")
    resource_url = _shopify_upload(img_bytes, f"er-product-{slug}.png", env)
    print(f"[image] Uploaded: {resource_url[:80]}…")
    return resource_url
