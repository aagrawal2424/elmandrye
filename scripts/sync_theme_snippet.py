#!/usr/bin/env python3
"""Sync a single theme snippet from repo source to the live or preview Shopify theme.

Usage:
    python3 scripts/sync_theme_snippet.py --target preview snippets/skio-plan-picker.liquid
    python3 scripts/sync_theme_snippet.py --target live    snippets/skio-plan-picker.liquid
    python3 scripts/sync_theme_snippet.py --check          snippets/skio-plan-picker.liquid

`--target preview` writes to the unpublished theme (id PREVIEW_THEME_ID).
`--target live` writes to the published main theme (id LIVE_THEME_ID).
`--check` compares the repo's version against the live theme — exits 0
if they match, 2 if they drift. Used by the theme-guard cron.

Banned per memory (feedback_no_live_theme_push_elmandrye):
    shopify theme push --theme 136704000212 --allow-live
Use this script instead — it's file-by-file via REST API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import get_token, load_env  # noqa: E402

STORE = load_env().get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API = load_env().get("SHOPIFY_API_VERSION", "2025-01")
LIVE_THEME_ID = "136704000212"          # [SKIO] ELM - Gamified Cart V2 (main)
PREVIEW_THEME_ID = "136464007380"       # ELM - Gamified Cart V2 (unpublished)
THEME_DIR = ROOT / "theme"


def _theme_id(target: str) -> str:
    if target == "live":
        return LIVE_THEME_ID
    if target == "preview":
        return PREVIEW_THEME_ID
    raise SystemExit(f"--target must be 'live' or 'preview', got {target!r}")


def _admin(method: str, path: str, body: dict | None = None) -> dict:
    token = get_token()
    url = f"https://{STORE}/admin/api/{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def fetch_remote(theme_id: str, asset_key: str) -> str | None:
    """Return the live content of the asset, or None if it doesn't exist."""
    try:
        # asset[key]=... query-string encoded
        path = f"/themes/{theme_id}/assets.json?asset%5Bkey%5D={urllib.request.quote(asset_key, safe='/')}"
        resp = _admin("GET", path)
        return resp.get("asset", {}).get("value")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def put_remote(theme_id: str, asset_key: str, value: str) -> None:
    _admin(
        "PUT",
        f"/themes/{theme_id}/assets.json",
        {"asset": {"key": asset_key, "value": value}},
    )


def _backup(theme_id: str, asset_key: str, remote_value: str) -> Path:
    backups_dir = ROOT / ".theme-backups"
    backups_dir.mkdir(exist_ok=True)
    import time
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    safe = asset_key.replace("/", "_")
    fp = backups_dir / f"{theme_id}.{safe}.{stamp}.bak"
    fp.write_text(remote_value)
    return fp


def cmd_push(target: str, asset_key: str) -> int:
    local = THEME_DIR / asset_key
    if not local.exists():
        print(f"ERROR: {local} does not exist in repo", file=sys.stderr)
        return 2
    new_value = local.read_text()
    theme_id = _theme_id(target)

    remote = fetch_remote(theme_id, asset_key)
    if remote is not None:
        if hashlib.sha256(remote.encode()).hexdigest() == hashlib.sha256(new_value.encode()).hexdigest():
            print(f"no-op: {asset_key} on {target} theme already matches repo")
            return 0
        bak = _backup(theme_id, asset_key, remote)
        print(f"backed up remote → {bak}")
    else:
        print(f"(remote did not exist; creating fresh on {target} theme)")

    put_remote(theme_id, asset_key, new_value)
    print(f"OK: pushed {len(new_value):,} chars to {target} theme {theme_id} / {asset_key}")
    return 0


def cmd_check(asset_key: str) -> int:
    """Live drift check: repo vs live theme. Used by the guard cron."""
    local = THEME_DIR / asset_key
    if not local.exists():
        print(f"DRIFT: {local} not in repo (cannot compare)")
        return 2
    local_value = local.read_text()
    remote_value = fetch_remote(LIVE_THEME_ID, asset_key)
    if remote_value is None:
        print(f"DRIFT: {asset_key} missing entirely on live theme")
        return 2
    if hashlib.sha256(local_value.encode()).hexdigest() != hashlib.sha256(remote_value.encode()).hexdigest():
        print(f"DRIFT: {asset_key} differs between repo and live theme")
        print(f"  repo  : sha256={hashlib.sha256(local_value.encode()).hexdigest()[:16]}... ({len(local_value):,} chars)")
        print(f"  live  : sha256={hashlib.sha256(remote_value.encode()).hexdigest()[:16]}... ({len(remote_value):,} chars)")
        return 2
    print(f"OK: {asset_key} matches between repo and live theme")
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Sync a theme snippet from repo to Shopify.")
    p.add_argument("--target", choices=("live", "preview"),
                   help="Push the local file to this theme. Mutually exclusive with --check.")
    p.add_argument("--check", action="store_true",
                   help="Compare repo file vs live theme; exit 2 on drift. For cron.")
    p.add_argument("asset_key", help="Asset key, e.g. 'snippets/skio-plan-picker.liquid'")
    args = p.parse_args(argv[1:])

    if args.check and args.target:
        p.error("--check and --target are mutually exclusive")
    if not args.check and not args.target:
        p.error("specify either --check or --target {live,preview}")

    if args.check:
        return cmd_check(args.asset_key)
    return cmd_push(args.target, args.asset_key)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
