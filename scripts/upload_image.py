#!/usr/bin/env python3
"""Upload a local image to Shopify and attach as product media.

Usage: python3 scripts/upload_image.py <product_gid> <local_file_path> [alt_text]

Prints the new MediaImage gid on success.
"""
from __future__ import annotations

import json
import mimetypes
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gql import call  # noqa: E402


def staged_upload(filename: str, mime: str, size: int) -> dict:
    query = """
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
    variables = {
        "input": [{
            "filename": filename,
            "mimeType": mime,
            "httpMethod": "POST",
            "resource": "IMAGE",
            "fileSize": str(size),
        }]
    }
    result = call(query, variables)
    if result.get("errors") or result["data"]["stagedUploadsCreate"]["userErrors"]:
        raise SystemExit(f"stagedUploadsCreate failed: {json.dumps(result, indent=2)}")
    return result["data"]["stagedUploadsCreate"]["stagedTargets"][0]


def post_multipart(url: str, fields: list, file_path: Path, mime: str):
    boundary = "----elmandryeFormBoundary7MA4YWxkTrZu0gW"
    body = b""
    for field in fields:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{field["name"]}"\r\n\r\n'.encode()
        body += field["value"].encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201, 204):
            raise SystemExit(f"Upload failed: HTTP {resp.status} {resp.read()[:500]!r}")


def attach_to_product(product_gid: str, resource_url: str, alt: str) -> dict:
    query = """
    mutation productCreateMedia($productId: ID!, $media: [CreateMediaInput!]!) {
      productCreateMedia(productId: $productId, media: $media) {
        media {
          ... on MediaImage {
            id
            alt
            image { url }
          }
        }
        mediaUserErrors { field message }
      }
    }
    """
    variables = {
        "productId": product_gid,
        "media": [{
            "originalSource": resource_url,
            "alt": alt,
            "mediaContentType": "IMAGE",
        }],
    }
    result = call(query, variables)
    if result.get("errors") or result["data"]["productCreateMedia"]["mediaUserErrors"]:
        raise SystemExit(f"productCreateMedia failed: {json.dumps(result, indent=2)}")
    return result["data"]["productCreateMedia"]["media"][0]


def main():
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    product_gid = sys.argv[1]
    file_path = Path(sys.argv[2])
    alt = sys.argv[3] if len(sys.argv) > 3 else file_path.stem
    mime = mimetypes.guess_type(str(file_path))[0] or "image/png"
    size = file_path.stat().st_size

    target = staged_upload(file_path.name, mime, size)
    fields = [{"name": p["name"], "value": p["value"]} for p in target["parameters"]]
    post_multipart(target["url"], fields, file_path, mime)
    media = attach_to_product(product_gid, target["resourceUrl"], alt)
    print(json.dumps(media, indent=2))


if __name__ == "__main__":
    main()
