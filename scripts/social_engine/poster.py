"""Post video to Instagram, Facebook, TikTok, and Twitter/X."""
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
from . import config


# ── Instagram + Facebook (Meta Graph API) ─────────────────────────────────────

def post_instagram(video_url: str, caption: str) -> str:
    if not config.META_PAGE_ACCESS_TOKEN or not config.INSTAGRAM_ACCOUNT_ID:
        print("  Instagram: skipped (no credentials)")
        return ""

    base = "https://graph.facebook.com/v19.0"
    tok = config.META_PAGE_ACCESS_TOKEN
    ig_id = config.INSTAGRAM_ACCOUNT_ID

    # Step 1: create container
    data = urllib.parse.urlencode({
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": tok,
        "share_to_feed": "true",
    }).encode()
    req = urllib.request.Request(f"{base}/{ig_id}/media", data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        container_id = json.loads(r.read())["id"]

    # Step 2: poll until container ready
    for _ in range(60):
        time.sleep(5)
        check = urllib.parse.urlencode({"fields": "status_code", "access_token": tok})
        req = urllib.request.Request(f"{base}/{container_id}?{check}")
        with urllib.request.urlopen(req) as r:
            status = json.loads(r.read()).get("status_code", "")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError("Instagram container processing failed")

    # Step 3: publish
    data = urllib.parse.urlencode({"creation_id": container_id, "access_token": tok}).encode()
    req = urllib.request.Request(f"{base}/{ig_id}/media_publish", data=data, method="POST")
    with urllib.request.urlopen(req) as r:
        post_id = json.loads(r.read())["id"]

    print(f"  Instagram: posted {post_id}")
    return post_id


def post_facebook(video_path: str, caption: str) -> str:
    if not config.META_PAGE_ACCESS_TOKEN or not config.META_PAGE_ID:
        print("  Facebook: skipped (no credentials)")
        return ""

    base = "https://graph.facebook.com/v19.0"
    tok = config.META_PAGE_ACCESS_TOKEN
    page_id = config.META_PAGE_ID

    with open(video_path, "rb") as f:
        video_data = f.read()

    boundary = "ElmRyeBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="description"\r\n\r\n'
        f"{caption}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="access_token"\r\n\r\n'
        f"{tok}\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="source"; filename="review.mp4"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode() + video_data + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{base}/{page_id}/videos",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        post_id = json.loads(r.read()).get("id", "")

    print(f"  Facebook: posted {post_id}")
    return post_id


# ── TikTok ────────────────────────────────────────────────────────────────────

def post_tiktok(video_path: str, caption: str) -> str:
    if not config.TIKTOK_ACCESS_TOKEN:
        print("  TikTok: skipped (no credentials)")
        return ""

    tok = config.TIKTOK_ACCESS_TOKEN
    file_size = os.path.getsize(video_path)

    # Step 1: init upload
    init_data = json.dumps({
        "post_info": {
            "title": caption[:150],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_comment": False,
            "disable_stitch": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }).encode()
    req = urllib.request.Request(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        data=init_data,
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    upload_url = resp["data"]["upload_url"]
    publish_id = resp["data"]["publish_id"]

    # Step 2: upload chunk
    with open(video_path, "rb") as f:
        chunk = f.read()
    req = urllib.request.Request(
        upload_url,
        data=chunk,
        headers={
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            "Content-Type": "video/mp4",
            "Content-Length": str(file_size),
        },
        method="PUT",
    )
    urllib.request.urlopen(req)

    print(f"  TikTok: posted {publish_id}")
    return publish_id


# ── Twitter/X ─────────────────────────────────────────────────────────────────

def _oauth1_header(method: str, url: str, params: dict) -> str:
    import os, time, random, string
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    ts = str(int(time.time()))
    oauth_params = {
        "oauth_consumer_key": config.TWITTER_API_KEY,
        "oauth_nonce": nonce,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": ts,
        "oauth_token": config.TWITTER_ACCESS_TOKEN,
        "oauth_version": "1.0",
    }
    all_params = {**params, **oauth_params}
    param_str = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(str(v), safe='')}"
                         for k, v in sorted(all_params.items()))
    base = f"{method.upper()}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(param_str, safe='')}"
    signing_key = f"{urllib.parse.quote(config.TWITTER_API_SECRET, safe='')}&{urllib.parse.quote(config.TWITTER_ACCESS_SECRET, safe='')}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    oauth_params["oauth_signature"] = sig
    header = "OAuth " + ", ".join(f'{k}="{urllib.parse.quote(str(v), safe="")}"' for k, v in sorted(oauth_params.items()))
    return header


def post_twitter(video_path: str, caption: str) -> str:
    if not config.TWITTER_API_KEY:
        print("  Twitter/X: skipped (no credentials)")
        return ""

    import os
    file_size = os.path.getsize(video_path)

    # Step 1: INIT
    url = "https://upload.twitter.com/1.1/media/upload.json"
    params = {"command": "INIT", "total_bytes": str(file_size), "media_type": "video/mp4", "media_category": "tweet_video"}
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": _oauth1_header("POST", url, params)},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        media_id = json.loads(r.read())["media_id_string"]

    # Step 2: APPEND
    with open(video_path, "rb") as f:
        chunk = f.read()
    body = (
        f'--boundary\r\nContent-Disposition: form-data; name="command"\r\n\r\nAPPEND\r\n'
        f'--boundary\r\nContent-Disposition: form-data; name="media_id"\r\n\r\n{media_id}\r\n'
        f'--boundary\r\nContent-Disposition: form-data; name="segment_index"\r\n\r\n0\r\n'
        f'--boundary\r\nContent-Disposition: form-data; name="media"; filename="review.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
    ).encode() + chunk + b"\r\n--boundary--\r\n"
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": _oauth1_header("POST", url, {}), "Content-Type": "multipart/form-data; boundary=boundary"},
        method="POST",
    )
    urllib.request.urlopen(req)

    # Step 3: FINALIZE
    params = {"command": "FINALIZE", "media_id": media_id}
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(params).encode(),
        headers={"Authorization": _oauth1_header("POST", url, params)},
        method="POST",
    )
    urllib.request.urlopen(req)

    # Step 4: poll processing
    for _ in range(30):
        time.sleep(5)
        check_url = f"{url}?command=STATUS&media_id={media_id}"
        req = urllib.request.Request(check_url, headers={"Authorization": _oauth1_header("GET", url, {"command": "STATUS", "media_id": media_id})})
        with urllib.request.urlopen(req) as r:
            state = json.loads(r.read()).get("processing_info", {}).get("state", "")
        if state == "succeeded":
            break

    # Step 5: tweet
    tweet_url = "https://api.twitter.com/2/tweets"
    tweet_body = json.dumps({"text": caption, "media": {"media_ids": [media_id]}}).encode()
    req = urllib.request.Request(
        tweet_url, data=tweet_body,
        headers={"Authorization": _oauth1_header("POST", tweet_url, {}), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        tweet_id = json.loads(r.read())["data"]["id"]

    print(f"  Twitter/X: posted {tweet_id}")
    return tweet_id
