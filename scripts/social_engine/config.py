import os

HEYGEN_API_KEY = os.environ["HEYGEN_API_KEY"]
HEYGEN_AVATAR_ID = os.environ.get("HEYGEN_AVATAR_ID", "880c2d15de754d9aaa75601179ec84c8")
HEYGEN_VOICE_ID = os.environ.get("HEYGEN_VOICE_ID", "68dedac41a9f46a6a4271a95c733823c")

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SHOPIFY_STORE = os.environ.get("SHOPIFY_STORE", "elmandrye.myshopify.com")
SHOPIFY_TOKEN = os.environ["SHOPIFY_TOKEN"]

# Meta (Instagram + Facebook)
META_PAGE_ACCESS_TOKEN = os.environ.get("META_PAGE_ACCESS_TOKEN", "")
META_PAGE_ID = os.environ.get("META_PAGE_ID", "")
INSTAGRAM_ACCOUNT_ID = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")

# TikTok
TIKTOK_ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")

# Twitter/X
TWITTER_API_KEY = os.environ.get("TWITTER_API_KEY", "")
TWITTER_API_SECRET = os.environ.get("TWITTER_API_SECRET", "")
TWITTER_ACCESS_TOKEN = os.environ.get("TWITTER_ACCESS_TOKEN", "")
TWITTER_ACCESS_SECRET = os.environ.get("TWITTER_ACCESS_SECRET", "")
