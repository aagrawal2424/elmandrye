"""Product engine orchestrator — runs daily Mon-Fri via GitHub Actions."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from product_engine.topics import fetch_trending          # noqa: E402
from product_engine.sources.errors import NoIdeasError    # noqa: E402
from product_engine.market_research import research_topic  # noqa: E402
from product_engine.image_generator import generate_product_image  # noqa: E402
from product_engine.product_creator import create_demand_product   # noqa: E402
from product_engine.notify import send_product_live, send_hero_failure  # noqa: E402

STATE_FILE = Path(__file__).parent / "state.json"
MAX_NEW_PER_RUN = 1  # one new demand-test product per day


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"created_products": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# Suffixes that don't change the underlying ingredient identity. We strip
# these (case-insensitive) so "Anthocyanin", "Anthocyanin supplement",
# "Anthocyanin complex", "Anthocyanin extract" all dedupe to one base
# ingredient. Caused 4-copy Anthocyanin spam on 2026-05-29 → 2026-06-01.
_PRODUCT_FORM_SUFFIXES = [
    "supplement", "supplements",
    "complex", "complexes",
    "extract", "extracts",
    "powder", "powders",
    "capsules", "capsule",
    "tablets", "tablet",
    "gummies", "gummy",
    "softgels", "softgel",
    "tincture",
    "drops",
    "blend",
    "formula",
    "support",
    "premium",
    "advanced",
    "pro",
    "plus",
    "max",
]


def _ingredient_stem(name: str) -> str:
    """Reduce a product name to its core ingredient identifier.

    Strips marketing form/dosage suffixes ("supplement", "complex", etc.),
    lowercases, collapses whitespace. Used for dedup so we don't ship
    the same ingredient under five different SKU names across a week.

    Examples:
      "Anthocyanin"             → "anthocyanin"
      "Anthocyanin supplement"  → "anthocyanin"
      "Anthocyanin Complex"     → "anthocyanin"
      "Magnolia Bark Extract"   → "magnolia bark"
    """
    import re as _re
    stem = name.lower().strip()
    # Strip dosages like "500mg", "1g", "2 grams"
    stem = _re.sub(r"\b\d+(\.\d+)?\s*(mg|mcg|g|grams|iu|cc|ml)\b", "", stem)
    # Strip leading/trailing "the ", articles, generic words
    tokens = stem.split()
    cleaned: list[str] = []
    for tok in tokens:
        tok_clean = tok.strip(".,;:!?()[]{}\"'")
        if tok_clean in _PRODUCT_FORM_SUFFIXES:
            continue
        if not tok_clean:
            continue
        cleaned.append(tok_clean)
    return " ".join(cleaned).strip()


def existing_ingredient_stems(state: dict) -> set[str]:
    """Return the set of ingredient stems we've already shipped, keyed
    on the per-product reduced form."""
    return {
        _ingredient_stem(p["product_title"])
        for p in state.get("created_products", [])
        if p.get("product_title")
    }


def existing_titles(state: dict) -> set[str]:
    """Legacy interface — returns full lowercase titles. Source chain
    still wants both: the full titles (for exact-match catches) AND the
    stems (for fuzzy-match catches). We pass UNION of both upstream so
    no candidate slips through either way."""
    full = {p["product_title"].lower() for p in state.get("created_products", [])}
    stems = existing_ingredient_stems(state)
    return full | stems


def already_created(product_name: str, state: dict) -> bool:
    """True when this product (by exact title OR by ingredient stem)
    matches something we've already shipped."""
    if product_name.lower() in {p["product_title"].lower() for p in state.get("created_products", [])}:
        return True
    return _ingredient_stem(product_name) in existing_ingredient_stems(state)


