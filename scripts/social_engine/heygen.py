"""Generate a HeyGen video and return the download URL."""
import json
import time
import urllib.request
from . import config

API = "https://api.heygen.com"


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode(),
        headers={"X-Api-Key": config.HEYGEN_API_KEY, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def _get(path: str) -> dict:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"X-Api-Key": config.HEYGEN_API_KEY},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def generate_video(script: str) -> str:
    """Submit video job, poll until done, return video download URL."""
    resp = _post("/v2/video/generate", {
        "video_inputs": [{
            "character": {
                "type": "avatar",
                "avatar_id": config.HEYGEN_AVATAR_ID,
                "avatar_style": "normal",
            },
            "voice": {
                "type": "text",
                "input_text": script,
                "voice_id": config.HEYGEN_VOICE_ID,
                "speed": 1.0,
            },
        }],
        "dimension": {"width": 1080, "height": 1920},
        "aspect_ratio": "9:16",
    })

    video_id = resp["data"]["video_id"]
    print(f"  HeyGen video submitted: {video_id}")

    # Poll until complete (timeout 10 min)
    for _ in range(120):
        time.sleep(5)
        status = _get(f"/v1/video_status.get?video_id={video_id}")
        data = status.get("data", {})
        state = data.get("status", "")
        print(f"  Status: {state}")
        if state == "completed":
            return data["video_url"]
        if state in ("failed", "error"):
            raise RuntimeError(f"HeyGen failed: {data.get('error')}")

    raise TimeoutError("HeyGen video timed out after 10 minutes")


def download_video(url: str, path: str) -> None:
    urllib.request.urlretrieve(url, path)
