"""Validation gate for generated articles.

Articles must pass every check below to be published live. If any check
fails, the orchestrator retries Claude once with the failure list as
feedback. If still fails, we don't publish — we email the user instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


BANNED_PHRASES = [
    # Em dash — banned entirely; use comma/period/colon instead
    "—",
    # Hype / cliché
    "powerhouse", "game-changer", "game changer", "revolutionary", "supercharge",
    "secret weapon", "unlock the power", "unleash", "harness the power",
    "transform your", "breakthrough", "miraculous", "magical", "next-level",
    "robust", "comprehensive", "pivotal", "paramount", "crucial role",
    "testament to", "treasure trove",
    # Filler openers
    "in today's fast-paced", "in the world of wellness", "embark on a journey",
    "tap into the power", "for centuries, traditional cultures",
    "since ancient times", "throughout history",
    # Meta-commentary
    "in conclusion", "let's dive in", "as previously mentioned",
    "without further ado", "it's important to note", "without a doubt",
    "in this article we will", "in this article, we will",
    # AI tells — verbs/connectives
    "delve into", "dive into", "dive deeper", "navigate the complexities",
    "navigate the", "in the realm of", "the world of supplements",
    "elevate your", "leverage the", "in essence", "ultimately,",
    # AI tells — nouns
    "tapestry", "myriad of", "plethora of", "the landscape of",
    "ecosystem of", "underscore",
    # Connectives that scream LLM
    " moreover,", " furthermore,",
    # Lazy phrasing
    "studies have shown", "research suggests that",  # Be specific, cite a number.
]

# High-trust external sources for citation validation.
AUTHORITATIVE_DOMAINS = (
    "pubmed.ncbi.nlm.nih.gov",
    "ncbi.nlm.nih.gov",
    "ods.od.nih.gov",
    "nih.gov",
    "examine.com",
    "cochrane.org",
    "cochranelibrary.com",
    "jamanetwork.com",
    "nejm.org",
    "thelancet.com",
    "bmj.com",
    "nature.com",
    "sciencedirect.com",
    "cdc.gov",
    "fda.gov",
)


REQUIRED_SECTIONS = {
    "hook": ["##"],            # the first H2 — name varies, just check 1+ H2 exists early
    "table": True,             # at least one markdown table
    "bottom_line": ["bottom line", "the takeaway", "what to do"],
    "faq": ["faq", "questions", "common questions"],
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def strip_faq(md: str) -> str:
    """Return everything before the FAQ section (for word-count purposes)."""
    m = re.search(r'^##\s+(faq|frequently\s+asked|common\s+questions)\b', md, re.MULTILINE | re.IGNORECASE)
    return md[: m.start()] if m else md


def word_count(text: str) -> int:
    # strip markdown syntax for accurate count
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)  # code blocks
    text = re.sub(r'\|.*?\|', '', text)  # table cells (don't count)
    text = re.sub(r'[#*_`>\-\|]', ' ', text)
    return len([w for w in text.split() if w.strip()])


def find_tables(md: str) -> list[str]:
    """A markdown table needs a header row of pipes AND a separator row like |---|---|."""
    tables = []
    lines = md.split('\n')
    for i, line in enumerate(lines[:-1]):
        if line.count('|') >= 2 and re.match(r'^\s*\|?[\s\-:\|]+\|?\s*$', lines[i + 1]) and '-' in lines[i + 1]:
            tables.append(line)
    return tables


def section_sizes(md: str) -> dict:
    """Return {section_heading: word_count}."""
    sections = re.split(r'^(##+ .+)$', md, flags=re.MULTILINE)
    out = {}
    for i in range(1, len(sections), 2):
        heading = sections[i].strip()
        body = sections[i + 1] if i + 1 < len(sections) else ""
        out[heading] = word_count(body)
    return out


def has_required_section(md: str, keywords: list) -> bool:
    headings = re.findall(r'^##+\s+(.+)$', md, re.MULTILINE)
    return any(any(k.lower() in h.lower() for k in keywords) for h in headings)


def first_n_words(md: str, n: int = 250) -> str:
    text = re.sub(r'[#*_`>\-]', ' ', md)
    return ' '.join(text.split()[:n])


def has_proof_objects(text_window: str) -> bool:
    """Stat or dosage near the top — loose check: any digit+unit pattern."""
    return bool(re.search(r'\b\d{1,4}\s*(mg|mcg|iu|g|ml|%|days?|weeks?|months?|hours?)\b', text_window, re.IGNORECASE))


def faq_question_count(md: str) -> int:
    faq_start = re.search(r'^##\s+(faq|frequently\s+asked|common\s+questions)\b', md, re.MULTILINE | re.IGNORECASE)
    if not faq_start:
        return 0
    faq_body = md[faq_start.end():]
    return len(re.findall(r'^###\s+', faq_body, re.MULTILINE))


def validate(md: str) -> ValidationResult:
    result = ValidationResult(ok=True)
    article_body = strip_faq(md)

    # 1. Word count (excluding FAQ)
    wc = word_count(article_body)
    result.stats["word_count_main"] = wc
    result.stats["word_count_total"] = word_count(md)
    if wc < 800:
        result.errors.append(f"Word count {wc} is below 800 minimum (excluding FAQ).")
    elif wc > 1200:
        result.errors.append(f"Word count {wc} exceeds 1200 maximum (excluding FAQ). Trim.")

    # 2. Markdown table required
    tables = find_tables(md)
    result.stats["table_count"] = len(tables)
    if len(tables) == 0:
        result.errors.append("No markdown table found. At least one required.")

    # 3. Banned phrases
    md_lower = md.lower()
    hits = [p for p in BANNED_PHRASES if p in md_lower]
    if hits:
        result.errors.append(f"Banned phrases found: {hits}. Rewrite without them.")

    # 4. Required sections
    if not has_required_section(md, REQUIRED_SECTIONS["bottom_line"]):
        result.errors.append("Missing 'Bottom Line' (or 'Takeaway') section.")
    if not has_required_section(md, REQUIRED_SECTIONS["faq"]):
        result.errors.append("Missing FAQ section.")

    # 5. FAQ question count
    faq_q = faq_question_count(md)
    result.stats["faq_questions"] = faq_q
    if faq_q < 3:
        result.errors.append(f"FAQ has only {faq_q} questions; need at least 3.")

    # 6. Section size cap
    sec_sizes = section_sizes(article_body)
    result.stats["section_sizes"] = sec_sizes
    for heading, sz in sec_sizes.items():
        if sz > 400:
            result.errors.append(f"Section '{heading}' is {sz} words; max 400.")

    # 7. Proof objects in first 250 words
    if not has_proof_objects(first_n_words(article_body, 250)):
        result.warnings.append("First 250 words lack a specific dosage/stat. Consider adding one for GEO.")

    # 8. H1 title present
    if not re.search(r'^#\s+', md, re.MULTILINE):
        result.errors.append("Missing H1 title (article must start with '# Title').")

    # 9. Has at least 3 internal-link-style markdown links
    md_links = re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', md)
    internal = [l for l in md_links if "elmandrye.com" in l[1]]
    result.stats["internal_link_count"] = len(internal)
    if len(internal) < 2:
        result.errors.append(f"Only {len(internal)} internal elmandrye.com links found; need 2–3.")
    elif len(internal) > 5:
        result.warnings.append(f"{len(internal)} internal links may feel spammy; consider trimming to 2–3.")

    # 10. At least one external authoritative citation (PubMed/NIH/Examine/.gov/.edu/etc.)
    def _is_authoritative(url: str) -> bool:
        u = url.lower()
        if any(d in u for d in AUTHORITATIVE_DOMAINS):
            return True
        # .gov or .edu domain (allow subdomains/paths)
        return bool(re.search(r'://[^/]+\.(gov|edu)(/|$)', u))

    authoritative = [l for l in md_links if _is_authoritative(l[1]) and "elmandrye.com" not in l[1]]
    result.stats["authoritative_citation_count"] = len(authoritative)
    if len(authoritative) < 1:
        result.errors.append(
            "No external authoritative citation found. Add at least one link to PubMed, "
            "NIH (ods.od.nih.gov), Examine.com, Cochrane, or a .gov/.edu source."
        )

    # 11. "Our take:" first-party block — exactly one short paragraph
    our_take_matches = re.findall(r'(?im)^\s*(?:[\*_]{0,2})(my|our) take[\*_]{0,2}\s*:', md)
    result.stats["our_take_blocks"] = len(our_take_matches)
    if len(our_take_matches) == 0:
        result.errors.append(
            "Missing required 'My take:' block — one short paragraph naming a specific "
            "Elm & Rye sourcing, dosing, or formulation choice with the reason."
        )
    elif len(our_take_matches) > 1:
        result.warnings.append(
            f"{len(our_take_matches)} 'Our take:' blocks found; convention is exactly one."
        )

    result.ok = len(result.errors) == 0
    return result


if __name__ == "__main__":
    import sys
    md = sys.stdin.read()
    r = validate(md)
    print("OK:" if r.ok else "FAILED:")
    for e in r.errors:
        print(f"  ERROR: {e}")
    for w in r.warnings:
        print(f"  WARN:  {w}")
    print(f"\nStats: {r.stats}")
