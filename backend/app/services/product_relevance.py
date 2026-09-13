"""Query-relative product relevance tiers.

Algolia's textual ranking cannot tell a core product from an accessory or a
sibling product: "Boat Rockerz 210" (earphones under the "Audio & Speakers"
category label) and "Boat Stone 500 Portable Speaker" are identical matches
for the query "boat speakers". This module compares the *head noun* of the
user's query against each result's product title so re-ranking can put core
products first without shrinking the result set.

Tiers (lower is better):
  0 - the title's head noun IS the query's head noun ("... bluetooth speaker")
  1 - the title contains the query noun but as a modifier ("ceiling fan regulator")
  2 - the title does not contain the query noun at all (matched via category
      label, vendor name, or brand only - e.g. earphones for "boat speakers")

When ``descriptors`` are present (e.g. product="light", descriptors=["operation
theatre"]), tier 0 additionally requires at least one descriptor token in the
title. A head-noun match alone ("Rabbit Night Light" for query "light") lands
in tier 1 so optionalWords boosts from Algolia are not undone by re-ranking.
"""

from __future__ import annotations

import re

from app.services import query_normalization

_SPLIT_RE = re.compile(r"[^a-z0-9]+")

# "Teleprompter FOR tablets" / "fan WITH led light": the words after the cut
# describe compatibility or bundled extras, never the product itself.
_CUT_PHRASES = (" for ", " with ")

# Trailing tokens that are marketing/pack suffixes, not the product head noun
# ("rockerz 255 PRO", "regulator SINGLE", "fan DLX MODEL").
_GENERIC_TAIL = {
    "pro", "plus", "max", "mini", "lite", "ultra", "prime", "deluxe", "dlx",
    "model", "new", "original", "single", "double", "set", "pack", "combo",
    "pc", "pcs", "piece", "pieces", "unit", "units",
}

# Trailing measurement units ("fan 1200 MM", "speaker 120 WATT").
_UNIT_TAIL = {
    "mm", "cm", "m", "inch", "inches", "in", "ft", "w", "watt", "watts", "kw",
    "v", "kv", "mah", "ah", "hz", "rpm", "mg", "ml", "l", "kg", "g", "gm", "gsm",
}


def _tokens(text: str) -> list[str]:
    return [token for token in _SPLIT_RE.split((text or "").lower()) if token]


def _singular(token: str) -> str:
    if len(token) > 3 and token.endswith("es") and token[-3] in "sxz":
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def head_noun(text: str) -> str:
    """Best-effort head noun of a product phrase: the last meaningful token."""
    lowered = f" {(text or '').lower()} "
    for cut in _CUT_PHRASES:
        index = lowered.find(cut)
        if index > 0:
            lowered = lowered[:index]
    tokens = _tokens(lowered)
    while tokens:
        tail = tokens[-1]
        if any(ch.isdigit() for ch in tail) or tail in _GENERIC_TAIL or tail in _UNIT_TAIL:
            tokens.pop()
            continue
        break
    return _singular(tokens[-1]) if tokens else ""


def _descriptor_tokens(descriptors: list[str] | tuple[str, ...] | None) -> set[str]:
    tokens: set[str] = set()
    for phrase in descriptors or []:
        for token in _tokens(phrase):
            singular = _singular(token)
            if len(singular) >= 2:
                tokens.add(singular)
    return tokens


def match_tier(
    query_product: str,
    product_title: str,
    *,
    descriptors: list[str] | tuple[str, ...] | None = None,
) -> int:
    """0 = core product for this query, 1 = related/accessory, 2 = unrelated."""
    query_noun = head_noun(query_product)
    if not query_noun or not product_title:
        return 1
    # Compare compound roots too so "monocartons" tiers against "Mono Cartons"
    # titles (query head "monocarton", title head "carton").
    query_root = query_normalization.compound_root(query_noun)
    title_noun = head_noun(product_title)
    if title_noun == query_noun or query_normalization.compound_root(title_noun) == query_root:
        tier = 0
    else:
        title_tokens = {_singular(token) for token in _tokens(product_title)}
        if query_noun in title_tokens or query_root in title_tokens:
            tier = 1
        else:
            tier = 2

    desc_tokens = _descriptor_tokens(descriptors)
    if tier == 0 and desc_tokens:
        title_tokens = {_singular(token) for token in _tokens(product_title)}
        if not desc_tokens & title_tokens:
            tier = 1
    return tier
