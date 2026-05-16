#!/usr/bin/env python3
"""Fetch or refresh the Shopify Admin API access token.

Caches token in .token-cache.json. Refreshes automatically when within 5 min of expiry.
Prints just the token to stdout so other scripts can capture it.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CACHE_PATH = ROOT / ".token-cache.json"
REFRESH_BUFFER = 300  # refresh if <5 min remaining


def load_env():
    env = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def fetch_token(env):
    data = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": env["SHOPIFY_CLIENT_ID"],
        "client_secret": env["SHOPIFY_CLIENT_SECRET"],
    }).encode()
    req = urllib.request.Request(
        f"https://{env['SHOPIFY_STORE']}/admin/oauth/access_token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())
    body["fetched_at"] = time.time()
    return body


def get_token() -> str:
    env = load_env()
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
        elapsed = time.time() - cache["fetched_at"]
        remaining = cache.get("expires_in", 86400) - elapsed
        if remaining > REFRESH_BUFFER:
            return cache["access_token"]
    cache = fetch_token(env)
    CACHE_PATH.write_text(json.dumps(cache))
    return cache["access_token"]


if __name__ == "__main__":
    sys.stdout.write(get_token())
