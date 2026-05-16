#!/usr/bin/env python3
"""Make a Shopify Admin GraphQL API call.

Usage:
    echo '{ shop { name } }' | python3 scripts/gql.py
    python3 scripts/gql.py < query.graphql
    python3 scripts/gql.py query.graphql [variables.json]
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from get_token import get_token, load_env  # noqa: E402


def call(query: str, variables: Optional[dict] = None) -> dict:
    env = load_env()
    token = get_token()
    url = f"https://{env['SHOPIFY_STORE']}/admin/api/{env['SHOPIFY_API_VERSION']}/graphql.json"
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    if len(sys.argv) > 1:
        query = Path(sys.argv[1]).read_text()
        variables = json.loads(Path(sys.argv[2]).read_text()) if len(sys.argv) > 2 else None
    else:
        query = sys.stdin.read()
        variables = None
    result = call(query, variables)
    json.dump(result, sys.stdout, indent=2)
    print()
    if "errors" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
