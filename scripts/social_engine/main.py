"""
Social Engine — daily AI product review video pipeline.

Modes:
  generate   (default) — pick product, generate HeyGen video, email for review
  publish    — download video URL from env and post to all platforms
"""
import argparse
import json
import os
import tempfile
import urllib.request

from . import product_picker, script_gen, heygen, poster
from . import config


def _send_review_email(product_title: str, script: str, video_url: str, captions: dict) -> None:
    resend_key = os.environ.get("RESEND_API_KEY", "")
    if not resend_key:
        print(f"  [email skipped — no RESEND_API_KEY]")
        print(f"  Video URL: {video_url}")
        return

    caption_html = "".join(
        f"<p><strong>{k.replace('_',' ').title()}:</strong><br>{v}</p>"
        for k, v in captions.items()
    )
    html = f"""
<h2>New AI Product Review — Approve to Post</h2>
<p><strong>Product:</strong> {product_title}</p>
<p><strong>Script:</strong><br><em>{script}</em></p>
<p><a href="{video_url}" style="background:#000;color:#fff;padding:12px 24px;text-decoration:none;font-weight:bold;display:inline-block;margin:16px 0;">▶ Watch Video</a></p>
{caption_html}
<hr>
<p style="color:#666;font-size:12px;">
To post: go to GitHub Actions → Social Engine → Run workflow → paste video URL below and set mode=publish.<br>
Or reply and I'll handle it.
</p>
"""
    payload = json.dumps({
        "from": "Elm & Rye Alerts <alerts@elmandrye.com>",
        "to": ["aj@elmandrye.com"],
        "subject": f"Review before posting: {product_title} video",
        "html": html,
    }).encode()
    req = urllib.request.Request(
        "https://api.resend.com/emails", data=payload,
        headers={"Authorization": f"Bearer {resend_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            print(f"  Review email sent: {json.loads(r.read()).get('id')}")
    except Exception as e:
        print(f"  Email failed: {e} — video URL: {video_url}")


def generate() -> None:
    print("=== Social Engine: Generate ===")

    print("\n[1/3] Picking product...")
    product = product_picker.get_product()
    print(f"  → {product['title']}")

    print("\n[2/3] Generating script and captions...")
    content = script_gen.generate(product)
    print(f"  {content['script'][:120]}...")

    print("\n[3/3] Generating HeyGen video...")
    video_url = heygen.generate_video(content["script"])
    print(f"  Ready: {video_url[:80]}...")

    captions = {k: content[k] for k in ("instagram_caption", "facebook_caption", "tiktok_caption", "twitter_caption")}

    print("\nSending review email...")
    _send_review_email(product["title"], content["script"], video_url, captions)

    # Write summary to stdout for GitHub Actions log
    print(f"\n{'='*60}")
    print(f"PRODUCT: {product['title']}")
    print(f"VIDEO_URL: {video_url}")
    print(f"SCRIPT: {content['script']}")
    print(f"{'='*60}")
    print("\n✓ Video ready for review. Not posted yet.")


def publish(video_url: str, captions_json: str) -> None:
    print("=== Social Engine: Publish ===")
    captions = json.loads(captions_json)

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video_path = tmp.name

    try:
        print("Downloading video...")
        heygen.download_video(video_url, video_path)

        print("Posting to platforms...")
        poster.post_instagram(video_url, captions.get("instagram_caption", ""))
        poster.post_facebook(video_path, captions.get("facebook_caption", ""))
        poster.post_tiktok(video_path, captions.get("tiktok_caption", ""))
        poster.post_twitter(video_path, captions.get("twitter_caption", ""))
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)

    print("\n✓ Posted to all platforms.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", default="generate", choices=["generate", "publish"])
    parser.add_argument("--video-url", default="")
    parser.add_argument("--captions", default="{}")
    args = parser.parse_args()

    if args.mode == "publish":
        publish(args.video_url, args.captions)
    else:
        generate()
