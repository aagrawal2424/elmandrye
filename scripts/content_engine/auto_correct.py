"""Auto-correct Claude's article output before validation.

Philosophy: don't fight Claude on stylistic quirks (em-dashes,
cliché openers, AI-tell connectives) — accept the output, normalize
it programmatically, and validate the cleaned version. Saves a
round-trip to the model, and produces deterministic output.

Mirrors the architecture of generate_image.py's provider chain:
silently work through the failure modes instead of hard-stopping.

What's NOT auto-corrected here:
  - Missing structure (H2 sections, intro, etc.) — needs Claude re-gen
  - Word count out of range — needs Claude re-gen
  - Missing citations — needs Claude re-gen
  - Bad title — needs Claude re-gen

Those flow through to the existing Claude-retry path in main.py.
This module only handles the surface-level text fixes that don't
need a re-generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# Map of (banned phrase) → (replacement). Empty string means strip the
# phrase entirely (and clean up surrounding whitespace).
#
# Case-insensitive matching; replacement preserves nothing of the
# original case (so we don't accidentally lowercase a sentence start).
# For phrases that commonly start sentences, the empty replacement
# fixes the sentence boundary in _cleanup_whitespace below.
PHRASE_FIXES: list[tuple[str, str]] = [
    # Em-dashes — replace with comma-space. Reads naturally and matches
    # the existing brand voice of short, plain sentences.
    ("—", ", "),
    ("–", "-"),  # en-dash → regular hyphen

    # Hype / cliché — strip (set empty)
    ("powerhouse", ""),
    ("game-changer", ""),
    ("game changer", ""),
    ("revolutionary", ""),
    ("supercharge", "boost"),
    ("secret weapon", ""),
    ("unleash", "release"),
    ("unlock the power", ""),
    ("harness the power", "use"),
    ("transform your", ""),
    ("breakthrough", ""),
    ("miraculous", "notable"),
    ("magical", "remarkable"),
    ("next-level", ""),
    ("robust", "strong"),
    ("comprehensive", "thorough"),
    ("pivotal", "important"),
    ("paramount", "important"),
    ("crucial role", "important role"),
    ("testament to", "evidence of"),
    ("treasure trove", "collection"),

    # Filler openers
    ("in today's fast-paced", ""),
    ("in the world of wellness", ""),
    ("embark on a journey", "start"),
    ("tap into the power", "use"),
    ("for centuries, traditional cultures", ""),
    ("since ancient times", "historically"),
    ("throughout history", "historically"),

    # Meta-commentary
    ("in conclusion", ""),
    ("let's dive in", ""),
    ("as previously mentioned", "as noted"),
    ("without further ado", ""),
    ("it's important to note", ""),
    ("without a doubt", ""),
    ("in this article we will", ""),
    ("in this article, we will", ""),

    # AI-tell verbs/connectives
    ("delve into", "examine"),
    ("dive into", "look at"),
    ("dive deeper", "look closer"),
    ("navigate the complexities", "work through"),
    ("navigate the", "work through"),
    ("in the realm of", "in"),
    ("the world of supplements", "supplements"),
    ("elevate your", "improve your"),
    ("leverage the", "use the"),
    ("in essence", ""),
    ("ultimately,", ""),

    # AI-tell nouns
    ("tapestry", "mix"),
    ("myriad of", "many"),
    ("plethora of", "many"),
    ("the landscape of", ""),
    ("ecosystem of", ""),
    ("underscore", "show"),

    # Connectives that scream LLM
    (" moreover,", "."),
    (" furthermore,", "."),

    # Lazy phrasing
    ("studies have shown", "research found"),
    ("research suggests that", "research found"),
]


@dataclass
class CorrectionResult:
    """Result of running auto-correct on an article body."""

    corrected: str
    corrections: list[tuple[str, str, int]] = field(default_factory=list)
    """List of (banned_phrase, replacement, count). count = number of
    occurrences replaced. Used by main.py to log to run_log for audit."""

    @property
    def total_replacements(self) -> int:
        return sum(c[2] for c in self.corrections)

    @property
    def ok(self) -> bool:
        """Always returns True — auto-correct doesn't fail, it just
        normalizes whatever it sees. The downstream validator decides
        if the corrected output is acceptable."""
        return True


def correct_banned_phrases(md: str) -> CorrectionResult:
    """Replace every banned phrase in the markdown with its
    auto-correction. Returns the corrected body + a log of what was
    replaced.

    The replacements use case-insensitive regex matching. For empty
    replacements, the phrase is removed AND surrounding whitespace
    is cleaned up so we don't leave double spaces or comma collisions.
    """
    out = md
    corrections: list[tuple[str, str, int]] = []

    for banned, replacement in PHRASE_FIXES:
        # Word-boundary-aware case-insensitive replace. We don't use
        # \b on both sides because some banned phrases contain
        # punctuation/spaces. Simple case-insensitive find suffices.
        pattern = re.compile(re.escape(banned), re.IGNORECASE)
        matches = pattern.findall(out)
        count = len(matches)
        if count == 0:
            continue
        out = pattern.sub(replacement, out)
        corrections.append((banned, replacement, count))

    out = _cleanup_whitespace(out)
    return CorrectionResult(corrected=out, corrections=corrections)


def _cleanup_whitespace(md: str) -> str:
    """After stripping banned phrases, fix the artifacts they leave:
    double spaces, comma collisions, leading/trailing space on lines.

    All regexes are NEWLINE-PRESERVING — `[^\\S\\n]` matches horizontal
    whitespace only, so we never accidentally merge two paragraphs into
    one when collapsing the space left by a stripped phrase."""
    # ", ." → "."   (em-dash replacement at end of clause; horizontal ws only)
    md = re.sub(r",[^\S\n]*\.", ".", md)
    # ", ," → ","   (em-dash replacement mid-clause meeting an existing comma)
    md = re.sub(r",[^\S\n]*,", ",", md)
    # ". , X" → ". X"   (stripped phrase left a stray leading comma after a period;
    # MUST NOT cross newlines or we'd merge separate paragraphs)
    md = re.sub(r"\.[^\S\n]*,[^\S\n]*", ". ", md)
    # Multiple horizontal spaces → single space (preserves \n)
    md = re.sub(r"[^\S\n]{2,}", " ", md)
    # Horizontal space before punctuation → punctuation (preserves \n)
    md = re.sub(r"[^\S\n]+([,\.;:!?])", r"\1", md)
    # Empty parens / brackets left over from strips
    md = re.sub(r"\(\s*\)", "", md)
    md = re.sub(r"\[\s*\]", "", md)
    # Per-line: strip leading whitespace + leading commas (artifact of
    # stripping a sentence-opening phrase like "In conclusion,")
    lines = md.split("\n")
    out_lines = []
    for line in lines:
        if line.startswith(("    ", "\t", "- ", "* ", ">")):
            # Preserve markdown indentation tokens
            out_lines.append(line)
        else:
            stripped = line.lstrip()
            # Strip leading comma+whitespace artifacts like ", both matter."
            stripped = re.sub(r"^,\s*", "", stripped)
            out_lines.append(stripped)
    md = "\n".join(out_lines)
    return md
