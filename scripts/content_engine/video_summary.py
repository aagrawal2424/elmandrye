"""AI-generated video summary attached to each published article.

Pipeline:
  1. Generate a tight 90s script from the article (Claude → JSON)
  2. Synthesize voiceover via ElevenLabs
  3. Composite a square 1080x1080 mp4 with the script's key points as a
     static slide over the audio (ffmpeg)
  4. Upload the mp4 to Shopify Files
  5. Build a VideoObject schema + HTML5 embed + transcript block

The caller (main.py) takes the returned HTML and prepends it inside the
article body HTML before publish. SEO/AEO value comes from:
  - Transcript indexed by Google and LLM crawlers (Perplexity, ChatGPT)
  - VideoObject schema unlocks the "video" SERP carousel
  - Dwell time bump from having a player on the page

Gracefully degrades: if ELEVENLABS_API_KEY is missing OR ffmpeg isn't on
PATH OR any step fails, returns None and the article publishes without
the video block — never breaks the publish pipeline.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from gql import call  # noqa: E402
from get_token import get_token, load_env  # noqa: E402

# ── Defaults ─────────────────────────────────────────────────────────────────

ELEVENLABS_VOICE_ID = os.environ.get(
    "ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL"  # "Sarah" — clean professional female
)
ELEVENLABS_MODEL_ID = os.environ.get(
    "ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5"
)

# Brand palette (lifted from the er-disclaimer and demand-intercept inline styles)
BG_COLOR = (250, 247, 240)     # cream
ACCENT = (201, 178, 122)       # gold
INK = (15, 15, 13)             # near-black
INK_MUTED = (74, 74, 74)


# ── Data shapes ──────────────────────────────────────────────────────────────

@dataclass
class VideoScript:
    hook: str               # one-line opener
    points: list[str]       # 3-4 short bullets (already trimmed for slide)
    cta: str                # closing line; usually points back to a product
    spoken_text: str        # full narration script (what ElevenLabs reads)


@dataclass
class GeneratedVideo:
    cdn_url: str            # final mp4 URL on shopify cdn
    duration_sec: float
    transcript: str
    title: str              # video title (article title + " (Summary)")


# ── 1. Script generation ────────────────────────────────────────────────────

def generate_script(article_md: str, title: str) -> Optional[VideoScript]:
    """Ask Claude to compress the article into a tight 90s script."""
    env = load_env()
    api_key = env.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    prompt = f"""You are summarizing an Elm & Rye blog article into a 90-second video script. The visuals will be a single brand slide; the audio is what carries the value. Target ~225 words spoken (90s at 150 wpm).

Hard rules:
- No em dashes. Use commas, periods, colons instead.
- No marketing hype phrases (powerhouse, game-changer, dive into, etc.).
- The "spoken_text" must read naturally end-to-end; do NOT include "[hook]" or "[point 1]" labels. It's narration.
- Points must be one sentence each, 7-12 words. They will be shown on screen.
- The hook is on screen as the title; spoken_text starts with the hook then flows.

Return ONLY this JSON object:
{{
  "hook": "Bold one-line hook (under 60 chars).",
  "points": [
    "First key takeaway as one short sentence.",
    "Second key takeaway as one short sentence.",
    "Third key takeaway as one short sentence."
  ],
  "cta": "One sentence directing readers to the full article or a product.",
  "spoken_text": "The full narration text, ~225 words, read aloud. Starts with the hook, weaves in the points naturally, ends with the cta."
}}

Article title: {title}

