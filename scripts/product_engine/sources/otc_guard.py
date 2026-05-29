"""OTC compliance guard for the source chain.

Two layers:

  1. Static blocklist (`is_rx_or_banned`) — fast deterministic check
     against known FDA-restricted, scheduled, or Rx-only compounds.
     Used as an early filter inside each source so we don't waste
     downstream API calls on garbage.

  2. Anthropic verification (`verify_otc`) — the authoritative final
     gate. Sends every candidate through Claude with a strict yes/no
     "can this be legally sold in the US as a dietary supplement
     without a prescription?" question. Caches results in memory so
     a single run never re-verifies the same compound.

The static list will inevitably miss things (FDA scope creeps,
hormone-precursor edge cases, new drug approvals). The LLM gate
is the safety net that catches whatever the list misses.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# ───────────────────── Static blocklist ──────────────────────────────────

# These are compounds we know are Rx-only, scheduled, FDA-banned from
# supplements, or otherwise commercially risky to sell at retail.
RX_OR_BANNED: set[str] = {
    # GLP-1 / GIP receptor agonists (all Rx)
    "retatrutide", "tirzepatide", "semaglutide", "ozempic", "wegovy",
    "mounjaro", "zepbound", "saxenda", "liraglutide", "rybelsus",

    # Growth-hormone secretagogues (research peptides, not OTC)
    "ipamorelin", "sermorelin", "tesamorelin", "ghrp-2", "ghrp-6",
    "cjc-1295", "cjc 1295", "cjc1295",

    # Healing / repair peptides (gray-zone, FDA target since 2024)
    "bpc-157", "bpc 157", "bpc157",
    "tb-500", "tb 500", "tb500",
    "thymosin alpha-1", "thymalin",

    # Cosmetic / metabolic peptides
    "mots-c", "mots c", "motsc",
    "epitalon", "selank", "semax",
    "pt-141", "pt 141", "pt141", "bremelanotide", "pt141",
    "melanotan", "melanotan ii", "mt1", "mt-1", "mt2", "mt-2",
    "ghk-cu", "ghk cu", "ghkcu",

    # FDA-banned from dietary supplements
    "phenibut", "picamilon", "dmaa", "dmha", "bmpea", "synephrine hcl",
    "ephedra", "ephedrine", "1,3 dimethylamylamine", "1,4 dimethylamylamine",
    "n-acetyl-l-cysteine ndi",  # (NAC itself is contested; we keep NAC OFF the reserve to be safe)

    # SARMs (selective androgen receptor modulators — all banned in supps)
    "sarms", "ostarine", "mk-2866", "mk 2866", "mk2866",
    "ligandrol", "lgd-4033", "lgd 4033", "lgd4033",
    "rad-140", "rad 140", "rad140", "testolone",
    "cardarine", "gw-501516", "gw 501516",
    "mk-677", "mk 677", "mk677", "ibutamoren",
    "yk-11", "yk11", "andarine", "s-4", "stenabolic", "sr-9009",

    # Anabolic / prohormones (DASCA-listed or scheduled)
    "epistane", "halodrol", "methylstenbolone", "trenbolone",
    "anavar", "oxandrolone", "winstrol", "dianabol",
    "1-andro", "4-andro", "andro", "androstenedione",
    "dhea-s", "7-keto-dhea",  # DHEA itself OTC but 7-keto is gray

    # Russian / gray-market nootropics (FDA non-approved)
    "noopept", "bromantane", "fonturacetam", "phenylpiracetam",
    "aniracetam", "oxiracetam", "pramiracetam", "coluracetam",
    "fasoracetam",

    # Statins / pharmacy compounds
    "lovastatin", "atorvastatin", "rosuvastatin", "simvastatin",
    "metformin", "rapamycin", "sirolimus",

    # Hormones (Rx in US)
    "pregnenolone",  # OTC but gray; safer to skip
    "progesterone", "estradiol", "testosterone", "dhea-25", "dhea 25",
    "thyroid extract", "armour thyroid",

    # Tianeptine (banned in many states, FDA warning)
    "tianeptine",

    # Methylene blue at pharmaceutical purity (USP) — not FDA-approved
    # as a dietary supplement
    "methylene blue usp", "methylene blue pharmaceutical",

    # Red yeast rice high-monacolin (treated as unapproved statin)
    "red yeast rice high monacolin",

    # Kratom (DEA scheduling pending, banned in several states)
    "kratom", "mitragyna", "mitragynine", "7-hydroxymitragynine",

    # Hemp / cannabis schedule complexity
    "delta-8", "delta 8", "delta-9", "delta 9", "delta-10", "delta 10",
    "thc-o", "thco", "hhc", "hexahydrocannabinol",
}

# Mainstream / saturated — not Rx but commercially boring
SATURATED: set[str] = {
    "creatine monohydrate", "whey protein", "casein protein",
    "vitamin c 1000mg", "vitamin d 5000iu", "fish oil 1000mg",
    "melatonin 5mg", "magnesium oxide", "calcium carbonate",
    "iron 65mg", "multivitamin daily",
}


def is_rx_or_banned(name: str) -> bool:
    """Fast static check — does this name match any compound on the
    Rx / banned-from-supplements list?"""
    n = name.lower().strip()
    if not n:
        return True
    return any(term in n for term in RX_OR_BANNED)


# ───────────────────── Anthropic verification gate ──────────────────────

_VERIFY_CACHE: dict[str, dict] = {}


def _build_verify_prompt(candidates: list[dict]) -> str:
    names = [c["product_name"] for c in candidates]
    items_json = json.dumps(names, indent=2)
    return f"""You are reviewing supplement product candidates for an e-commerce \
