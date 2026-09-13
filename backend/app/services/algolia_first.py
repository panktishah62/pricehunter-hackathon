"""Facet-driven disambiguation for Algolia-first search.

Before launching a search, probe Algolia with the user's product words and
look at the taxonomy facet distribution. If the hits spread across several
product types (e.g. "camera" -> CCTV, photography, fire-safety), the chat asks
"what type of X?" with options generated from the live facet counts, so we
never ask about a type we don't actually stock.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.services import algolia_search, query_normalization
from app.taxonomy.canonical import NODE_BY_ID

logger = logging.getLogger(__name__)

# Only bother disambiguating broad queries.
MIN_HITS_FOR_DISAMBIGUATION = 30
# If the top facet already covers this share of hits, the query is specific
# enough -- search directly and let Algolia's ranking do the rest.
DOMINANT_FACET_SHARE = 0.75
# An option must cover at least this many hits (and this share) to be offered.
# Counts are truthful matches now that the polluted `tokens`/`attributes`
# fields are excluded from search, so a low floor is safe.
MIN_OPTION_COUNT = 8
MIN_OPTION_SHARE = 0.01
MAX_OPTIONS = 5
# Non-product taxonomy buckets that must never be offered as an answer.
EXCLUDED_FACET_VALUES = {"taxonomy_review_hold", "unknown", ""}

_WORD_RE = re.compile(r"[a-z0-9]+")

# Filler words ignored when matching a reply against the offered options.
_STOPWORDS = {
    "the", "a", "an", "one", "ones", "please", "i", "want", "need", "looking",
    "for", "type", "kind", "of", "yes", "ok", "okay", "go", "with", "option",
}


@dataclass
class ProbeOption:
    node_id: str
    label: str
    count: int


@dataclass
class ProbeResult:
    nb_hits: int = 0
    options: list[ProbeOption] = field(default_factory=list)
    needs_disambiguation: bool = False


def _label_for_node(node_id: str) -> str:
    node = NODE_BY_ID.get(node_id)
    if node is not None:
        return node.display_name
    # Fall back to a readable version of the id, e.g.
    # "electronics_cctv_security" -> "Cctv Security".
    stripped = re.sub(r"^[a-z]+_", "", node_id)
    return stripped.replace("_", " ").strip().title() or node_id


async def probe_product_types(product: str, city: str | None) -> ProbeResult:
    """One cheap Algolia query (0 hits, facets only) to measure ambiguity."""
    # Singularize in code: the index's ignorePlurals does not merge forms for
    # this catalog ("cartons" ~40 hits vs "carton" ~1,000+).
    query_text = query_normalization.singularize_phrase(product)
    result = await algolia_search.search_offerings_faceted(
        query_text=query_text,
        city=city,
        limit=0,
        facet_attributes=("taxonomy_node_id",),
    )
    # Compound fallback: "monocartons" is indexed as "mono carton". Only when
    # the glued form finds too little to work with.
    if result.nb_hits < MIN_HITS_FOR_DISAMBIGUATION:
        split_text = query_normalization.split_compounds(query_text)
        if split_text:
            split_result = await algolia_search.search_offerings_faceted(
                query_text=split_text,
                city=city,
                limit=0,
                facet_attributes=("taxonomy_node_id",),
            )
            if split_result.nb_hits > result.nb_hits:
                result = split_result
    counts = result.facets.get("taxonomy_node_id") or {}
    if not counts or result.nb_hits < MIN_HITS_FOR_DISAMBIGUATION:
        return ProbeResult(nb_hits=result.nb_hits)

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    total = max(sum(count for _, count in ranked), 1)
    top_share = ranked[0][1] / total

    options = [
        ProbeOption(node_id=node_id, label=_label_for_node(node_id), count=count)
        for node_id, count in ranked
        if node_id not in EXCLUDED_FACET_VALUES
        and count >= MIN_OPTION_COUNT
        and (count / total) >= MIN_OPTION_SHARE
    ][:MAX_OPTIONS]

    needs_disambiguation = top_share < DOMINANT_FACET_SHARE and len(options) >= 2
    return ProbeResult(
        nb_hits=result.nb_hits,
        options=options,
        needs_disambiguation=needs_disambiguation,
    )


def build_question(product: str, result: ProbeResult) -> str:
    labels = ", ".join(option.label for option in result.options)
    return (
        f"I found {result.nb_hits:,} matches for \"{product}\". "
        f"What type are you looking for — {labels}? "
        "You can also describe it in your own words."
    )


def match_option(user_message: str, options: list[ProbeOption]) -> ProbeOption | None:
    """Map the user's reply to an option: exact label, ordinal, or word overlap."""
    text = user_message.strip().lower()
    if not text:
        return None

    for option in options:
        if text == option.label.lower() or text == option.node_id.lower():
            return option

    ordinal = re.fullmatch(r"(\d+)\.?|option (\d+)", text)
    if ordinal:
        index = int(ordinal.group(1) or ordinal.group(2)) - 1
        if 0 <= index < len(options):
            return options[index]

    message_words = set(_WORD_RE.findall(text)) - _STOPWORDS
    if not message_words:
        return None
    best: tuple[float, ProbeOption] | None = None
    for option in options:
        option_words = set(_WORD_RE.findall(option.label.lower())) | set(
            _WORD_RE.findall(option.node_id.lower())
        )
        overlap = len(message_words & option_words)
        if overlap == 0:
            continue
        share = overlap / len(message_words)
        if best is None or share > best[0]:
            best = (share, option)
    # Require at least half of the user's words to point at the option so that
    # a fresh query ("actually I need a tripod") falls through to Priya.
    if best is not None and best[0] >= 0.5:
        return best[1]
    return None