Article body:
{article_md[:8000]}"""

    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 2000,
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
        print(f"[video] script generation failed: {e}")
        return None

    # Extract trailing JSON object
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < 0:
        print(f"[video] script: no JSON object in response")
        return None
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception as e:
        print(f"[video] script: JSON parse failed: {e}")
        return None

    if not parsed.get("hook") or not parsed.get("spoken_text"):
        return None

    points = parsed.get("points") or []
    if not isinstance(points, list):
        return None

    return VideoScript(
        hook=str(parsed["hook"])[:140],
        points=[str(p)[:120] for p in points[:4]],
        cta=str(parsed.get("cta") or "")[:200],
        spoken_text=str(parsed["spoken_text"]),
    )


# ── 2. Voiceover via ElevenLabs ──────────────────────────────────────────────

def synthesize_voiceover(spoken_text: str, output_path: Path) -> bool:
    """Generate an MP3 from spoken_text. Returns True on success."""
    api_key = os.environ.get("ELEVENLABS_API_KEY") or load_env().get(
        "ELEVENLABS_API_KEY", ""
    )
    if not api_key:
        print("[video] no ELEVENLABS_API_KEY — skipping voiceover")
        return False

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
        f"?output_format=mp3_44100_128"
    )
    body = json.dumps({
        "text": spoken_text,
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
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
        print(f"[video] voiceover failed: {e}")
        return False


# ── 3. ffmpeg compositing ────────────────────────────────────────────────────

def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _render_slide(script: VideoScript, title: str, output_path: Path) -> bool:
    """Render a single 1080x1080 PNG with the script's hook + points."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[video] Pillow not installed — install with: pip install Pillow")
        return False

    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to use a serif font for brand feel; fall back to PIL default
    def _font(size: int, bold: bool = False) -> "ImageFont.FreeTypeFont":
        for name in (
            "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"
            if bold else
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"
            if bold else
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
            if bold else
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ):
            if Path(name).exists():
                return ImageFont.truetype(name, size)
        return ImageFont.load_default(size=size)

    # Brand band at top
    draw.rectangle([(0, 0), (W, 12)], fill=ACCENT)

    # Brand label
    f_brand = _font(28, bold=True)
    draw.text((60, 50), "ELM & RYE", fill=ACCENT, font=f_brand)

    # Hook (large, bold, multiline)
    f_hook = _font(64, bold=True)
    hook = script.hook
    margin_x = 60
    max_w = W - 2 * margin_x
    hook_lines = _wrap_text(hook, f_hook, max_w, draw)
    y = 150
    for line in hook_lines[:3]:
        draw.text((margin_x, y), line, fill=INK, font=f_hook)
        y += 78
    y += 20

    # Divider
    draw.rectangle([(margin_x, y), (margin_x + 80, y + 4)], fill=ACCENT)
    y += 40

    # Bullet points
    f_point = _font(36)
    for i, point in enumerate(script.points[:4]):
        bullet_y = y
        # gold dot
        draw.ellipse(
            [(margin_x, bullet_y + 16), (margin_x + 14, bullet_y + 30)],
            fill=ACCENT,
        )
        # text wrapped
        lines = _wrap_text(point, f_point, max_w - 50, draw)
        for j, line in enumerate(lines[:2]):
            draw.text((margin_x + 32, bullet_y + j * 48), line, fill=INK, font=f_point)
        y = bullet_y + max(50, len(lines[:2]) * 48 + 8)

    # Footer
    f_foot = _font(24)
    foot_txt = "Read the full article on elmandrye.com"
    draw.text((margin_x, H - 70), foot_txt, fill=INK_MUTED, font=f_foot)

    img.save(output_path, "PNG")
    return True


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for w in words:
        trial = f"{current} {w}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def composite_video(
    audio_path: Path,
    slide_path: Path,
    output_path: Path,
) -> bool:
    """ffmpeg: combine a static slide with the audio into an mp4."""
    if not _ffmpeg_available():
        print("[video] ffmpeg not on PATH — skipping composite")
        return False

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(slide_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        return True
    except subprocess.CalledProcessError as e:
        print(f"[video] ffmpeg failed: {e.stderr.decode(errors='replace')[:400]}")
        return False
    except Exception as e:
        print(f"[video] ffmpeg error: {e}")
        return False


def _probe_duration_sec(video_path: Path) -> float:
    """Use ffprobe to get the exact duration. Falls back to 90.0 on failure."""
    if not shutil.which("ffprobe"):
        return 90.0
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            timeout=30,
        )
        return float(out.strip())
    except Exception:
        return 90.0


# ── 4. Upload to Shopify Files ───────────────────────────────────────────────

