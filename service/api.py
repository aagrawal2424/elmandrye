"""HTTP service wrapping the content engine for multi-tenant use.

Single endpoint: POST /generate-article
  Accepts a per-shop config in the request body, runs the engine pipeline
  with that config overlaid on top of the baseline env, returns the
  resulting article URL + image URL + status.

The engine itself (scripts/content_engine/*) is unchanged — this is a
thin orchestration layer. Per-request config is injected into os.environ
for the duration of the call, then reverted, so the existing engine code
(which reads env via get_token.load_env) sees the right credentials and
brand voice without code changes.

For now this lives in the elmandrye repo alongside the engine it wraps.
When workflow.build customer count grows past a few, we'd extract this
service into its own repo and stop assuming the engine source ships
with it.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Request
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "content_engine"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("content-engine-service")

app = FastAPI(
    title="Elm & Rye Content Engine",
    description="Multi-tenant content generation as HTTP service",
    version="0.1.0",
)


# ── Request / response models ─────────────────────────────────────────────────


class BrandConfig(BaseModel):
    """Per-shop brand voice + targeting overrides. All optional — engine has
    elmandrye defaults baked in for any field omitted."""

    brand_name: Optional[str] = Field(None, description="e.g. 'Acme Wellness'")
    voice_tone: Optional[str] = Field(None, description="e.g. 'casual, first-person, science-skeptical'")
    persona_bio: Optional[str] = Field(None, description="Author persona — 1-2 sentences")
    persona_name: Optional[str] = Field(None, description="Author name shown on the byline")
    product_categories: Optional[list[str]] = Field(None, description="e.g. ['supplements','skincare']")
    target_audience: Optional[str] = Field(None, description="e.g. 'women 25-45 interested in longevity'")
    banned_phrases: Optional[list[str]] = Field(None, description="Phrases the article must never use")


class GenerateArticleRequest(BaseModel):
    """One full content-engine run for a single shop."""

    shop_domain: str = Field(..., description="e.g. 'acme-wellness.myshopify.com'")
    shop_access_token: str = Field(..., description="Shopify Admin API offline token")
    api_version: str = Field("2025-01", description="Shopify Admin API version")
    blog_id: Optional[str] = Field(None, description="Numeric blog ID; if omitted, engine uses its first active blog")
    brand: BrandConfig = Field(default_factory=BrandConfig)
    dry_run: bool = Field(False, description="If true, run pipeline but skip Shopify publish step")


class GenerateArticleResponse(BaseModel):
    request_id: str
    status: str  # "published" | "dry_run" | "skipped" | "failed"
    title: Optional[str] = None
    article_url: Optional[str] = None
    article_id: Optional[str] = None
    hero_image_url: Optional[str] = None
    elapsed_ms: int
    error: Optional[str] = None
    logs_tail: Optional[list[str]] = None


# ── Helpers ───────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def env_overlay(overrides: dict[str, str]):
    """Temporarily set/override env vars for the duration of the with block.
    Restores prior values (or unsets if previously absent) on exit."""
    sentinel = object()
    prior: dict[str, object] = {}
    for k, v in overrides.items():
        prior[k] = os.environ.get(k, sentinel)
        os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prior.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v  # type: ignore[assignment]


def _shop_state_path(shop_domain: str) -> Path:
    """Per-shop state.json under /tmp/engine-state/<shop>/. Each shop gets
    its own dedup history. Persists across requests within one Fly machine;
    Fly machines may be reaped, in which case state resets (acceptable
    because Shopify-side article-handle dedup catches re-publishes)."""
    safe = "".join(c if c.isalnum() or c in "-." else "_" for c in shop_domain)
    base = Path(os.environ.get("ENGINE_STATE_DIR", "/tmp/engine-state")) / safe
    base.mkdir(parents=True, exist_ok=True)
    return base / "state.json"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    """Liveness probe used by Fly + Vercel/Inngest pre-flight."""
    return {"status": "ok", "service": "content-engine", "version": "0.1.0"}


@app.post("/generate-article", response_model=GenerateArticleResponse)
def generate_article(
    req: GenerateArticleRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """Run the content engine pipeline once for the requested shop.

    Auth: `Authorization: Bearer <CONTENT_ENGINE_SERVICE_TOKEN>` matching
    the env var. This is a shared secret between workflow.build and this
    service — it is NOT the merchant's Shopify token (that's in the body)."""

    request_id = uuid.uuid4().hex[:12]
    t0 = time.time()

    # ── Auth: shared service token (workflow.build ↔ this service) ────────
    expected_token = os.environ.get("CONTENT_ENGINE_SERVICE_TOKEN", "")
    if not expected_token:
        raise HTTPException(500, "service misconfigured: CONTENT_ENGINE_SERVICE_TOKEN unset")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing Bearer token")
    presented = authorization.removeprefix("Bearer ").strip()
    if presented != expected_token:
        raise HTTPException(401, "invalid service token")

    # ── Build env overlay: per-shop Shopify creds + brand overrides ───────
    overlay = {
        "SHOPIFY_STORE": req.shop_domain,
        "SHOPIFY_ACCESS_TOKEN_OVERRIDE": req.shop_access_token,
        "SHOPIFY_API_VERSION": req.api_version,
        "CONTENT_ENGINE_DRY_RUN": "true" if req.dry_run else "false",
        "CONTENT_ENGINE_REQUEST_ID": request_id,
    }
    if req.blog_id:
        overlay["SHOPIFY_BLOG_ID"] = req.blog_id
    if req.brand.brand_name:
        overlay["BRAND_NAME"] = req.brand.brand_name
    if req.brand.voice_tone:
        overlay["BRAND_VOICE_TONE"] = req.brand.voice_tone
    if req.brand.persona_bio:
        overlay["BRAND_PERSONA_BIO"] = req.brand.persona_bio
    if req.brand.persona_name:
        overlay["BRAND_PERSONA_NAME"] = req.brand.persona_name
    if req.brand.product_categories:
        overlay["BRAND_PRODUCT_CATEGORIES"] = ",".join(req.brand.product_categories)
    if req.brand.target_audience:
        overlay["BRAND_TARGET_AUDIENCE"] = req.brand.target_audience
    if req.brand.banned_phrases:
        overlay["BRAND_BANNED_PHRASES"] = "||".join(req.brand.banned_phrases)

    overlay["ENGINE_STATE_FILE"] = str(_shop_state_path(req.shop_domain))

    log.info(
        f"[{request_id}] generate-article shop={req.shop_domain} "
        f"dry_run={req.dry_run} brand={req.brand.brand_name or '<default>'}"
    )

    # ── Run engine inside env overlay ─────────────────────────────────────
    captured: list[str] = []
    capture_buf = io.StringIO()
    captured_stdout = sys.stdout
    captured_stderr = sys.stderr

    result: dict = {"status": "failed", "error": "engine returned no result"}
    try:
        with env_overlay(overlay):
            sys.stdout = capture_buf
            sys.stderr = capture_buf
            try:
                from content_engine import main as engine_main  # type: ignore

                exit_code = engine_main.main()
            finally:
                sys.stdout = captured_stdout
                sys.stderr = captured_stderr

            captured = capture_buf.getvalue().splitlines()
            # Parse last run from state file to get article URL + image
            try:
                state = json.loads(_shop_state_path(req.shop_domain).read_text())
                runs = state.get("run_log", [])
                last = runs[-1] if runs else {}
                covered = state.get("covered_topics", [])
                last_published = covered[-1] if covered else {}
            except Exception:
                last = {}
                last_published = {}

            if exit_code == 0:
                if req.dry_run:
                    result = {
                        "status": "dry_run",
                        "title": last.get("topic") or last_published.get("title"),
                    }
                elif last.get("status") == "duplicate_skipped":
                    result = {"status": "skipped", "title": last.get("topic")}
                else:
                    result = {
                        "status": "published",
                        "title": last_published.get("title"),
                        "article_url": last_published.get("live_url"),
                        "article_id": last_published.get("article_id"),
                        "hero_image_url": last_published.get("hero_url"),
                    }
            else:
                result = {
                    "status": "failed",
                    "error": f"engine exited with code {exit_code}",
                    "title": last.get("topic"),
                }
    except Exception as e:
        sys.stdout = captured_stdout
        sys.stderr = captured_stderr
        log.exception(f"[{request_id}] engine raised")
        captured = capture_buf.getvalue().splitlines()
        result = {"status": "failed", "error": f"{type(e).__name__}: {e}"}

    elapsed_ms = int((time.time() - t0) * 1000)
    log.info(
        f"[{request_id}] done status={result['status']} elapsed_ms={elapsed_ms}"
    )

    return GenerateArticleResponse(
        request_id=request_id,
        status=result["status"],
        title=result.get("title"),
        article_url=result.get("article_url"),
        article_id=result.get("article_id"),
        hero_image_url=result.get("hero_image_url"),
        elapsed_ms=elapsed_ms,
        error=result.get("error"),
        logs_tail=captured[-50:] if captured else None,
    )
