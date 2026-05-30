#!/usr/bin/env python3
"""Fetch or refresh the Shopify Admin API access token.

Caches token in .token-cache.json. Refreshes automatically when within 5 min of expiry.
Prints just the token to stdout so other scripts can capture it.
"""
from __future__ import annotations

import json
import os
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
    """Merge .env file with os.environ — os.environ wins for any key set.

    This lets the HTTP service (service/api.py) overlay per-request creds
    (Shopify store, brand config) on top of the baseline file-based env
    without modifying any downstream engine module."""
    env = {}
    # Baseline from .env file if present (CI / local dev / single-tenant cron).
    # In the Fly container the .env doesn't exist; that's fine, fall straight
    # through to os.environ-only.
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # Anything set in os.environ wins — covers per-request HTTP overrides
    # and Fly secret injections.
    for k, v in os.environ.items():
        env[k] = v
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
    """Return the Shopify Admin API access token.

    Multi-tenant override: if SHOPIFY_ACCESS_TOKEN_OVERRIDE is set in the
    environment, return it directly. The HTTP service (service/api.py)
    uses this to inject the merchant's offline token from the request
    body without going through the OAuth client_credentials flow.

    Single-tenant fallback (the original elmandrye behavior): use
    SHOPIFY_CLIENT_ID/SECRET to fetch+cache a fresh token."""
    env = load_env()
    override = env.get("SHOPIFY_ACCESS_TOKEN_OVERRIDE")
    if override:
        return override
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
