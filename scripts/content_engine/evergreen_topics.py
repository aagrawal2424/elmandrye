"""Evergreen topic fallback list for slow Reddit days.

These are durable, high-search-volume supplement topics that work as
fallbacks when none of the day's Reddit threads cross the quality bar.

Each entry is a topic prompt the writer should treat as if it came from
Reddit — but it has no thread context, so the article leans more on
mechanism + practical guidance.
"""
from __future__ import annotations

EVERGREEN_TOPICS = [
    "Magnesium glycinate vs citrate vs threonate for sleep and anxiety",
    "Creatine monohydrate dosing for adults over 40",
    "Why most B-complex supplements use the wrong form of B12",
    "How long does it actually take ashwagandha to work for cortisol?",
    "Omega-3 EPA:DHA ratios — what the research actually shows",
    "Vitamin D3 + K2 — does the combo actually matter?",
    "L-Theanine + caffeine stack for focus without jitters",
    "Inositol for women's hormonal health and PCOS",
    "Tongkat Ali for testosterone — separating data from hype",
    "Methylated B vitamins for MTHFR variants — who actually needs them?",
    "Berberine vs metformin for blood sugar — what we know and don't",
    "Apigenin for sleep — the Huberman effect, examined",
    "Glycine before bed for body temperature regulation and sleep depth",
    "NAC vs glutathione — which form actually crosses cell membranes?",
    "Rhodiola Rosea for stress and endurance — real dose, real timing",
    "Choline sources compared: alpha-GPC, citicoline, lecithin",
    "Iron supplementation for women — why ferritin matters more than serum iron",
    "Zinc-copper ratios — the supplement mistake most people make",
    "Curcumin bioavailability — why most turmeric supplements waste your money",
    "Electrolytes beyond sodium — why magnesium and potassium ratios matter",
    "Adaptogens stacked: ashwagandha, rhodiola, holy basil for HPA axis",
    "Why probiotic CFU count matters less than strain specificity",
    "Fish oil oxidation — how to tell if your supplement has gone rancid",
    "Resveratrol vs pterostilbene for longevity pathways",
    "Spermidine — the autophagy compound nobody's talking about (yet)",
    "Saffron for mood — the unexpectedly strong clinical data",
    "Lion's Mane for nerve regeneration — what NGF actually does",
    "Glycine + magnesium for sleep architecture — synergy explained",
    "Vitamin K2 MK-4 vs MK-7 — which form for which goal?",
    "TUDCA for liver health — when bile acid supplementation makes sense",
]


def get_evergreen_pool() -> list[str]:
    """Return the full evergreen list — caller dedupes against state."""
    return list(EVERGREEN_TOPICS)
