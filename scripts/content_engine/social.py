"""Social distribution for published Elm & Rye articles.

Posts to Twitter/X via the v2 API using OAuth 1.0a (HMAC-SHA1), implemented
with stdlib only (urllib, hmac, hashlib) — no third-party dependencies.

Credentials are read from the project .env file via get_token.load_env().
Required keys: TWITTER_API_KEY, TWITTER_API_SECRET,
               TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET.

Design contract:
  - social failure MUST NOT block the publish pipeline.
  - Every exported function swallows exceptions and returns/prints a result.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — same pattern as the other modules in this directory
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # exposes scripts/get_token

import get_token as _get_token_module  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_TWITTER_V2_TWEET_URL = "https://api.twitter.com/2/tweets"
_TITLE_MAX_CHARS = 200


# ---------------------------------------------------------------------------
# OAuth 1.0a helpers
# ---------------------------------------------------------------------------

def _nonce() -> str:
    """Return a 32-character hex nonce from os.urandom."""
    return os.urandom(16).hex()


def _percent_encode(s: str) -> str:
    """RFC 3986 percent-encoding as required by OAuth 1.0a."""
    return urllib.parse.quote(s, safe="")


def _build_auth_header(
    method: str,
    url: str,
    oauth_params: dict[str, str],
    consumer_secret: str,
    token_secret: str,
) -> str:
    """Build an OAuth 1.0a Authorization header value using HMAC-SHA1.

    Args:
        method:          HTTP method in uppercase (e.g. "POST").
        url:             Request URL without query string.
        oauth_params:    OAuth protocol parameters (no oauth_signature yet).
        consumer_secret: TWITTER_API_SECRET.
        token_secret:    TWITTER_ACCESS_SECRET.

    Returns:
        Full Authorization header value string starting with 'OAuth '.
    """
    # Collect parameters for the signature base string.
    # For Twitter API v2 tweet creation the body is JSON, so no body params
    # are included — only the OAuth header params contribute to the base string.
    param_pairs = sorted(
        (_percent_encode(k), _percent_encode(v))
        for k, v in oauth_params.items()
    )
    params_string = "&".join(f"{k}={v}" for k, v in param_pairs)

    # Signature base string: METHOD&url&params
    base_string = "&".join([
        method.upper(),
        _percent_encode(url),
        _percent_encode(params_string),
    ])

    # Signing key: consumer_secret&token_secret (both percent-encoded)
    signing_key = (
        _percent_encode(consumer_secret) + "&" + _percent_encode(token_secret)
    ).encode("utf-8")

    raw_signature = hmac.new(
        signing_key,
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(raw_signature).decode("utf-8")

    # Build the Authorization header
    all_params = {**oauth_params, "oauth_signature": signature}
    header_parts = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(all_params.items())
    )
    return f"OAuth {header_parts}"


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _load_credentials() -> dict[str, str] | None:
    """Return Twitter credentials dict or None if any key is missing/empty."""
    try:
        env = _get_token_module.load_env()
    except Exception as exc:
        print(f"[social] Could not load .env: {exc}")
        return None

    required = (
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_SECRET",
    )
    creds = {k: env.get(k, "").strip() for k in required}
    if any(not v for v in creds.values()):
        return None
    return creds


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_to_twitter(title: str, url: str) -> bool:
    """Post a tweet linking to the published article.

    Tweet text: "{title}\\n\\n{url}" with title truncated to 200 characters.

    Args:
        title: Article title.
        url:   Live article URL.

    Returns:
        True on HTTP 201 success, False otherwise.
        Never raises — social failure must not block publish.
    """
    try:
        creds = _load_credentials()
        if creds is None:
            print("[social] Twitter credentials not configured, skipping.")
            return False

        truncated_title = title[:_TITLE_MAX_CHARS]
        tweet_text = f"{truncated_title}\n\n{url}"

        oauth_params = {
            "oauth_consumer_key": creds["TWITTER_API_KEY"],
            "oauth_token": creds["TWITTER_ACCESS_TOKEN"],
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp": str(int(time.time())),
            "oauth_nonce": _nonce(),
            "oauth_version": "1.0",
        }

        auth_header = _build_auth_header(
            method="POST",
            url=_TWITTER_V2_TWEET_URL,
            oauth_params=oauth_params,
            consumer_secret=creds["TWITTER_API_SECRET"],
            token_secret=creds["TWITTER_ACCESS_SECRET"],
        )

        body = json.dumps({"text": tweet_text}).encode("utf-8")
        req = urllib.request.Request(
            _TWITTER_V2_TWEET_URL,
            data=body,
            headers={
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "User-Agent": "ElmAndRye-ContentEngine/1.0",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                response_body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as http_err:
            status = http_err.code
            response_body = http_err.read().decode("utf-8", errors="replace")

        if status == 201:
            try:
                data = json.loads(response_body)
                tweet_id = data.get("data", {}).get("id", "<unknown>")
            except (json.JSONDecodeError, KeyError):
                tweet_id = "<unknown>"
            print(f"[social] Tweet posted successfully. id={tweet_id}")
            return True

        print(
            f"[social] WARNING: Twitter API returned HTTP {status}. "
            f"Response: {response_body[:300]}"
        )
        return False

    except Exception as exc:  # noqa: BLE001
        print(f"[social] WARNING: Unexpected error posting to Twitter: {exc}")
        return False


def distribute(article: dict, md: str) -> None:  # noqa: ARG001
    """Distribute a published article to all configured social channels.

    Currently posts to Twitter/X. Silently swallows all exceptions so that
    social failure never blocks or terminates the publish pipeline.

    Args:
        article: The article dict returned by publish.publish_article().
                 Must contain "title" and "live_url" keys.
        md:      Raw article markdown (reserved for future channels that need
                 it, e.g. LinkedIn excerpt generation).
    """
    try:
        title = article.get("title", "")
        live_url = article.get("live_url", "")

        if not title or not live_url:
            print("[social] WARNING: article dict missing title or live_url — skipping distribute.")
            return

        success = post_to_twitter(title, live_url)
        if success:
            print(f"[social] Distributed: {live_url}")
        else:
            print(f"[social] Distribution skipped or failed for: {live_url}")

    except Exception as exc:  # noqa: BLE001
        print(f"[social] WARNING: distribute() raised unexpectedly: {exc}")
