"""Catalog variance probe for dominant product queries (Phase 3).

When a query maps to one product type in Algolia (no facet disambiguation)
but top catalog titles vary widely on capacity/storage/etc., surface that
spread to Priya so she asks 1–2 focused spec questions before searching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.database import vendor_offerings_collection
from app.services import algolia_first, algolia_search, query_normalization, search_specs

logger = logging.getLogger(__name__)

SAMPLE_HITS = 40
MIN_TITLES = 10
MIN_SPREAD_RATIO = 3.0
MIN_DISTINCT_VALUES = 3
MAX_SPEC_QUESTIONS = 2

_UNIT_LABELS = {
    "mah": "capacity",
    "ah": "capacity",
    "gb": "storage",
    "tb": "storage",
    "mb": "storage",
    "w": "power",
    "watt": "power",
    "watts": "power",
    "kw": "power",
    "v": "voltage",
    "volt": "voltage",
    "volts": "voltage",
    "mm": "size",
    "cm": "size",
    "m": "size",
    "in": "size",
    "inch": "size",
    "inches": "size",
    "ml": "volume",
    "l": "volume",
    "litre": "volume",
    "liter": "volume",
    "kg": "weight",
    "g": "weight",
    "rpm": "speed",
    "kva": "rating",
    "hp": "power",
}


@dataclass
class VarianceAxis:
    dimension: str
    unit: str
    min_value: float
    max_value: float
    examples: list[str] = field(default_factory=list)
    spread_ratio: float = 1.0


@dataclass
class VarianceResult:
    nb_hits: int = 0
    sample_size: int = 0
    high_variance: bool = False
    axes: list[VarianceAxis] = field(default_factory=list)


def _display_unit(unit: str) -> str:
    if unit.lower() == "mah":
        return "mAh"
    return unit.upper() if len(unit) <= 4 else unit


def _format_number(value: float) -> str:
    if value >= 1000 and value == int(value):
        return f"{int(value):,}"
    if value == int(value):
        return str(int(value))
    return str(value)


def _pick_examples(values: list[float]) -> list[str]:
    unique = sorted(set(values))
    if len(unique) <= 4:
        return [_format_number(v) for v in unique]
    picks = [unique[0], unique[len(unique) // 3], unique[(2 * len(unique)) // 3], unique[-1]]
    return [_format_number(v) for v in dict.fromkeys(picks)]


def analyze_title_variance(titles: list[str]) -> VarianceResult:
    """Measure numeric spread across product titles (unit-tested, no Algolia)."""
    by_unit: dict[str, list[float]] = {}
    for title in titles:
        for snippet in search_specs.extract_specs_from_text(title):
            for spec in search_specs.parse_numeric_specs([snippet]):
                if spec.unit:
                    by_unit.setdefault(spec.unit, []).append(spec.value)

    axes: list[VarianceAxis] = []
    for unit, values in by_unit.items():
        unique = sorted(set(values))
        if len(unique) < MIN_DISTINCT_VALUES:
            continue
        low = unique[0]
        high = unique[-1]
        if low <= 0:
            continue
        ratio = high / low
        if ratio < MIN_SPREAD_RATIO and len(unique) < MIN_DISTINCT_VALUES + 1:
            continue
        axes.append(
            VarianceAxis(
                dimension=_UNIT_LABELS.get(unit, unit),
                unit=unit,
                min_value=low,
                max_value=high,
                examples=_pick_examples(unique),
                spread_ratio=ratio,
            )
        )
    axes.sort(key=lambda axis: axis.spread_ratio, reverse=True)
    return VarianceResult(
        sample_size=len(titles),
        high_variance=bool(axes),
        axes=axes,
    )


async def _titles_for_hits(hits: list[dict[str, Any]]) -> list[str]:
    offering_ids = [str(hit.get("offering_id") or "") for hit in hits if hit.get("offering_id")]
    if not offering_ids:
        return []
    docs = await vendor_offerings_collection.find(
        {"offering_id": {"$in": offering_ids}, "is_active": {"$ne": False}}
    ).to_list(length=len(offering_ids))
    by_id = {
        str(doc.get("offering_id") or ""): (
            str(doc.get("normalized_product_name") or doc.get("product_name_raw") or "").strip()
        )
        for doc in docs
    }
    titles = [by_id[offering_id] for offering_id in offering_ids if by_id.get(offering_id)]
    return titles


async def probe_catalog_variance(
    product: str,
    *,
    city: str | None = None,
    taxonomy_facet_id: str | None = None,
    optional_words: tuple[str, ...] = (),
) -> VarianceResult:
    """Sample Algolia hits for a dominant product query and measure spec spread."""
    if not algolia_search.is_enabled():
        return VarianceResult()
    query_text = query_normalization.singularize_phrase((product or "").strip())
    if len(query_text) < 3:
        return VarianceResult()

    result = await algolia_search.search_offerings_faceted(
        query_text=query_text,
        city=city,
        taxonomy_node_id=taxonomy_facet_id,
        limit=SAMPLE_HITS,
        optional_words=optional_words,
    )
    titles = await _titles_for_hits(result.hits)
    if len(titles) < MIN_TITLES:
        split_text = query_normalization.split_compounds(query_text)
        if split_text and split_text != query_text:
            split_result = await algolia_search.search_offerings_faceted(
                query_text=split_text,
                city=city,
                taxonomy_node_id=taxonomy_facet_id,
                limit=SAMPLE_HITS,
                optional_words=optional_words,
            )
            extra = await _titles_for_hits(split_result.hits)
            titles = list(dict.fromkeys([*titles, *extra]))

    analyzed = analyze_title_variance(titles)
    analyzed.nb_hits = result.nb_hits
    return analyzed


def axis_to_dict(axis: VarianceAxis) -> dict[str, Any]:
    return {
        "dimension": axis.dimension,
        "unit": axis.unit,
        "min_value": axis.min_value,
        "max_value": axis.max_value,
        "examples": axis.examples,
        "spread_ratio": axis.spread_ratio,
    }


def variance_to_hint_payload(product: str, result: VarianceResult) -> dict[str, Any]:
    return {
        "product": product,
        "high_variance": result.high_variance,
        "nb_hits": result.nb_hits,
        "sample_size": result.sample_size,
        "axes": [axis_to_dict(axis) for axis in result.axes],
        "questions_asked": 0,
        "pending_spec_question": False,
        "still_high": result.high_variance,
    }


def format_variance_note(hint: dict[str, Any] | None) -> str | None:
    """System note for Priya when catalog variants spread widely."""
    if not hint or not hint.get("high_variance"):
        return None
    axes = hint.get("axes") or []
    if not axes:
        return None
    product = str(hint.get("product") or "this product")
    asked = int(hint.get("questions_asked") or 0)
    if asked >= MAX_SPEC_QUESTIONS:
        return None

    axis = axes[min(asked, len(axes) - 1)]
    dimension = axis.get("dimension") or axis.get("unit") or "spec"
    unit = str(axis.get("unit") or "").lower()
    examples = ", ".join(str(item) for item in (axis.get("examples") or [])[:4])
    low = _format_number(float(axis.get("min_value") or 0))
    high = _format_number(float(axis.get("max_value") or 0))
    unit_suffix = f" {_display_unit(unit)}" if unit else ""

    remaining = MAX_SPEC_QUESTIONS - asked
    return (
        f"CATALOG VARIANCE — \"{product}\" is one product type in our catalog, but "
        f"{dimension} varies widely (roughly {low} to {high}{unit_suffix}"
        f"{f', e.g. {examples}' if examples else ''}). "
        f"Ask ONE focused question about {dimension} before location or \"Noted\". "
        f"You may ask up to {remaining} more spec question(s) if the answer is still vague. "
        "If the buyer already stated this spec in the conversation, skip it."
    )


def build_spec_question(hint: dict[str, Any] | None) -> str | None:
    """Deterministic spec question when Priya tries to search too early."""
    if not hint or not hint.get("high_variance"):
        return None
    axes = hint.get("axes") or []
    if not axes:
        return None
    asked = int(hint.get("questions_asked") or 0)
    if asked >= MAX_SPEC_QUESTIONS:
        return None
    axis = axes[min(asked, len(axes) - 1)]
    dimension = axis.get("dimension") or "spec"
    unit = str(axis.get("unit") or "").lower()
    examples = ", ".join(str(item) for item in (axis.get("examples") or [])[:4])
    low = _format_number(float(axis.get("min_value") or 0))
    high = _format_number(float(axis.get("max_value") or 0))
    unit_suffix = f" {_display_unit(unit)}" if unit else ""
    if examples:
        return (
            f"What {dimension} do you need? Our catalog has options from about "
            f"{low} to {high}{unit_suffix} (e.g. {examples})."
        )
    return f"What {dimension} do you need?"


def build_spec_suggested_replies(hint: dict[str, Any] | None) -> list[str]:
    """Clickable example answers for a catalog-variance spec question."""
    if not hint or not hint.get("high_variance"):
        return []
    axes = hint.get("axes") or []
    if not axes:
        return []
    asked = int(hint.get("questions_asked") or 0)
    axis = axes[min(asked, len(axes) - 1)]
    unit = str(axis.get("unit") or "").lower()
    unit_suffix = f" {_display_unit(unit)}" if unit else ""
    replies: list[str] = []
    seen: set[str] = set()
    for item in (axis.get("examples") or [])[:5]:
        text = str(item).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        replies.append(text)
    if not replies:
        low = _format_number(float(axis.get("min_value") or 0))
        high = _format_number(float(axis.get("max_value") or 0))
        if low:
            replies.append(f"{low}{unit_suffix}".strip())
        if high and high != low:
            replies.append(f"{high}{unit_suffix}".strip())
    return replies[:5]


def _collected_specs(conversation_history: list[dict[str, str]] | None) -> list[str]:
    return search_specs.enrich_search_specs([], conversation_history=conversation_history)


def _axis_covered(axis: dict[str, Any], collected: list[str]) -> bool:
    unit = str(axis.get("unit") or "").lower()
    if not unit:
        return False
    for spec in search_specs.parse_numeric_specs(collected):
        if spec.unit == unit:
            return True
    return False


def needs_spec_question(
    hint: dict[str, Any] | None,
    *,
    conversation_history: list[dict[str, str]] | None,
) -> bool:
    """True when intake must ask another spec question before searching."""
    if not hint or not hint.get("high_variance"):
        return False
    asked = int(hint.get("questions_asked") or 0)
    if asked >= MAX_SPEC_QUESTIONS:
        return False
    axes = hint.get("axes") or []
    if not axes:
        return False

    collected = _collected_specs(conversation_history)

    if asked == 0:
        return not _axis_covered(axes[0], collected)

    if asked == 1 and hint.get("still_high"):
        for axis in axes[1:2] if len(axes) > 1 else axes[:1]:
            if not _axis_covered(axis, collected):
                return True
        return not _axis_covered(axes[0], collected)

    return False


async def refresh_variance_hint(
    state_hint: dict[str, Any] | None,
    *,
    product: str,
    city: str | None = None,
    taxonomy_facet_id: str | None = None,
    conversation_history: list[dict[str, str]] | None,
) -> dict[str, Any] | None:
    """Re-probe variance, preserving question counters from prior turns."""
    if not product.strip() or not algolia_search.is_enabled() or not settings_algolia_first():
        return state_hint

    facet_probe = await algolia_first.probe_product_types(product, city)
    if facet_probe.needs_disambiguation:
        return None

    optional = search_specs.optional_search_tokens(None, list(_collected_specs(conversation_history)))
    result = await probe_catalog_variance(
        product,
        city=city,
        taxonomy_facet_id=taxonomy_facet_id,
        optional_words=optional,
    )
    if not result.high_variance:
        return None

    previous = dict(state_hint or {})
    payload = variance_to_hint_payload(product, result)
    payload["questions_asked"] = int(previous.get("questions_asked") or 0)
    payload["pending_spec_question"] = bool(previous.get("pending_spec_question"))
    payload["still_high"] = result.high_variance
    return payload


def settings_algolia_first() -> bool:
    from app.config import settings

    return bool(settings.algolia_first_search)
