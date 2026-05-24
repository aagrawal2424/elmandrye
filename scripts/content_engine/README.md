# Elm & Rye Content Engine

Mon/Wed/Fri content engine, hard-capped at 3 articles per rolling 7 days. Picks a supplement topic from Reddit (or an evergreen list), writes an 800–1,200 word article with Claude Sonnet 4.6, validates against an anti-AI-spam ruleset, runs an originality check vs the last 90 days, generates a hero image with DALL-E 3, and publishes live to `/blogs/news` under the byline **AJ Agrawal** with JSON-LD schema and a medical disclaimer baked in.

## File map

| File | Purpose |
|---|---|
| `system_prompt.md` | The voice/structure spec. Edit this to change tone or rules. Versioned. |
| `fetch_topics.py` | Reddit JSON scraper for `r/Nootropics`, `r/Supplements`, `r/Biohackers`, `r/HubermanLab`, `r/Longevity`. |
| `evergreen_topics.py` | ~30 durable fallback topics when Reddit is dry. |
| `internal_links.py` | Pulls live Shopify products + recent articles, scores by topic keyword overlap. |
| `generate_article.py` | Calls Anthropic API with system prompt + topic + research + link registry. |
| `generate_image.py` | DALL-E 3 → download → upload to Shopify Files → return CDN URL. |
| `validate.py` | Word count, table presence, banned phrases, section structure, internal-link count, external authoritative citation, "Our take" block. |
| `originality.py` | MinHash signature + Jaccard similarity vs prior 90 days of articles. Rejects if > 0.30. |
| `publish.py` | Markdown→HTML, injects `MedicalWebPage` + `FAQPage` JSON-LD and medical disclaimer, `articleCreate` mutation, attaches hero, publishes LIVE. |
| `main.py` | Orchestrator. Cadence cap → topic → generate → validate (retry once) → originality → image → publish → state update. |
| `state.json` | Persists past 90 days of covered topics (with originality signatures) + run log. |

## Run flow (Mon / Wed / Fri)

0. **Cadence guard.** If 3+ articles already published in the last 7 days, exit cleanly without publishing.
1. Fetch ~125 hot posts across the 5 subreddits, filter to supplement-relevant + ≥30 upvotes + ≥8 comments + posted in last 36h.
2. Pick highest-scoring undiscussed topic (or fall back to evergreen pool).
3. Pull top 8 comments on that thread for research context.
4. Build internal link registry from live Shopify products + recent blog articles.
5. Call Claude with system prompt + topic + comments + link list.
6. Validate: word count, banned phrases (expanded AI-tell list), tables, FAQ, internal links, **≥1 external authoritative citation** (PubMed/NIH/Examine/.gov/.edu), required **"Our take:" first-party block**. Retry once with feedback if invalid; skip if still invalid.
6b. **Originality.** MinHash + Jaccard against last 90 days of articles. Reject if > 0.30 similarity.
7. Generate hero image via DALL-E (safe-degrade — article publishes without it if image generation fails).
8. Publish LIVE via `articleCreate`. Body includes `MedicalWebPage` + `FAQPage` JSON-LD and the medical disclaimer block. Author = AJ Agrawal.
9. Update `state.json` (covered topic + originality signature). Bot commits it back to the repo.

## Kill switches

| Scenario | Action |
|---|---|
| Pause the engine entirely | GitHub repo → Actions → "Daily Content Engine" → ⋯ → Disable workflow. |
| Test without publishing | Run workflow manually with `dry_run: true`. Or locally: `CONTENT_ENGINE_DRY_RUN=true python scripts/content_engine/main.py` |
| Tune the voice | Edit `system_prompt.md`, commit, push. Next run uses the new prompt. |
| Avoid a topic permanently | Add a fake entry to `state.json` `covered_topics` with the title — dedup will match. |
| Force a specific evergreen | Move the desired topic to position [0] in `evergreen_topics.py` and clear that day's Reddit candidates. |

## Required GitHub secrets

Repo → Settings → Secrets and Variables → Actions. Set:

| Secret | Source |
|---|---|
| `SHOPIFY_CLIENT_ID` | From the Dev Dashboard Custom App named "Claude Code - elmandrye" |
| `SHOPIFY_CLIENT_SECRET` | Same |
| `SHOPIFY_STORE` | `elmandrye.myshopify.com` |
| `SHOPIFY_API_VERSION` | `2025-01` |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `OPENAI_API_KEY` | platform.openai.com → API Keys |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` |
| `OPENAI_IMAGE_MODEL` | `gpt-image-1` |

## Local development

```bash
cd ~/elmandrye
# Dry run — full pipeline, no publish
CONTENT_ENGINE_DRY_RUN=true python3 scripts/content_engine/main.py

# Test a single component
python3 scripts/content_engine/fetch_topics.py        # see top candidates
python3 scripts/content_engine/internal_links.py      # see picked links for sample query
python3 scripts/content_engine/generate_article.py    # generate one article (uses evergreen topic)
python3 scripts/content_engine/generate_image.py "Magnesium glycinate for sleep"
echo "$ARTICLE" | python3 scripts/content_engine/validate.py
```

## Cost estimate

- Claude Sonnet 4.6: ~$0.50–$1.00 per article (input + output tokens for system prompt + research + generation)
- gpt-image-1 medium 1536x1024: ~$0.04 per image
- **Per article: ~$0.55–$1.05**
- **Per month (3/wk × ~13 articles): ~$7–$14**

## What happens when validation fails

- First attempt fails → retry once with errors as feedback to Claude
- Second attempt fails → do NOT publish. Log the failure to `state.json.run_log`. Print errors to GitHub Actions log (visible in the Actions tab).
- No fallback to a degraded article — better to skip a day than ship something off-brand.

## What happens when Reddit is down or returns nothing

- The orchestrator falls back to `evergreen_topics.py` and picks the first unused topic.
- Once the full pool is exhausted, it cycles from the top again (de-duped against `covered_topics` over a 90-day window).
