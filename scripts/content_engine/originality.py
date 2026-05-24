"""Originality guard.

Before publish, compare the new article's text against fingerprints of
every article published in the last 90 days. Reject if Jaccard similarity
of 5-gram shingles exceeds the threshold against any prior article.

This catches the scaled-content failure mode where Claude regresses to
the same talking points across many topics — and protects us from the
exact pattern Google's spam policy targets.

Footprint per article in state.json: 256 ints (MinHash signature). At
90 articles in the rolling window, the storage cost is trivial.
"""
from __future__ import annotations

import hashlib
import re

SIGNATURE_SIZE = 256
SHINGLE_N = 5
SIMILARITY_THRESHOLD = 0.30


def _tokenize(text: str) -> list[str]:
    # Strip markdown / HTML noise, lowercase, keep only word-ish tokens.
    text = re.sub(r'<[^>]+>', ' ', text)            # html tags
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)  # code blocks
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)     # markdown links → anchor text
    text = re.sub(r'[#*_`>\-\|]+', ' ', text)
    return [w for w in re.findall(r'[a-z0-9]+', text.lower()) if len(w) > 1]


def _shingles(tokens: list[str], n: int = SHINGLE_N) -> set[str]:
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def signature(text: str) -> list[int]:
    """Return a SIGNATURE_SIZE-element MinHash-style signature for the article body."""
    shingles = _shingles(_tokenize(text))
    if not shingles:
        return []
    # SIGNATURE_SIZE independent hash functions via salt prefixes.
    sig: list[int] = []
    for k in range(SIGNATURE_SIZE):
        salt = str(k).encode()
        min_h = None
        for s in shingles:
            h = int.from_bytes(
                hashlib.blake2b(salt + s.encode(), digest_size=8).digest(),
                "big",
            )
            if min_h is None or h < min_h:
                min_h = h
        sig.append(min_h if min_h is not None else 0)
    return sig


def jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a or not sig_b:
        return 0.0
    n = min(len(sig_a), len(sig_b))
    if n == 0:
        return 0.0
    matches = sum(1 for i in range(n) if sig_a[i] == sig_b[i])
    return matches / n


def find_most_similar(new_sig: list[int], prior_entries: list[dict]) -> tuple[float, dict | None]:
    """Return (max_similarity, matching_entry). prior_entries each have a 'signature' key."""
    best = (0.0, None)
    for e in prior_entries:
        s = e.get("signature") or []
        sim = jaccard(new_sig, s)
        if sim > best[0]:
            best = (sim, e)
    return best


if __name__ == "__main__":
    import sys
    txt = sys.stdin.read()
    sig = signature(txt)
    print(f"signature_len={len(sig)} first5={sig[:5]}")
