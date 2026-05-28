"""Resilient hero image generation for the content engine.

Architecture (from workflow diagnosis of recurring imageless-publish bug):

  Provider chain:  OpenAI gpt-image-1
                 → Replicate Flux 1.1 Pro
                 → Stability Ultra
                 → Replicate Flux Schnell

  Per provider: up to 2 attempts (exponential backoff with jitter).
  Skip rules:   AuthError or ModerationError → skip provider entirely.
                RateLimitError / TransientError → retry once, then advance.
  Global cap:   90s total wall-clock budget across all providers.

  Validation:   decoded bytes must start with the PNG magic header AND
                exceed 10 KB. Otherwise the result is rejected as
                TransientError on the current provider.

  Failure:      if all providers exhaust, raise HeroPipelineFailure with
                a structured per-attempt log. main.py treats this as
                FATAL — no imageless article is allowed to publish.

  Backward compat: callers that still call generate_hero() get the new
                behavior. The function now RAISES on total failure rather
                than returning empty string. Old call sites that did
                `if not hero_url:` will still work (None is falsy) but
                the recommended pattern is to catch HeroPipelineFailure.
"""
from __future__ import annotations

import base64
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402
from upload_file import staged_upload, post_multipart, file_create, wait_for_url  # noqa: E402


# ── Errors ──────────────────────────────────────────────────────────────────

class HeroProviderError(Exception):
    """Base for provider-level failures. Carries a category for chain routing."""


class AuthError(HeroProviderError):
    """API key missing, revoked, or wrong account. Skip provider entirely."""


class ModerationError(HeroProviderError):
    """Prompt rejected by safety system. Skip provider — same prompt won't pass."""


class RateLimitError(HeroProviderError):
    """429 or quota exceeded. Retry once with backoff, then advance."""


class TransientError(HeroProviderError):
    """5xx, timeout, network blip, malformed response. Retry once."""


class HeroPipelineFailure(Exception):
    """All providers exhausted. Caller MUST treat as fatal and not publish."""

    def __init__(self, attempts_log: list[dict]):
        self.attempts_log = attempts_log
        summary = " | ".join(
            f"{a['provider']}#{a['attempt']}={a['error_class']}({a['error_msg'][:60]})"
            for a in attempts_log
        )
        super().__init__(f"All hero providers failed: {summary}")


# ── Validation ──────────────────────────────────────────────────────────────

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_BYTES = 10_240  # 10 KB — anything smaller is corrupt or 1x1 placeholder


def _validate_png(data: bytes) -> None:
    if not data:
        raise TransientError("empty response body")
    if len(data) < MIN_BYTES:
        raise TransientError(f"PNG too small ({len(data)} bytes < {MIN_BYTES})")
    if not data.startswith(PNG_MAGIC):
        raise TransientError(f"not a PNG (first 8 bytes: {data[:8]!r})")


# ── Prompt ──────────────────────────────────────────────────────────────────

def build_image_prompt(article_title: str) -> str:
    return (
        f"Editorial hero image for a wellness article titled '{article_title}'. "
        "Style: clean, minimalist, soft warm pastel color palette (cream, beige, soft sage, "
        "muted ochre). Abstract botanical or supplement-adjacent imagery — think soft "
        "natural-light photography of leaves, stones, glass jars, droplets, mortar and "
        "pestle, abstract gradients. NO text, NO words, NO letters, NO faces of any people, "
        "NO recognizable brand logos. Suitable for premium supplement blog hero. "
        "16:9 horizontal aspect."
    )


# ── HTTP helper ─────────────────────────────────────────────────────────────

