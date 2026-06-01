# Content engine RUNBOOK

The daily cron occasionally fails. Use this guide to recover the day's publish
without breaking the engine's invariants (H1 strip, hero image, originality
dedup, engine-provenance metafield).

## ⚠️ Never manually publish via Shopify Admin

Hand-pasting markdown into Shopify Admin → Online Store → Blog Posts → Add
blog post bypasses **every** guard in `publish.py`:

- `<h1>` from raw markdown renders the title twice on the page
- No feature image attached → empty thumbnail on the blog index
- No `engine.run_id` metafield → the article-validator webhook on workflow.build
  will flag-and-unpublish it
- No JSON-LD article schema → SEO regression
- No video summary embed → quality regression
- No internal-link injection → loses traffic loop
- No originality check → can duplicate a prior post

If you see the temptation to "just paste it real quick," **stop.** Re-run the
workflow instead.

## When today's 14:00Z cron fails

Symptoms: alert email from `support@elmandrye.com` with subject
`[content-engine] <stage> failed: <topic>`.

Recovery in priority order:

### 1. Re-trigger the workflow (preferred, ~5 min)

[Open Actions → Daily Content Engine → Run workflow](https://github.com/aagrawal2424/elmandrye/actions/workflows/content-engine.yml)

- Branch: `main`
- `dry_run`: `false`
- Click **Run workflow**

This re-runs the entire pipeline on the latest commit. Auto-correct will fix
any banned phrases Claude wrote; the validator will catch real structural
issues; the hero-image provider chain will fall through OpenAI → Replicate →
Stability → Schnell. If the same root cause is still present, the same error
will come back — fix the root cause and re-run.

### 2. Manual local run (for prompt/system fixes)

```bash
cd ~/elmandrye
CONTENT_ENGINE_DRY_RUN=true python3 scripts/content_engine/main.py
```

Dry run skips the Shopify publish but exercises every other step. Use to test
prompt changes, validator rule changes, etc. The rendered HTML is logged to
stdout — never copy it into Shopify Admin.

Once you're satisfied, push your changes and trigger #1 above.

### 3. Skip today (acceptable)

A single missed publish day is not a crisis. The cron auto-runs tomorrow at
14:00Z and the missed topic stays in the evergreen pool. **One bad publish is
worse than one missed publish.**

## When today's product engine cron fails

Same logic: re-trigger via [Daily Product Engine → Run workflow](https://github.com/aagrawal2424/elmandrye/actions/workflows/product-engine.yml).

Product engine has its own source chain (Ahrefs → PubMed → curated reserve)
so single-source failures are absorbed. If the reserve runs dry → email alert
(rare; reserve has 192 entries as of 2026-06-01).

## Common failures + fixes

| Failure log line | Root cause | Fix |
|---|---|---|
| `Banned phrases found: ['—']` | Old codebase pre-em-dash unban | Pull latest; em-dash is no longer banned |
| `Validation FAILED after retry. Errors: [...]` (other phrase) | Auto-correct missed a phrase | Add the phrase + replacement to `auto_correct.py::PHRASE_FIXES` |
| `HERO PIPELINE FAILED` | All 4 image providers down | Check provider status pages; usually self-resolves; force re-run |
| `originality_failed` (Jaccard > threshold) | Topic too similar to a prior post | Add the topic to `state.json::evergreen_used` to skip it, re-run |
| `Banned phrases found: ['...']` on a NEW phrase | Auto-correct doesn't know about it yet | Edit `PHRASE_FIXES` in `auto_correct.py` to add a replacement |
| `Reddit fetch failed: HTTP 403: Blocked` | GH Actions IP rate-limited by Reddit | Expected; evergreen fallback kicks in. Not a real failure. |

## Provenance tracking

Every engine-published article carries an `engine.run_id` metafield with the
GH Actions run id (or `local-{pid}-{timestamp}` for ad-hoc runs).

To find which run published a given article:

```bash
gh api -X GET "/admin/api/2025-01/articles/<article_id>/metafields.json" \
  -H "X-Shopify-Access-Token: $SHOPIFY_ACCESS_TOKEN"
```

To find the article a given run produced, search `state.json::run_log` for the
run's timestamp.

## Things the workflow.build validator catches automatically

A separate webhook listener on workflow.build subscribes to `articles/create`
on elmandrye's blog. It unpublishes + alerts on:

- Article body starting with `<h1>` (title-twice symptom)
- No `image` field on the article
- Missing `engine.run_id` metafield (manual / off-path publish)

If your re-run produces an article that this validator unpublishes,
something is broken in `publish.py`'s guards — open an issue.

## Escalation

If after re-running 2-3 times the engine still fails on the same error, ping
@aj. Don't hand-paste.
