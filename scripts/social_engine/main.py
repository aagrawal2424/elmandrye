"""
Social Engine — daily AI product review video pipeline.
Picks a random Elm & Rye product, generates a HeyGen video, posts to all platforms.
"""
import argparse
import os
import tempfile

from . import product_picker, script_gen, heygen, poster


def run(dry_run: bool = False) -> None:
    print("=== Social Engine ===")

    # 1. Pick product
    print("\n[1/4] Picking product...")
    product = product_picker.get_product()
    print(f"  → {product['title']} (${product['variants'][0]['price']})")

    # 2. Generate script + captions
    print("\n[2/4] Generating script and captions...")
    content = script_gen.generate(product)
    print(f"  Script ({len(content['script'].split())} words):")
    print(f"  {content['script'][:120]}...")

    if dry_run:
        print("\n[DRY RUN] Skipping video generation and posting.")
        print("\nInstagram caption:", content["instagram_caption"])
        print("Facebook caption:", content["facebook_caption"])
        print("TikTok caption:", content["tiktok_caption"])
        print("Twitter caption:", content["twitter_caption"])
        return

    # 3. Generate HeyGen video
    print("\n[3/4] Generating HeyGen video (9:16)...")
    video_url = heygen.generate_video(content["script"])
    print(f"  Video URL: {video_url[:80]}...")

    # 4. Download + post
    print("\n[4/4] Posting to platforms...")
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video_path = tmp.name

    try:
        heygen.download_video(video_url, video_path)
        print(f"  Downloaded to {video_path}")

        poster.post_instagram(video_url, content["instagram_caption"])
        poster.post_facebook(video_path, content["facebook_caption"])
        poster.post_tiktok(video_path, content["tiktok_caption"])
        poster.post_twitter(video_path, content["twitter_caption"])
    finally:
        if os.path.exists(video_path):
            os.unlink(video_path)

    print("\n✓ Done.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