def _post_json(url: str, headers: dict, body: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        if e.code in (401, 403):
            raise AuthError(f"{e.code}: {raw[:200]}") from e
        if e.code == 400 and any(
            k in raw.lower() for k in ("safety", "moderation", "policy", "rejected")
        ):
            raise ModerationError(f"{e.code}: {raw[:200]}") from e
        if e.code == 429:
            raise RateLimitError(f"429: {raw[:200]}") from e
        if e.code >= 500:
            raise TransientError(f"{e.code}: {raw[:200]}") from e
        raise TransientError(f"{e.code}: {raw[:200]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransientError(f"network: {e}") from e


def _get_bytes(url: str, headers: dict, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise TransientError(f"download {e.code}") from e
        raise TransientError(f"download {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TransientError(f"download network: {e}") from e


# ── Providers (each raises typed errors, returns PNG bytes) ────────────────

def _provider_openai(prompt: str, timeout_sec: int) -> bytes:
    env = load_env()
    api_key = env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise AuthError("OPENAI_API_KEY missing or empty in env")
    model = env.get("OPENAI_IMAGE_MODEL", "gpt-image-1")

    result = _post_json(
        "https://api.openai.com/v1/images/generations",
        headers={"Authorization": f"Bearer {api_key}"},
        body={
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": "1536x1024",
            "quality": "medium",
        },
        timeout=timeout_sec,
    )
    if "error" in result:
        err = result["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        code = err.get("code", "") if isinstance(err, dict) else ""
        if code in ("content_policy_violation", "moderation_blocked"):
            raise ModerationError(msg)
        raise TransientError(f"openai error: {msg}")
    try:
        b64 = result["data"][0]["b64_json"]
    except (KeyError, IndexError, TypeError) as e:
        raise TransientError(f"malformed openai response: {e}") from e
    data = base64.b64decode(b64)
    _validate_png(data)
    return data


def _provider_replicate_flux_pro(prompt: str, timeout_sec: int) -> bytes:
    return _replicate_predict(
        prompt, timeout_sec,
        model_version="black-forest-labs/flux-1.1-pro",
        extra_input={"aspect_ratio": "3:2", "output_format": "png", "output_quality": 90},
    )


def _provider_replicate_flux_schnell(prompt: str, timeout_sec: int) -> bytes:
    return _replicate_predict(
        prompt, timeout_sec,
        model_version="black-forest-labs/flux-schnell",
        extra_input={"aspect_ratio": "3:2", "output_format": "png"},
    )


def _replicate_predict(
    prompt: str,
    timeout_sec: int,
    model_version: str,
    extra_input: dict,
) -> bytes:
    env = load_env()
    token = env.get("REPLICATE_API_TOKEN") or os.environ.get("REPLICATE_API_TOKEN", "")
    if not token:
        raise AuthError("REPLICATE_API_TOKEN missing or empty in env")

    # Replicate "official model" endpoint creates + polls in one shot
    start = time.time()
    create = _post_json(
        f"https://api.replicate.com/v1/models/{model_version}/predictions",
        headers={
            "Authorization": f"Bearer {token}",
            "Prefer": "wait=30",  # block up to 30s server-side for completion
        },
        body={"input": {"prompt": prompt, **extra_input}},
        timeout=timeout_sec,
    )

    pred_id = create.get("id")
    status = create.get("status")
    output = create.get("output")
    # Poll if still in progress
    while status in ("starting", "processing") and (time.time() - start) < timeout_sec:
        time.sleep(2)
        poll_req = urllib.request.Request(
            f"https://api.replicate.com/v1/predictions/{pred_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=20) as r:
                poll = json.loads(r.read())
        except Exception as e:
            raise TransientError(f"poll error: {e}") from e
        status = poll.get("status")
        output = poll.get("output")

    if status == "failed":
        err = (create.get("error") or "").lower()
        if "nsfw" in err or "safety" in err or "moderation" in err:
            raise ModerationError(create.get("error", "moderation"))
        raise TransientError(f"replicate failed: {create.get('error', 'unknown')}")
    if status != "succeeded":
        raise TransientError(f"replicate status={status} after {int(time.time()-start)}s")

    image_url = output[0] if isinstance(output, list) and output else output
    if not isinstance(image_url, str):
        raise TransientError(f"replicate returned unexpected output: {type(output).__name__}")
    data = _get_bytes(image_url, headers={}, timeout=timeout_sec)
    _validate_png(data)
    return data


def _provider_stability(prompt: str, timeout_sec: int) -> bytes:
    env = load_env()
    api_key = env.get("STABILITY_API_KEY") or os.environ.get("STABILITY_API_KEY", "")
    if not api_key:
        raise AuthError("STABILITY_API_KEY missing or empty in env")

    # Stable Image Ultra v2beta — multipart form, returns PNG bytes directly
    boundary = "----formboundary" + os.urandom(8).hex()
    parts: list[bytes] = []
    for k, v in [
        ("prompt", prompt),
        ("aspect_ratio", "3:2"),
        ("output_format", "png"),
    ]:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    req = urllib.request.Request(
        "https://api.stability.ai/v2beta/stable-image/generate/ultra",
        data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "image/*",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        if e.code in (401, 403):
            raise AuthError(f"{e.code}: {raw[:200]}") from e
        if e.code == 400 and "content" in raw.lower():
            raise ModerationError(f"{e.code}: {raw[:200]}") from e
        if e.code == 429:
            raise RateLimitError(f"429: {raw[:200]}") from e
        raise TransientError(f"{e.code}: {raw[:200]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise TransientError(f"stability network: {e}") from e
    _validate_png(data)
    return data


# ── Chain runner ────────────────────────────────────────────────────────────

@dataclass
class _ProviderSpec:
    name: str
    func: Callable[[str, int], bytes]


PROVIDER_CHAIN: list[_ProviderSpec] = [
    _ProviderSpec("openai-gpt-image-1", _provider_openai),
    _ProviderSpec("replicate-flux-1.1-pro", _provider_replicate_flux_pro),
    _ProviderSpec("stability-ultra", _provider_stability),
    _ProviderSpec("replicate-flux-schnell", _provider_replicate_flux_schnell),
]

PER_ATTEMPT_TIMEOUT = 30
PER_PROVIDER_ATTEMPTS = 2
TOTAL_BUDGET_SEC = 90


@dataclass
class HeroResult:
    png_bytes: bytes
    provider: str
    attempts_log: list[dict] = field(default_factory=list)


def generate_hero_bytes(prompt: str) -> HeroResult:
    """Run the provider chain. Returns the first successful PNG.

    Raises HeroPipelineFailure with the full per-attempt log if every
    provider exhausts. Logs each attempt to stderr in structured form so
    the GH Actions log shows exactly which provider hit what error.
    """
    started = time.time()
    log: list[dict] = []

    for provider in PROVIDER_CHAIN:
        attempt = 0
        while attempt < PER_PROVIDER_ATTEMPTS:
            attempt += 1
            elapsed = time.time() - started
            if elapsed > TOTAL_BUDGET_SEC:
                _emit(log, provider.name, attempt, "BudgetExceeded",
                      f"global budget {TOTAL_BUDGET_SEC}s exhausted")
                raise HeroPipelineFailure(log)

            t0 = time.time()
            try:
                data = provider.func(prompt, PER_ATTEMPT_TIMEOUT)
                latency_ms = int((time.time() - t0) * 1000)
                log.append({
                    "provider": provider.name,
                    "attempt": attempt,
                    "error_class": "OK",
                    "error_msg": "",
                    "latency_ms": latency_ms,
                })
                print(
                    f"[hero] OK provider={provider.name} attempt={attempt} "
                    f"latency_ms={latency_ms} bytes={len(data)}",
                    file=sys.stderr,
                )
                return HeroResult(png_bytes=data, provider=provider.name, attempts_log=log)
            except AuthError as e:
                _emit(log, provider.name, attempt, "AuthError", str(e),
                      latency_ms=int((time.time() - t0) * 1000))
                break  # skip provider entirely
            except ModerationError as e:
                _emit(log, provider.name, attempt, "ModerationError", str(e),
                      latency_ms=int((time.time() - t0) * 1000))
                break  # same prompt won't pass; skip provider
            except RateLimitError as e:
                _emit(log, provider.name, attempt, "RateLimitError", str(e),
                      latency_ms=int((time.time() - t0) * 1000))
                if attempt < PER_PROVIDER_ATTEMPTS:
                    _sleep_with_jitter(2.0)
            except TransientError as e:
                _emit(log, provider.name, attempt, "TransientError", str(e),
                      latency_ms=int((time.time() - t0) * 1000))
                if attempt < PER_PROVIDER_ATTEMPTS:
                    _sleep_with_jitter(2.0)
            except Exception as e:  # unexpected — log + advance
                _emit(log, provider.name, attempt, type(e).__name__, str(e),
                      latency_ms=int((time.time() - t0) * 1000))
                break

    raise HeroPipelineFailure(log)


def _emit(log: list[dict], provider: str, attempt: int, err_class: str,
          err_msg: str, latency_ms: int = 0) -> None:
    log.append({
        "provider": provider,
        "attempt": attempt,
        "error_class": err_class,
        "error_msg": err_msg,
        "latency_ms": latency_ms,
    })
    print(
        f"[hero] FAIL provider={provider} attempt={attempt} "
        f"error_class={err_class} latency_ms={latency_ms} msg={err_msg[:200]}",
        file=sys.stderr,
    )


def _sleep_with_jitter(base_sec: float, jitter_sec: float = 0.5) -> None:
    delay = max(0.0, base_sec + random.uniform(-jitter_sec, jitter_sec))
    time.sleep(delay)


# ── Upload + top-level entry ────────────────────────────────────────────────

def slug_from_title(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
    return s[:60]


def save_image_bytes(data: bytes, slug: str) -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="hero-"))
    out = tmp_dir / f"blog-hero-{slug}.png"
    out.write_bytes(data)
    return out


def upload_to_shopify(local_path: Path, alt: str) -> str:
    """Reuse the existing Shopify Files uploader. Returns CDN URL."""
    mime = "image/png"
    size = local_path.stat().st_size
    target = staged_upload(local_path.name, mime, size)
    fields = [{"name": p["name"], "value": p["value"]} for p in target["parameters"]]
    post_multipart(target["url"], fields, local_path, mime)
    f = file_create(target["resourceUrl"], alt)
    url = f.get("image", {}).get("url") if f.get("image") else None
    if not url:
        url = wait_for_url(f["id"])
    if not url:
        raise TransientError("Shopify upload returned no CDN url after polling")
    return url


def generate_hero(article_title: str) -> str:
    """Top-level: provider chain → PNG → Shopify Files → CDN URL.

    CHANGED BEHAVIOR (was: silently returned "" on any failure).
    Now RAISES HeroPipelineFailure if every provider fails. main.py is
    responsible for catching and treating that as a hard failure.

    Still raises TransientError if the Shopify upload itself fails after
    the image was generated — caller can treat that the same way.
    """
    prompt = build_image_prompt(article_title)
    result = generate_hero_bytes(prompt)

    local = save_image_bytes(result.png_bytes, slug_from_title(article_title))
    try:
        cdn_url = upload_to_shopify(local, f"Hero image: {article_title}")
    finally:
        try:
            local.unlink(missing_ok=True)
            local.parent.rmdir()
        except OSError:
            pass

    print(
        f"[hero] published cdn_url={cdn_url} provider={result.provider}",
        file=sys.stderr,
    )
    return cdn_url


if __name__ == "__main__":
    title = " ".join(sys.argv[1:]) or "Magnesium glycinate for sleep — what we know"
    try:
        url = generate_hero(title)
        print(url)
    except HeroPipelineFailure as e:
        print(f"FAILED — {e}", file=sys.stderr)
        print(json.dumps(e.attempts_log, indent=2), file=sys.stderr)
        sys.exit(2)
