"""Publish a generated article to Shopify's News blog.

Converts markdown to HTML (simple converter), creates the article via
articleCreate, attaches the hero image, sets the author, publishes live.

Per user direction: publishes LIVE, not as a draft.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gql import call  # noqa: E402
from chart_renderer import preprocess_charts, inject_charts  # noqa: E402
from get_token import get_token, load_env  # noqa: E402

BLOG_ID = "gid://shopify/Blog/74623221917"  # /blogs/news
BLOG_HANDLE = "news"
BLOG_NUMERIC_ID = "74623221917"
API_VERSION = "2025-01"


def _token() -> str:
    return get_token()


def _admin_host() -> str:
    return load_env().get("SHOPIFY_STORE", "elmandrye.myshopify.com")
# Combined editor + team byline. Removes the sole-human-author impersonation
# signal (Google AI disclosure / E-E-A-T) while keeping AJ as editor-in-chief.
AUTHOR_NAME = "AJ Agrawal"
AUTHOR_PERSON = "AJ Agrawal"
AUTHOR_PERSON_URL = "https://elmandrye.com/pages/aj-agrawal"
STORE = "elmandrye.com"
ORG_LOGO_URL = "https://elmandrye.com/cdn/shop/files/elmandrye-logo.png"

MEDICAL_DISCLAIMER_HTML = (
    '<aside class="er-disclaimer" '
    'style="margin-top:2rem;padding:1rem 1.25rem;border-left:3px solid #c9b27a;'
    'background:#faf7f0;font-size:0.9rem;line-height:1.5;color:#444;">'
    '<strong>Educational use only.</strong> This article is for general information and '
    'does not constitute medical advice, diagnosis, or treatment. Supplements are not '
    'evaluated by the FDA to diagnose, treat, cure, or prevent any disease. Talk to a '
    'qualified healthcare provider before starting any new supplement, especially if you '
    'are pregnant, nursing, taking medication, or managing a health condition.'
    '</aside>'
)


# --- Minimal Markdown → HTML converter -----------------------------------
# Handles what Claude actually emits per our template: headings, paragraphs,
# bullets, numbered lists, bold/italic, tables, links, inline code. Not a
# full CommonMark — but good enough for the article shapes we generate.

def md_to_html(md: str) -> str:
    lines = md.split("\n")
    html_parts: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i].rstrip()

        # blank line
        if not line.strip():
            i += 1
            continue

        # headings — skip h1 (Shopify theme renders article title as h1 already)
        m = re.match(r'^(#{1,6})\s+(.+)$', line)
        if m:
            level = len(m.group(1))
            if level > 1:
                html_parts.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # tables — needs current line of pipes + next line of |---|
        if "|" in line and i + 1 < n and re.match(r'^\s*\|?[\s\-:\|]+\|?\s*$', lines[i + 1]) and '-' in lines[i + 1]:
            table_lines = [line]
            i += 2  # skip header line + separator line
            while i < n and "|" in lines[i] and lines[i].strip():
                table_lines.append(lines[i])
                i += 1
            html_parts.append(render_table(table_lines))
            continue

        # unordered list
        if re.match(r'^[\-\*]\s+', line):
            list_items = []
            while i < n and re.match(r'^[\-\*]\s+', lines[i]):
                list_items.append(re.sub(r'^[\-\*]\s+', '', lines[i]))
                i += 1
            html_parts.append("<ul>" + "".join(f"<li>{inline(it)}</li>" for it in list_items) + "</ul>")
            continue

        # ordered list
        if re.match(r'^\d+\.\s+', line):
            list_items = []
            while i < n and re.match(r'^\d+\.\s+', lines[i]):
                list_items.append(re.sub(r'^\d+\.\s+', '', lines[i]))
                i += 1
            html_parts.append("<ol>" + "".join(f"<li>{inline(it)}</li>" for it in list_items) + "</ol>")
            continue

        # blockquote
        if line.startswith(">"):
            quote_lines = []
            while i < n and lines[i].startswith(">"):
                quote_lines.append(lines[i].lstrip("> "))
                i += 1
            html_parts.append("<blockquote><p>" + inline(" ".join(quote_lines)) + "</p></blockquote>")
            continue

        # paragraph — gather contiguous non-blank, non-special lines
        para_lines = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(r'^(#{1,6}\s|>|[\-\*]\s|\d+\.\s|\|)', lines[i]):
            para_lines.append(lines[i].rstrip())
            i += 1
        html_parts.append("<p>" + inline(" ".join(para_lines)) + "</p>")

    return "\n".join(html_parts)


def inline(text: str) -> str:
    """Handle inline markdown: bold, italic, code, links."""
    # links: [anchor](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # bold: **x** or __x__
    text = re.sub(r'\*\*([^\*]+)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__([^_]+)__', r'<strong>\1</strong>', text)
    # italic: *x* or _x_ (avoid matching inside words)
    text = re.sub(r'(?<!\w)\*([^\*\n]+)\*(?!\w)', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)', r'<em>\1</em>', text)
    # inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return text


def render_table(lines: list[str]) -> str:
    header = [c.strip() for c in lines[0].strip().strip('|').split('|')]
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        rows.append(cells)
    th = "".join(f"<th>{inline(h)}</th>" for h in header)
    trs = []
    for r in rows:
        tds = "".join(f"<td>{inline(c)}</td>" for c in r)
        trs.append(f"<tr>{tds}</tr>")
    return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"


# --- Article extraction ---------------------------------------------------

def extract_title(md: str) -> str:
    m = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
    return m.group(1).strip() if m else "Untitled"


def extract_summary(md: str, max_chars: int = 240) -> str:
    """Use the first paragraph after the H1 as a summary/excerpt."""
    body = re.sub(r'^#\s+.+\n', '', md, count=1)
    body = re.sub(r'^##+\s+.+\n', '', body, count=1, flags=re.MULTILINE)
    # first prose paragraph
    para = ""
    for line in body.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("|") and not line.startswith("-"):
            para = line
            break
    para = re.sub(r'[*_`\[\]\(\)]', '', para)
    return para[:max_chars]


def extract_faq_pairs(md: str) -> list[tuple[str, str]]:
    """Return [(question, answer_text)] from the FAQ section for FAQPage JSON-LD."""
    faq_start = re.search(r'^##\s+(faq|frequently\s+asked|common\s+questions)\b',
                          md, re.MULTILINE | re.IGNORECASE)
    if not faq_start:
        return []
    body = md[faq_start.end():]
    # Split on H3 questions; pairs are [question, answer_block, question, answer_block, ...]
    parts = re.split(r'^###\s+(.+)$', body, flags=re.MULTILINE)
    pairs: list[tuple[str, str]] = []
    for i in range(1, len(parts) - 1, 2):
        q = parts[i].strip()
        a_raw = parts[i + 1].strip()
        # strip markdown emphasis, links, code for clean schema text
        a_clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', a_raw)
        a_clean = re.sub(r'[*_`]+', '', a_clean)
        a_clean = re.sub(r'\s+', ' ', a_clean).strip()
        if q and a_clean:
            pairs.append((q, a_clean))
    return pairs


def build_jsonld(title: str, summary: str, hero_image_url: str,
                 article_url: str, faq_pairs: list[tuple[str, str]]) -> str:
    """Return one or two <script type='application/ld+json'> blocks (Article + optional FAQPage)."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    article_obj: dict = {
        "@context": "https://schema.org",
        "@type": "MedicalWebPage",
        "headline": title,
        "description": summary,
        "datePublished": now_iso,
        "dateModified": now_iso,
        "author": {
            "@type": "Person",
            "name": AUTHOR_PERSON,
            "url": AUTHOR_PERSON_URL,
        },
        "publisher": {
            "@type": "Organization",
            "name": "Elm & Rye",
            "logo": {"@type": "ImageObject", "url": ORG_LOGO_URL},
        },
        "mainEntityOfPage": {"@type": "WebPage", "@id": article_url},
    }
    if hero_image_url:
        article_obj["image"] = hero_image_url

    def _jsonld_tag(obj: dict) -> str:
        # Escape any `</` inside the serialized JSON so a stray sequence in the
        # payload can't break out of the surrounding <script> tag.
        payload = json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")
        return '<script type="application/ld+json">' + payload + '</script>'

    blocks = [_jsonld_tag(article_obj)]

    if faq_pairs:
        faq_obj = {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": q,
                    "acceptedAnswer": {"@type": "Answer", "text": a},
                }
                for q, a in faq_pairs
            ],
        }
        blocks.append(_jsonld_tag(faq_obj))

    return "\n".join(blocks)


