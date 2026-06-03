#!/usr/bin/env python3
"""One-shot PostHog deployment: substitutes the API key + host into the
template snippet under theme/snippets/posthog.liquid, then pushes both
the substituted snippet AND the layout/theme.liquid (which renders it)
to the chosen theme.

Usage:
    python3 scripts/install_posthog.py --target preview \
        --key phc_xxx --host https://us.i.posthog.com

    python3 scripts/install_posthog.py --target live \
        --key phc_xxx --host https://us.i.posthog.com

The key is the PUBLIC project API key from PostHog (starts with phc_).
It IS safe to ship to client code per PostHog docs — that's the whole
design. Don't paste a private key.

The substituted content is NOT written to disk in the repo (the source
template stays generic) — only sent to Shopify as the asset PUT body.
This means the theme-guard cron will see DRIFT for snippets/posthog.liquid
since the live version has the key but the repo version has the
placeholder. The guard will alert but we choose to live with that
(the placeholder is the source of truth, the key is environment
configuration). Workaround: add posthog.liquid to a guard skiplist
once the install is stable.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import get_token, load_env  # noqa: E402

STORE = load_env().get("SHOPIFY_STORE", "elmandrye.myshopify.com")
API = load_env().get("SHOPIFY_API_VERSION", "2025-01")
LIVE_THEME_ID = "136704000212"
PREVIEW_THEME_ID = "136464007380"

TEMPLATE_PATH = ROOT / "theme" / "snippets" / "posthog.liquid"
LAYOUT_PATH = ROOT / "theme" / "layout" / "theme.liquid"

KEY_PLACEHOLDER = "__POSTHOG_API_KEY__"
HOST_PLACEHOLDER = "__POSTHOG_HOST__"


def _admin_put(theme_id: str, asset_key: str, value: str) -> None:
    body = json.dumps({"asset": {"key": asset_key, "value": value}}).encode()
    req = urllib.request.Request(
        f"https://{STORE}/admin/api/{API}/themes/{theme_id}/assets.json",
        data=body, method="PUT",
        headers={
            "X-Shopify-Access-Token": get_token(),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    print(f"  PUT {asset_key}: ok (size={resp['asset']['size']:,} bytes)")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", choices=("live", "preview"), required=True)
    p.add_argument("--key", required=True,
                   help="PostHog public project API key (phc_...)")
    p.add_argument("--host", default="https://us.i.posthog.com",
                   help="PostHog host URL")
    args = p.parse_args(argv[1:])

    if not args.key.startswith("phc_"):
        print(f"WARNING: key {args.key[:8]}... does not look like a PostHog public "
              "project key (expected phc_ prefix). Continuing anyway.", file=sys.stderr)

    template = TEMPLATE_PATH.read_text()
    if KEY_PLACEHOLDER not in template:
        print(f"ERROR: {TEMPLATE_PATH} does not contain {KEY_PLACEHOLDER}. "
              "Check the template wasn't already substituted.", file=sys.stderr)
        return 2

    substituted = template.replace(KEY_PLACEHOLDER, args.key).replace(HOST_PLACEHOLDER, args.host)
    layout = LAYOUT_PATH.read_text()
    if "{% render 'posthog' %}" not in layout:
        print(f"ERROR: {LAYOUT_PATH} does not include the PostHog render call. "
              "Add `{% render 'posthog' %}` before </head>.", file=sys.stderr)
        return 2

    theme_id = LIVE_THEME_ID if args.target == "live" else PREVIEW_THEME_ID
    print(f"=== installing PostHog on {args.target} theme ({theme_id}) ===")
    print(f"  key: {args.key[:8]}... (length {len(args.key)})")
    print(f"  host: {args.host}")

    _admin_put(theme_id, "snippets/posthog.liquid", substituted)
    _admin_put(theme_id, "layout/theme.liquid", layout)

    print()
    if args.target == "preview":
        print(f"Preview URL: https://elmandrye.com/?preview_theme_id={theme_id}")
        print("Open in incognito + check DevTools Network for a request to "
              f"{args.host}/i/v0/e/ or /array.js. If 200 → install works.")
    else:
        print("Live install complete. Verify:")
        print("  - Visit https://elmandrye.com/ in incognito")
        print("  - DevTools Network → filter posthog → expect /array.js + /e/ (or /i/) calls")
        print("  - PostHog dashboard → Activity → fresh pageview within ~60s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