def run(dry_run: bool = False) -> int:
    state = load_state()
    print(f"[main] State loaded — {len(state['created_products'])} products created so far.")

    try:
        opportunities = fetch_trending(
            limit=10, existing_titles=existing_titles(state)
        )
    except NoIdeasError as e:
        # Backstop reserve was empty AND all live sources failed —
        # mathematically near-impossible by design (the reserve alone
        # holds 200 curated entries). Fail loud, alert, return non-zero.
        print(f"[main] CRITICAL: source chain returned zero — {e}")
        _alert_no_ideas(str(e))
        return 2

    if not opportunities:
        # Source chain returned a non-empty list but main.py filtered them
        # all (every candidate already in state). Soft-exit, no alert —
        # but log so we notice if it persists.
        print("[main] All candidates already in state — nothing new to create. Exiting.")
        return 0

    created_count = 0
    for opp in opportunities:
        if created_count >= MAX_NEW_PER_RUN:
            break

        name = opp.get("product_name", "")
        if not name or already_created(name, state):
            continue

        print(f"\n[main] ── {name} | {opp.get('category')} | niche score {opp.get('niche_score')}/10 ──")
        print(f"[main] Why: {opp.get('why_interesting')}")

        try:
            research = research_topic(opp)
            print(f"[main] Research: {research['product_title']} @ ${research['luxury_price_usd']} | {research.get('form')}")
        except Exception as e:
            print(f"[main] Research failed for '{name}': {e}")
            continue

        try:
            image_url = generate_product_image(
                product_name=research["product_title"],
                descriptor=research.get("descriptor_line", "60 Capsules"),
                dry_run=dry_run,
            )
        except Exception as e:
            # Fail-loud per AJ 2026-06-02: NEVER publish a product without
            # a hero. Previous behavior set image_url="" and shipped a
            # naked product card. Now: skip the candidate, alert ops,
            # try the next opportunity in the same run so we still ship
            # something today if another candidate's image succeeds.
            print(f"[main] HERO PIPELINE FAILED — skipping {name!r}: {e}")
            try:
                send_hero_failure(name, str(e))
            except Exception as alert_err:
                print(f"[main]   (hero-failure alert send failed: {alert_err})")
            state.setdefault("skipped_no_image", []).append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "product_name": name,
                "error": str(e),
            })
            save_state(state)
            continue

        if not image_url:
            # Defense in depth: generate_product_image succeeded by code
            # path but returned an empty string. Same skip behavior.
            print(f"[main] generate_product_image returned empty url for {name!r} — skipping")
            try:
                send_hero_failure(name, "generate_product_image returned empty url (no exception)")
            except Exception:
                pass
            continue

        try:
            product = create_demand_product(research, image_url, dry_run=dry_run)
        except Exception as e:
            print(f"[main] Product creation failed: {e}")
            continue

        if not dry_run:
            send_product_live(research["product_title"], product.get("handle", ""))

        state["created_products"].append({
            "product_name": name,
            "product_title": research["product_title"],
            "category": opp.get("category"),
            "niche_score": opp.get("niche_score"),
            "shopify_product_id": product.get("id", ""),
            "handle": product.get("handle", ""),
            "image_url": image_url,
            "created_at": datetime.now(timezone.utc).date().isoformat(),
            "price_usd": research.get("luxury_price_usd"),
            "reddit_url": opp.get("reddit_url", ""),
            "why_interesting": opp.get("why_interesting", ""),
        })
        save_state(state)
        created_count += 1
        print(f"[main] Created: {research['product_title']}")

    print(f"\n[main] Done. {created_count} new product(s) this run.")
    return 0


def _alert_no_ideas(reason: str) -> None:
    """Send a Resend alert when the source chain produces zero usable
    candidates. Best-effort — never raises."""
    import urllib.request
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from get_token import load_env as _load
    env = _load()
    key = env.get("RESEND_API_KEY", "")
    if not key:
        print("[alert] no RESEND_API_KEY — skipping no-ideas alert")
        return
    body = json.dumps({
        "from": "Elm & Rye product engine <support@elmandrye.com>",
        "to": ["aj@portraitpal.ai"],
        "subject": "[product-engine] CRITICAL: source chain returned zero candidates",
        "html": (
            "<h2>Product engine: source chain produced no usable candidates</h2>"
            "<p>Every source — including the curated evergreen reserve — "
            "returned nothing this run. No new product was created.</p>"
            "<p><strong>Most likely causes:</strong></p>"
            "<ul>"
            "<li>Ahrefs key revoked or quota exhausted</li>"
            "<li>Shopify catalog fetch saturated the dedupe set (200+ reserve items already shipped)</li>"
            "<li>reserve.json missing or corrupted in this commit</li>"
            "</ul>"
            f"<p><strong>Raw diagnostic:</strong></p><pre>{reason[:2000]}</pre>"
        ),
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "elmandrye-product-engine/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"[alert] no-ideas email sent: {r.status}")
    except Exception as e:
        print(f"[alert] Resend send failed: {e}")


if __name__ == "__main__":
    dry_run = os.environ.get("PRODUCT_ENGINE_DRY_RUN", "false").lower() == "true"
    sys.exit(run(dry_run=dry_run) or 0)