def extract_tags(_md: str, _topic: dict) -> list[str]:
    return []


# --- Shopify publish -------------------------------------------------------

MUTATION = """
mutation CreateArticle($article: ArticleCreateInput!) {
  articleCreate(article: $article) {
    article {
      id title handle isPublished publishedAt
      blog { id handle }
      author { name }
    }
    userErrors { field message }
  }
}
"""


def build_article_body_html(
    md: str,
    hero_image_url: str,
    handle_hint: str,
    video_block_html: str = "",
) -> tuple[str, str]:
    """Return (body_html, summary). Body includes JSON-LD + disclaimer.

    If video_block_html is non-empty, it's injected after the article's
    first paragraph so the player is visible above the fold while the
    opening hook still reads naturally.
    """
    title = extract_title(md)
    summary = extract_summary(md)
    md_processed, rendered_charts = preprocess_charts(md)
    article_html = inject_charts(md_to_html(md_processed), rendered_charts)
    if video_block_html:
        # Insert after the first </p> so the lead paragraph reads first
        m = re.search(r"</p>", article_html, re.IGNORECASE)
        if m:
            idx = m.end()
            article_html = (
                article_html[:idx]
                + "\n\n" + video_block_html + "\n\n"
                + article_html[idx:]
            )
        else:
            article_html = video_block_html + "\n\n" + article_html
    faq_pairs = extract_faq_pairs(md)
    article_url = f"https://{STORE}/blogs/{BLOG_HANDLE}/{handle_hint}"
    jsonld = build_jsonld(title, summary, hero_image_url, article_url, faq_pairs)
    body = "\n".join([article_html, MEDICAL_DISCLAIMER_HTML, jsonld])
    return body, summary