def upload_video_to_shopify(video_path: Path, alt_text: str) -> Optional[str]:
    """Stage + register a video file in Shopify Files. Returns the CDN URL."""
    env = load_env()
    store = env.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
    api = env.get("SHOPIFY_API_VERSION", "2025-01")
    size = video_path.stat().st_size

    # Stage
    stage_q = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets {
          url resourceUrl
          parameters { name value }
        }
        userErrors { field message }
      }
    }
    """
    stage_resp = call(stage_q, {
        "input": [{
            "filename": video_path.name,
            "mimeType": "video/mp4",
            "resource": "VIDEO",
            "fileSize": str(size),
            "httpMethod": "POST",
        }]
    })
    targets = (
        stage_resp.get("data", {})
        .get("stagedUploadsCreate", {})
        .get("stagedTargets", [])
    )
    if not targets:
        print(f"[video] stagedUploadsCreate returned no targets: {stage_resp}")
        return None
    target = targets[0]

    # POST file to staged URL (multipart)
    boundary = "----formboundary" + os.urandom(8).hex()
    parts: list[bytes] = []
    for p in target["parameters"]:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{p["name"]}"\r\n\r\n{p["value"]}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{video_path.name}"\r\nContent-Type: video/mp4\r\n\r\n'.encode()
    )
    parts.append(video_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    final_body = b"".join(parts)

    req = urllib.request.Request(
        target["url"],
        data=final_body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            if r.status >= 300:
                print(f"[video] staged upload failed: {r.status}")
                return None
    except Exception as e:
        print(f"[video] staged upload error: {e}")
        return None

    # Register the file
    create_q = """
    mutation fileCreate($files: [FileCreateInput!]!) {
      fileCreate(files: $files) {
        files {
          id
          ... on Video {
            sources { url mimeType }
            originalSource { url }
          }
        }
        userErrors { field message }
      }
    }
    """
    create_resp = call(create_q, {
        "files": [{
            "originalSource": target["resourceUrl"],
            "contentType": "VIDEO",
            "alt": alt_text,
        }]
    })
    files = (
        create_resp.get("data", {})
        .get("fileCreate", {})
        .get("files", [])
    )
    if not files:
        print(f"[video] fileCreate returned no file: {create_resp}")
        return None
    file_id = files[0]["id"]

    # Poll until the video is processed and a CDN source URL is available
    poll_q = """
    query($id: ID!) {
      node(id: $id) {
        ... on Video {
          status
          sources { url mimeType height width }
          originalSource { url }
        }
      }
    }
    """
    import time
    cdn_url: Optional[str] = None
    for _ in range(30):
        resp = call(poll_q, {"id": file_id})
        node = resp.get("data", {}).get("node") or {}
        status = node.get("status")
        srcs = node.get("sources") or []
        if status == "READY" and srcs:
            cdn_url = srcs[0]["url"]
            break
        if status == "FAILED":
            print("[video] Shopify reported FAILED processing")
            return None
        time.sleep(4)
    return cdn_url


# ── 5. Embed + schema injection ──────────────────────────────────────────────

def build_video_block_html(
    video_url: str,
    title: str,
    transcript: str,
    duration_sec: float,
    poster_url: Optional[str] = None,
) -> str:
    """Return HTML to prepend to the article body.

    Contains:
      - Native <video> player
      - VideoObject JSON-LD schema
      - Collapsible transcript block (so LLM crawlers and Google find it)
    """
    upload_date = _now_iso()
    duration_iso = _iso_duration(duration_sec)
    safe_title = title.replace('"', '\\"')
    safe_desc = (transcript[:200] + "...").replace('"', '\\"').replace("\n", " ")

    schema = {
        "@context": "https://schema.org",
        "@type": "VideoObject",
        "name": safe_title,
        "description": safe_desc,
        "thumbnailUrl": poster_url or video_url,
        "uploadDate": upload_date,
        "duration": duration_iso,
        "contentUrl": video_url,
        "embedUrl": video_url,
        "transcript": transcript,
    }
    schema_html = (
        '<script type="application/ld+json">'
        + json.dumps(schema, separators=(",", ":"))
        + "</script>"
    )

    poster_attr = f'poster="{poster_url}"' if poster_url else ""
    video_html = f"""
