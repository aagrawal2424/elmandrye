"""Call Claude (Sonnet 4.6) to generate the daily article.

Uses raw HTTP via urllib — no third-party SDK needed. Loads the system
prompt from system_prompt.md and assembles a user message with the
topic, Reddit context, and internal-link registry.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from get_token import load_env  # noqa: E402

HERE = Path(__file__).resolve().parent


def load_system_prompt() -> str:
    return (HERE / "system_prompt.md").read_text()


def build_user_message(topic: dict, comments: list[str], links: list[dict],
                       format_prompt: str = "") -> str:
    """Assemble what we send to Claude after the system prompt."""
    lines = []
    lines.append("Write today's article on the following topic.")
    lines.append("")
    lines.append("## Topic")
    if topic.get("subreddit"):
        lines.append(f"From r/{topic['subreddit']} — community discussion of: **{topic['title']}**")
        if topic.get("selftext"):
            lines.append("")
            lines.append("Original post context:")
            lines.append("> " + topic["selftext"].replace("\n", "\n> "))
    else:
        lines.append(f"Evergreen topic: **{topic['title']}**")
    lines.append("")

    if comments:
        lines.append("## Community discussion (research only — do NOT quote directly)")
        lines.append("Use these as signal for what real people are debating. Do not name users.")
        lines.append("")
        for i, c in enumerate(comments[:8], 1):
            lines.append(f"**Comment {i}:** {c[:600]}")
            lines.append("")

    lines.append("## Available internal links")
    lines.append("Pick 2–3 of these to cite. Use the EXACT URL provided. Anchor text should be 3–5 descriptive words (not generic, not just the product name).")
    lines.append("")
    for it in links:
        kind_label = "PRODUCT" if it["kind"] == "product" else "BLOG"
        lines.append(f"- [{kind_label}] {it['title']} → {it['url']}")
    lines.append("")

    if format_prompt:
        lines.append("## Article Format")
        lines.append(format_prompt.strip())
        lines.append("")

    lines.append("## Output")
    lines.append("Return ONLY the article in markdown. No preamble. Start with `# Title` and end with the FAQ section. Follow the format template above (or the system prompt default if none given). Stay between 800–1,200 words (excluding the FAQ at the bottom).")
    return "\n".join(lines)


def call_claude(system: str, user: str, retry_feedback: str = "") -> str:
    env = load_env()
    api_key = env["ANTHROPIC_API_KEY"]
    model = env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    messages = [{"role": "user", "content": user}]
    if retry_feedback:
        messages.append({"role": "assistant", "content": "[previous attempt — see below]"})
        messages.append({"role": "user", "content":
            f"The previous attempt failed validation with these errors:\n\n{retry_feedback}\n\n"
            f"Rewrite the article fixing every error. Keep the same topic and angle. "
            f"Return ONLY the corrected markdown."})

    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "temperature": 0.7,
        "system": system,
        "messages": messages,
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read())

    if result.get("type") == "error":
        raise SystemExit(f"Claude API error: {result}")
    # Concatenate any text blocks
    text_blocks = [b["text"] for b in result.get("content", []) if b.get("type") == "text"]
    return "".join(text_blocks).strip()


def generate(topic: dict, comments: list[str], links: list[dict],
             retry_feedback: str = "", format_prompt: str = "") -> str:
    system = load_system_prompt()
    user = build_user_message(topic, comments, links, format_prompt)
    return call_claude(system, user, retry_feedback)


if __name__ == "__main__":
    # Quick smoke test against an evergreen topic
    topic = {"title": "Magnesium glycinate vs citrate vs threonate for sleep and anxiety"}
    links = [
        {"kind": "product", "title": "Sleep Aid", "url": "https://elmandrye.com/products/melatonin"},
        {"kind": "article", "title": "Snooze Blues? Tips to Help You Sleep Soundly", "url": "https://elmandrye.com/blogs/news/snooze-blues-tips-to-help-you-sleep-soundly-at-night"},
    ]
    md = generate(topic, [], links)
    print(md)
