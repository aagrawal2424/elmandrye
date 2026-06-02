# Theme source-of-truth files

This directory holds the parts of the elmandrye Shopify theme that we
treat as version-controlled source. Anything in here is the canonical
version that should be on the live theme (id `136704000212`).

Today's contents:
- `snippets/skio-plan-picker.liquid` — the Skio plan picker with the
  one-time-only price fallback added on 2026-06-02 (added so products
  the daily product engine creates without Skio enrollment still
  display a price). Without this fallback, no-SPG products render with
  a completely empty price slot.

## Workflow

We do NOT run `shopify theme push` (banned per `feedback_no_live_theme_push_elmandrye`
in memory — breaks the live store). Instead:

1. Edit the file in this directory.
2. Preview the change on an unpublished theme:
   `python3 scripts/sync_theme_snippet.py --target preview snippets/skio-plan-picker.liquid`
3. Visit `https://elmandrye.com/products/<handle>?preview_theme_id=<unpublished_id>`
   and confirm.
4. Promote to live:
   `python3 scripts/sync_theme_snippet.py --target live snippets/skio-plan-picker.liquid`

## Guard

`.github/workflows/theme-guard.yml` runs hourly and verifies the live
theme's copy of each tracked file matches what's in this directory. If
it drifts (e.g. someone edits via Shopify Admin Theme Editor and
overwrites our fallback), the guard alerts via Resend.
