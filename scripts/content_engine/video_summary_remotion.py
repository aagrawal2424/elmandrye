"""Remotion-based renderer for the animated video summary.

Lives behind the CONTENT_ENGINE_VIDEO=true env flag. When enabled, the
video_summary pipeline calls render_remotion_video() instead of the Pillow
static-slide compositer. Everything else (script generation, voiceover,
Shopify upload, embed HTML) is unchanged.

The Remotion project itself is at <repo>/video-renderer/ — a peer of the
scripts/ tree. `npm ci` must have been run there before this is called.
The GitHub Actions workflow does that step ahead of the python entry.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
# Repo root is two levels up from scripts/content_engine/
REPO_ROOT = HERE.parent.parent
VIDEO_RENDERER_DIR = REPO_ROOT / "video-renderer"


# Voice tuning the user signed off on for this v1 — Adam, eleven_multilingual_v2.
# Anchored here (not in env defaults) so we don't accidentally drift them
# between runs. Override via env when intentionally A/B testing.
ELEVENLABS_ADAM_VOICE_ID = "pNInz6obpgDXkVnNRC50"
ELEVENLABS_V2_MODEL_ID = "eleven_multilingual_v2"
ELEVENLABS_V2_VOICE_SETTINGS = {
    "stability": 0.35,
    "similarity_boost": 0.85,
    "style": 0.45,
    "use_speaker_boost": True,
}


@dataclass
class VideoPointV2:
    """Richer point shape Remotion scenes consume. Maps 1-to-1 onto the
    `points` array in props.json (see video-renderer/src/types.ts)."""
    headline: str  # 3-6 words shown as the big slide heading
    body: str      # one sentence shown beneath, smaller


def is_enabled() -> bool:
    """Single source of truth for the feature gate."""
    return os.environ.get("CONTENT_ENGINE_VIDEO", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def renderer_ready() -> bool:
    """Verify the Remotion project has been installed (npm ci)."""
    if not VIDEO_RENDERER_DIR.is_dir():
        print(f"[video/remotion] {VIDEO_RENDERER_DIR} missing — disable CONTENT_ENGINE_VIDEO")
        return False
    if not (VIDEO_RENDERER_DIR / "node_modules" / ".package-lock.json").exists() and \
       not (VIDEO_RENDERER_DIR / "node_modules" / "remotion").exists():
        print("[video/remotion] node_modules not installed — run `npm ci` in video-renderer/")
        return False
    return True


def synthesize_voiceover_adam(
    spoken_text: str,
    output_path: Path,
    api_key: str,
) -> bool:
    """Adam voice + multilingual_v2 + the v1-locked settings."""
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID_V2", ELEVENLABS_ADAM_VOICE_ID)
    model_id = os.environ.get("ELEVENLABS_MODEL_ID_V2", ELEVENLABS_V2_MODEL_ID)
    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        f"?output_format=mp3_44100_128"
    )
    body = json.dumps({
        "text": spoken_text,
        "model_id": model_id,
        "voice_settings": ELEVENLABS_V2_VOICE_SETTINGS,
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            output_path.write_bytes(r.read())
        return True
    except Exception as e:
        print(f"[video/remotion] voiceover failed: {e}")
        return False


def expand_points_for_video(
    raw_points: list[str],
    title: str,
    article_md: str,
) -> list[VideoPointV2]:
    """Convert the legacy `[str]` script points into `[{headline, body}]`.

    Strategy: ask Claude once for headline+body pairs derived from the
    already-vetted point sentences. If the call fails, fall back to a
    deterministic split (first 5 words as headline, rest as body) so we
    never block the publish on this.
    """
    from get_token import load_env  # local import to avoid a cycle on init
    env = load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key or not raw_points:
        return [_deterministic_split(p) for p in raw_points]

    prompt = (
        "You are reformatting bullet sentences from an article video script "
        "into title-card slides. For each input sentence, return a 3-6 word "
        "HEADLINE (sentence-case, no period) and a one-sentence BODY (under "
        "16 words). Headlines are the big slide text; the body explains it.\n\n"
        "Hard rules: no em dashes (use commas), no marketing hype, no "
        '"Discover/Unlock/Powerhouse" verbs. Output ONLY this JSON object:\n'
        '{"points":[{"headline":"...","body":"..."}, ...]}\n\n'
        f"Article title: {title}\n\nInput sentences (in order):\n"
        + "\n".join(f"- {p}" for p in raw_points)
        + f"\n\nArticle context (for tone only, do not paraphrase verbatim):\n"
        + article_md[:1200]
    )
    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1200,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        raw = data["content"][0]["text"].strip()
    except Exception as e:
        print(f"[video/remotion] headline expansion failed: {e} — falling back to split")
        return [_deterministic_split(p) for p in raw_points]

    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        return [_deterministic_split(p) for p in raw_points]
    try:
        parsed = json.loads(raw[start:end + 1])
        pts = parsed.get("points") or []
    except Exception:
        return [_deterministic_split(p) for p in raw_points]

    out: list[VideoPointV2] = []
    for i, p in enumerate(pts[: len(raw_points)]):
        h = str(p.get("headline") or "").strip()
        b = str(p.get("body") or "").strip()
        if not h or not b:
            out.append(_deterministic_split(raw_points[i]))
            continue
        out.append(VideoPointV2(headline=h[:60], body=b[:140]))
    while len(out) < len(raw_points):
        out.append(_deterministic_split(raw_points[len(out)]))
    return out


def _deterministic_split(sentence: str) -> VideoPointV2:
    words = sentence.strip().rstrip(".").split()
    head = " ".join(words[:5])
    tail = " ".join(words[5:]) or sentence
    return VideoPointV2(headline=head[:60] or "Key takeaway", body=tail[:140])


def render_remotion_video(
    audio_path: Path,
    title: str,
    handle: str,
    points: list[VideoPointV2],
    hero_image_url: Optional[str],
    audio_duration_sec: float,
    output_path: Path,
) -> bool:
    """Drive the Remotion CLI. Returns True on success."""
    if not renderer_ready():
        return False
    npx = shutil.which("npx")
    if not npx:
        print("[video/remotion] npx not on PATH")
        return False

    public_dir = VIDEO_RENDERER_DIR / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    # Stage narration + hero so staticFile() can resolve them from the bundle.
    audio_dest = public_dir / "narration.mp3"
    shutil.copyfile(audio_path, audio_dest)

    hero_rel: Optional[str] = None
    if hero_image_url:
        try:
            req = urllib.request.Request(hero_image_url, headers={"User-Agent": "elmandrye"})
            with urllib.request.urlopen(req, timeout=30) as r:
                hero_bytes = r.read()
            hero_dest = public_dir / "hero.jpg"
            hero_dest.write_bytes(hero_bytes)
            hero_rel = "hero.jpg"
        except Exception as e:
            print(f"[video/remotion] hero fetch failed ({e}) — continuing without")

    props = {
        "title": title,
        "handle": handle,
        "points": [{"headline": p.headline, "body": p.body} for p in points[:5]],
        "audioSrc": "narration.mp3",
        "audioDurationSec": max(6.0, float(audio_duration_sec)),
        "heroImageSrc": hero_rel,
    }
    props_path = VIDEO_RENDERER_DIR / "props.json"
    props_path.write_text(json.dumps(props, indent=2))

    # Render. Concurrency=1 keeps memory bounded on GH Actions' 7GB runners.
    cmd = [
        npx, "remotion", "render",
        "src/index.ts", "VideoSummary",
        str(output_path.resolve()),
        f"--props={props_path}",
        "--concurrency=1",
        "--log=info",
    ]
    print(f"[video/remotion] $ {' '.join(cmd)}")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(VIDEO_RENDERER_DIR),
            check=False,
            capture_output=True,
            timeout=900,  # 15min ceiling for the render itself
        )
    except subprocess.TimeoutExpired:
        print("[video/remotion] render exceeded 15min — aborting")
        return False

    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode(errors="replace")[-2000:])
        print(f"[video/remotion] render exited {proc.returncode}")
        return False
    if not output_path.exists() or output_path.stat().st_size < 5_000:
        print(f"[video/remotion] output mp4 missing or suspiciously small: {output_path}")
        return False
    return True
