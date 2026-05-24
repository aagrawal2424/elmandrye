"""Article format templates for structural variety.

Rotating format prevents Google's spam classifier from fingerprinting a
single article template across the entire blog.
"""
import random


standard = {
    "id": "standard",
    "title_pattern": "default",
    "structure_prompt": "",
}

versus = {
    "id": "versus",
    "title_pattern": "[Compound A] vs [Compound B]...",
    "structure_prompt": """
Use this article structure instead of the standard template:

# [Compound A] vs [Compound B]: The Clinical Breakdown

## What They Have in Common
[1-2 paragraphs on shared mechanisms or goals]

## Where They Diverge
[Comparison table required here — same category, different columns]

## Who Should Pick Which
[Bullet points — profile-based recommendation]

## The Reality Check
[Limits, side effects, who should avoid either]

## The Bottom Line
[2 sentences]

## FAQ
[3 questions]

Title must follow the pattern "[A] vs [B]..." Keep same word count and validation rules.
""",
}

protocol = {
    "id": "protocol",
    "title_pattern": "The [X]-Week [Topic] Protocol: What to Take and When",
    "structure_prompt": """
Use this article structure instead of the standard template:

# The [X]-Week [Topic] Protocol: What to Take and When

## Why Timing and Sequencing Matter
[1-2 paragraphs on why this isn't just "take supplement X"]

## The Protocol
[Required markdown table: Phase/Week | Compound | Dose | Timing | Goal]

## What to Expect Week by Week
[H3 subheadings per phase. Max 150 words each.]

## What Can Go Wrong
[Common mistakes, side effects, contraindications]

## The Bottom Line
[2 sentences]

## FAQ
[3 questions]

Title must frame a clear time period (e.g. "4-Week", "8-Week", "90-Day"). Keep same word count and validation rules.
""",
}

deep_dive = {
    "id": "deep_dive",
    "title_pattern": "[Compound]: How It Actually Works and What the Evidence Says",
    "structure_prompt": """
Use this article structure instead of the standard template:

# [Compound]: How It Actually Works and What the Evidence Says

## The Mechanism (Skip This If You Just Want the Dose)
[2-3 paragraphs going deep on the biochemistry — be specific about pathways]

## What the Clinical Evidence Actually Shows
[Required table: Study Type | Sample Size | Finding | Dose Used]

## How to Take It Correctly
[Dosing, timing, form selection — bullet points]

## The Honest Limitations
[What the research can't prove yet, individual variance, contraindications]

## The Bottom Line
[2 sentences]

## FAQ
[3 questions]

Make the mechanism section the strongest part — this is for readers who want the full picture. Keep same word count and validation rules.
""",
}


FORMATS = [standard, versus, protocol, deep_dive]


def pick_format(state: dict) -> dict:
    """Rotate through formats based on a counter stored in state.
    Falls back to random if counter missing."""
    idx = state.get("format_index", 0)
    fmt = FORMATS[idx % len(FORMATS)]
    state["format_index"] = idx + 1
    return fmt