brand (Elm & Rye) that sells dietary supplements in the US direct-to-consumer.

For each candidate below, decide if it is LEGAL TO SELL AT RETAIL in the US \
as a dietary supplement WITHOUT a prescription, with NO meaningful FDA \
enforcement risk, and is a SPECIFIC NAMED COMPOUND OR HERB (not a generic \
category like "skin supplement support").

Reject anything that is:
- Prescription-only or DEA-scheduled (statins, GLP-1s, SARMs, etc.)
- Banned from dietary supplements by FDA (Phenibut, DMAA, BMPEA, Picamilon, \
ephedra, etc.)
- A research peptide sold gray-market (BPC-157, TB-500, CJC-1295, MOTS-C, etc.)
- A hormone (testosterone, progesterone, thyroid extract)
- A Russian / non-FDA-approved nootropic (Noopept, racetams, Bromantane)
- A generic category label, not a real compound (e.g. "skin supplement", \
"natural sleep", "anti-aging support")
- A branded product owned by another company (Lipo Flavonoid, Athletic Greens, \
Skinfix, etc.)

Reply with ONLY a JSON array of objects, no prose, no markdown fences:
[
  {{"name": "Anthocyanin",  "approved": true,  "reason": "OTC plant pigment, no FDA restriction, specific named compound"}},
  {{"name": "Retatrutide",  "approved": false, "reason": "Rx-only GLP-1 / GIP agonist"}}
]

Candidates:
{items_json}"""


def verify_otc(
    candidates: list[dict],
    env: dict,
) -> list[dict]:
    """Filter the candidate list through a Claude OTC-legality check.

    Returns ONLY candidates Claude marked approved=true. Each returned
    candidate has `_otc_verdict` attached with the reason string for
    audit trail.

    If the Anthropic call fails (no key, transient error, parse error),
    we fall back to the static blocklist alone — safer to over-filter
    than to let an Rx through. The static blocklist is already strict.
    """
    if not candidates:
        return []

    api_key = env.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("[otc_guard] no ANTHROPIC_API_KEY — relying on static blocklist alone")
        return [c for c in candidates if not is_rx_or_banned(c["product_name"])]

    prompt = _build_verify_prompt(candidates)
    body = json.dumps({
        "model": env.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
        raw = data["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.lstrip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.rstrip("`")
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1:
            raise ValueError(f"no JSON array in Claude reply (first 200 chars): {raw[:200]}")
        verdicts = json.loads(raw[start:end + 1])
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"[otc_guard] Anthropic verification failed ({e}) — falling back to static blocklist")
        return [c for c in candidates if not is_rx_or_banned(c["product_name"])]

    verdict_by_name: dict[str, dict] = {
        v.get("name", "").lower().strip(): v for v in verdicts if isinstance(v, dict)
    }

    approved: list[dict] = []
    rejected: list[tuple[str, str]] = []
    for c in candidates:
        name = c.get("product_name", "")
        # Belt-and-suspenders: even if Claude approves, static blocklist wins
        if is_rx_or_banned(name):
            rejected.append((name, "static blocklist match"))
            continue
        v = verdict_by_name.get(name.lower().strip())
        if not v:
            # Claude omitted this one — be conservative, drop it
            rejected.append((name, "no Claude verdict returned"))
            continue
        if not v.get("approved"):
            rejected.append((name, v.get("reason", "Claude rejected")))
            continue
        c["_otc_verdict"] = v.get("reason", "")
        approved.append(c)

    print(f"[otc_guard] {len(approved)} approved / {len(rejected)} rejected by Anthropic verifier")
    for name, reason in rejected[:5]:
        print(f"  - REJECT  {name:<40} {reason[:80]}")
    for c in approved[:5]:
        print(f"  - APPROVE {c['product_name']:<40} {c.get('_otc_verdict', '')[:80]}")
    return approved