def slugify(title: str) -> str:
    s = re.sub(r'[^a-zA-Z0-9]+', '-', title.lower()).strip('-')
    return s[:80] or "article"


class DuplicateArticleError(Exception):
    """Raised when an article with the same slug already exists on the blog.

    Single source of truth for dedup is the Shopify blog itself — state.json
    can lose entries across repo migrations or be out of sync.

    Carries the colliding handle so main.py's skip handler can update
    state.json AND link the existing live URL in the alert email.
    """

    def __init__(self, message: str, *, predicted_slug: str = "",
                 colliding_handle: str = ""):
        super().__init__(message)
        self.predicted_slug = predicted_slug
        self.colliding_handle = colliding_handle

    @property
    def existing_url(self) -> str:
        if not self.colliding_handle:
            return ""
        return f"https://elmandrye.com/blogs/news/{self.colliding_handle}"


def _existing_article_handles() -> set[str]:
    """Pull every handle (slug) from the elmandrye blog.

    Shopify caps a single GET at 250 articles; we paginate via Link header
    until empty. ~250 articles per call is plenty for current volume.
    """
    handles: set[str] = set()
    cursor: str | None = None
    while True:
        params = ["limit=250", "fields=handle"]
        if cursor:
            params.append(f"page_info={cursor}")
        url = (
            f"https://{_admin_host()}/admin/api/{API_VERSION}/blogs/"
            f"{BLOG_NUMERIC_ID}/articles.json?{'&'.join(params)}"
        )
        req = urllib.request.Request(
            url, headers={"X-Shopify-Access-Token": _token()},
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                link = r.headers.get("Link", "")
                data = json.loads(r.read())
        except Exception as e:
            print(f"[publish] Could not fetch existing handles ({e}); "
                  f"proceeding without Shopify-side dedup.")
            return handles
        for a in data.get("articles", []):
            h = a.get("handle")
            if h:
                handles.add(h)
        next_cursor: str | None = None
        for part in link.split(","):
            part = part.strip()
            if 'rel="next"' in part and "page_info=" in part:
                start = part.index("page_info=") + len("page_info=")
                end = part.index(">", start)
                next_cursor = part[start:end]
                break
        if not next_cursor:
            break
        cursor = next_cursor
    return handles


def _slug_matches_existing(predicted_slug: str, existing: set[str]) -> str | None:
    """Return the matching existing handle if our predicted slug would collide.

    Shopify accepts a slug, then if it's already taken appends `-1`, `-2`, etc.
    We treat both the exact slug AND the `<slug>-N` variants as collisions.
    """
    if predicted_slug in existing:
        return predicted_slug
    # Catch the case where the original (without suffix) exists but our run
    # would also be the original; or where a `-1` exists from a prior dup.
    for h in existing:
        if h == predicted_slug or re.fullmatch(
            re.escape(predicted_slug) + r"-\d+", h,
        ):
            return h
    return None


def publish_article(
    md: str,
    topic: dict,
    hero_image_url: str = "",
    publish_live: bool = True,
    video_block_html: str = "",
) -> dict:
    title = extract_title(md)
    tags = extract_tags(md, topic)
    predicted_slug = slugify(title)

    # ── Shopify-side dedup ───────────────────────────────────────────────────
    # state.json is a soft dedup; the blog itself is the source of truth.
    # If a collision exists, abort BEFORE creating an article. The caller
    # (main.py) catches this and logs the skip without touching state.
    existing = _existing_article_handles()
    collision = _slug_matches_existing(predicted_slug, existing)
    if collision is not None:
        raise DuplicateArticleError(
            f"Predicted slug '{predicted_slug}' would collide with existing "
            f"article '{collision}' on the blog. Skipping publish to avoid "
            f"creating a '<slug>-N' duplicate.",
            predicted_slug=predicted_slug,
            colliding_handle=collision,
        )

    # Shopify generates the final handle, but we need a stable URL inside the JSON-LD
    # before publish. The slug we predict here will match Shopify's default in nearly
    # all cases (lowercase, hyphenated). If it differs, the JSON-LD @id is still a
    # valid URL on the store.
    body, summary = build_article_body_html(
        md, hero_image_url, predicted_slug, video_block_html=video_block_html,
    )

    # ── Defensive guard #1: never publish an article whose body starts
    #    with <h1>. Shopify's theme renders the article title as h1
    #    automatically — a body h1 makes the title appear twice on the
    #    page. md_to_html() already skips h1 from the source markdown,
    #    but this guard catches any path that somehow snuck h1 into the
    #    body (off-path publishes, manual injections, future regressions).
    body = re.sub(r"^\s*<h1[^>]*>[^<]*</h1>\s*", "", body, count=1, flags=re.IGNORECASE)

    # ── Defensive guard #2: refuse to publish without a hero image.
    #    Imageless articles are unacceptable per the workflow-fix memory
    #    (see also: HeroPipelineFailure raised from generate_hero()).
    #    main.py catches that exception upstream and aborts; this guard
    #    is the belt-and-suspenders at the publish layer itself.
    if not hero_image_url:
        raise SystemExit(
            f"publish_article: refusing to publish '{title}' without a hero image. "
            f"Caller must pass a non-empty hero_image_url; see generate_hero()."
        )

    # Engine-provenance metafield — lets the workflow-build articles/create
    # webhook validator tell apart "engine-created article" (trust the body)
    # from "manual paste via Shopify Admin UI" (run quality checks + alert).
    # GH_RUN_ID is set by the cron's runner; falls back to "local-{pid}"
    # for ad-hoc runs from a developer's laptop so we can still trace.
    engine_run_id = os.environ.get("GITHUB_RUN_ID") or f"local-{os.getpid()}-{int(time.time())}"

    article_input: dict = {
        "blogId": BLOG_ID,
        "title": title,
        "body": body,
        "summary": summary,
        "author": {"name": AUTHOR_NAME},
        "tags": tags,
        "isPublished": publish_live,
        "image": {"url": hero_image_url, "altText": title},
        "metafields": [
            {
                "namespace": "engine",
                "key": "run_id",
                "type": "single_line_text_field",
                "value": engine_run_id,
            }
        ],
    }

    result = call(MUTATION, {"article": article_input})
    if result.get("errors"):
        raise SystemExit(f"articleCreate GraphQL errors: {result['errors']}")
    payload = result["data"]["articleCreate"]
    if payload["userErrors"]:
        raise SystemExit(f"articleCreate userErrors: {payload['userErrors']}")
    article = payload["article"]
    article["live_url"] = f"https://{STORE}/blogs/{article['blog']['handle']}/{article['handle']}"
    return article


if __name__ == "__main__":
    # smoke test of the markdown converter
    sample = sys.stdin.read()
    print(md_to_html(sample))
