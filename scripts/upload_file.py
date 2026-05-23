#!/usr/bin/env python3
"""Upload a local file to Shopify Files (not product-scoped).

Usage: python3 scripts/upload_file.py <local_path> [alt_text]

Prints the file's CDN URL on stdout once processing completes.
Polls for up to ~30s waiting for image processing.
"""
from __future__ import annotations

import json
import mimetypes
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gql import call  # noqa: E402


def staged_upload(filename, mime, size):
    query = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets { url resourceUrl parameters { name value } }
        userErrors { field message }
      }
    }
    """
    variables = {"input": [{
        "filename": filename, "mimeType": mime, "httpMethod": "POST",
        "resource": "FILE", "fileSize": str(size),
    }]}
    r = call(query, variables)
    if r.get("errors") or r["data"]["stagedUploadsCreate"]["userErrors"]:
        raise SystemExit(f"stagedUploadsCreate failed: {json.dumps(r, indent=2)}")
    return r["data"]["stagedUploadsCreate"]["stagedTargets"][0]


def post_multipart(url, fields, file_path, mime):
    boundary = "----elmandryeFormBoundary7MA4YWxkTrZu0gW"
    body = b""
    for f in fields:
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{f["name"]}"\r\n\r\n'.encode()
        body += f["value"].encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'.encode()
    body += f"Content-Type: {mime}\r\n\r\n".encode()
    body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req) as resp:
        if resp.status not in (200, 201, 204):
            raise SystemExit(f"Upload failed: HTTP {resp.status}")


def file_create(resource_url, alt):
    query = """
    mutation fileCreate($files: [FileCreateInput!]!) {
      fileCreate(files: $files) {
        files { id alt fileStatus ... on MediaImage { image { url } } }
        userErrors { field message }
      }
    }
    """
    variables = {"files": [{"originalSource": resource_url, "alt": alt, "contentType": "IMAGE"}]}
    r = call(query, variables)
    if r.get("errors") or r["data"]["fileCreate"]["userErrors"]:
        raise SystemExit(f"fileCreate failed: {json.dumps(r, indent=2)}")
    return r["data"]["fileCreate"]["files"][0]


def wait_for_url(file_id, max_attempts=20, delay=2):
    query = """
    query($id: ID!) {
      node(id: $id) { ... on MediaImage { fileStatus image { url } } }
    }
    """
    for _ in range(max_attempts):
        r = call(query, {"id": file_id})
        node = r["data"]["node"]
        if node and node.get("image") and node["image"].get("url"):
            return node["image"]["url"]
        time.sleep(delay)
    raise SystemExit(f"Timed out waiting for file URL: {file_id}")


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    file_path = Path(sys.argv[1])
    alt = sys.argv[2] if len(sys.argv) > 2 else file_path.stem
    mime = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"
    size = file_path.stat().st_size

    target = staged_upload(file_path.name, mime, size)
    fields = [{"name": p["name"], "value": p["value"]} for p in target["parameters"]]
    post_multipart(target["url"], fields, file_path, mime)
    f = file_create(target["resourceUrl"], alt)

    url = f.get("image", {}).get("url") if f.get("image") else None
    if not url:
        url = wait_for_url(f["id"])
    print(url)


if __name__ == "__main__":
    main()