<figure class="er-summary-video" style="margin:0 0 2rem;padding:1.25rem;background:#faf7f0;border-radius:6px;">
  <p style="margin:0 0 0.75rem;font-size:0.75rem;letter-spacing:0.16em;color:#c9b27a;text-transform:uppercase;font-weight:600;">
    90-second summary
  </p>
  <video controls preload="metadata" {poster_attr}
         style="width:100%;display:block;border-radius:4px;background:#000;"
         aria-label="Audio summary of {safe_title}">
    <source src="{video_url}" type="video/mp4" />
    Your browser does not support the video tag.
  </video>
  <details style="margin-top:0.75rem;">
    <summary style="cursor:pointer;font-size:0.85rem;color:#555;">
      Read the transcript
    </summary>
    <p style="margin-top:0.5rem;font-size:0.9rem;line-height:1.6;color:#444;">
      {_escape_html(transcript)}
    </p>
  </details>
</figure>
{schema_html}
""".strip()
    return video_html


def inject_into_article_body(body_html: str, video_block: str) -> str:
    """Prepend the video block to the article body. Right after the first <p>
    or at the very start if no <p> tag.
    """
    # Insert after the first paragraph so the opening hook isn't pushed below
    # the video; keeps article skimming intact.
    m = re.search(r"</p>", body_html, re.IGNORECASE)
    if m:
        idx = m.end()
        return body_html[:idx] + "\n\n" + video_block + "\n\n" + body_html[idx:]
    return video_block + "\n\n" + body_html


# ── Public entry point ──────────────────────────────────────────────────────

def generate_video_for_article(
    article_md: str,
    title: str,
    work_dir: Optional[Path] = None,
) -> Optional[GeneratedVideo]:
    """Top-level: returns a GeneratedVideo or None if any step gracefully bailed."""
    if not _ffmpeg_available():
        print("[video] ffmpeg not installed — skipping video summary")
        return None

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY") or load_env().get(
        "ELEVENLABS_API_KEY", ""
    )
    if not elevenlabs_key:
        print("[video] no ELEVENLABS_API_KEY — skipping video summary")
        return None

    print("[video] generating script…")
    script = generate_script(article_md, title)
    if not script:
        return None
    print(f"[video] script: {len(script.spoken_text)} chars ({len(script.points)} points)")

    cleanup = False
    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="er-video-"))
        cleanup = True

    try:
        audio_path = work_dir / "voiceover.mp3"
        slide_path = work_dir / "slide.png"
        video_path = work_dir / "summary.mp4"

        print("[video] synthesizing voiceover…")
        if not synthesize_voiceover(script.spoken_text, audio_path):
            return None
        print(f"[video]   audio: {audio_path.stat().st_size:,} bytes")

        print("[video] rendering slide…")
        if not _render_slide(script, title, slide_path):
            return None

        print("[video] compositing mp4…")
        if not composite_video(audio_path, slide_path, video_path):
            return None
        print(f"[video]   mp4: {video_path.stat().st_size:,} bytes")

        duration = _probe_duration_sec(video_path)
        print(f"[video]   duration: {duration:.1f}s")

        print("[video] uploading to Shopify Files…")
        cdn_url = upload_video_to_shopify(video_path, alt_text=f"Summary of: {title}")
        if not cdn_url:
            return None
        print(f"[video]   cdn: {cdn_url}")

        return GeneratedVideo(
            cdn_url=cdn_url,
            duration_sec=duration,
            transcript=script.spoken_text,
            title=title,
        )
    finally:
        if cleanup:
            try:
                import shutil as _sh
                _sh.rmtree(work_dir, ignore_errors=True)
            except Exception:
                pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_duration(seconds: float) -> str:
    """schema.org duration is ISO 8601 (PT1M30S style)."""
    s = int(round(seconds))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    parts = ["PT"]
    if h:
        parts.append(f"{h}H")
    if m:
        parts.append(f"{m}M")
    if s or (h == 0 and m == 0):
        parts.append(f"{s}S")
    return "".join(parts)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
