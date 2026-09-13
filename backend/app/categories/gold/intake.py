from __future__ import annotations

import logging
import re
from typing import Iterable

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.categories.intake import CategoryIntakeResult
from app.config import settings
from app.categories.gold.metal import infer_bullion_metal
from app.models.schemas import ConversationState, StructuredQuery

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


class _WeightExtraction(BaseModel):
    grams: float | None


async def _llm_extract_weight_grams(text: str) -> str | None:
    """LLM fallback for the weight step when the deterministic regex can't parse
    the reply (e.g. odd phrasings, tola/oz, typos). Best-effort: returns None on
    no API key or any error, so the caller just re-asks as before."""
    if not (text or "").strip() or not settings.openai_api_key:
        return None
    try:
        # System-level guidance goes in `instructions` (a top-level string);
        # responses.parse() does not honour a system-role entry inside `input`.
        response = await _get_openai_client().responses.parse(
            model=settings.openai_model,
            instructions=(
                "Extract the gold weight the buyer wants, converted to GRAMS. "
                "Units: g/gm/gms/gram(s); kg/kgs/kilogram(s) = x1000; "
                "tola = 11.6638 g; oz/ounce = 31.1035 g. Return the weight in "
                "grams as a number, or null if the message states no weight."
            ),
            input=text,
            text_format=_WeightExtraction,
            temperature=0,
        )
        selection = response.output_parsed
        if selection is None or selection.grams is None or selection.grams <= 0:
            return None
        grams = float(selection.grams)
        return str(int(grams) if grams.is_integer() else round(grams, 2))
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning("LLM weight extraction failed: %s", exc)
        return None


GOLD_VARIANT_OPTIONS: tuple[tuple[str, str], ...] = (
    ("999_bullion", "999 bullion"),
    ("999_bis_bullion", "999 BIS bullion"),
    ("995_bullion", "995 bullion"),
    ("jewellery", "Jewellery"),
    ("not_sure", "Not sure"),
)

BULLION_FORM_OPTIONS: tuple[tuple[str, str], ...] = (
    ("bar", "Bar"),
    ("coin", "Coin"),
    ("biscuit", "Biscuit"),
    ("bullion", "Other bullion form"),
)

JEWELLERY_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("ring", "Ring"),
    ("chain", "Chain"),
    ("bangle", "Bangle"),
    ("necklace", "Necklace"),
    ("earrings", "Earrings"),
    ("other", "Other jewellery"),
)

JEWELLERY_PURITY_OPTIONS: tuple[tuple[str, str], ...] = (
    ("22k", "22K"),
    ("18k", "18K"),
    ("24k", "24K"),
    ("not_sure", "Not sure"),
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _build_option_prompt(question: str, options: Iterable[tuple[str, str]]) -> str:
    lines = [question]
    for index, (_, label) in enumerate(options, start=1):
        lines.append(f"{index}. {label}")
    return "\n".join(lines)


def _match_option(
    latest_message: str,
    options: tuple[tuple[str, str], ...],
    *,
    aliases: dict[str, tuple[str, ...]] | None = None,
) -> str | None:
    normalized = _normalize_text(latest_message)
    if normalized.isdigit():
        idx = int(normalized)
        if 1 <= idx <= len(options):
            return options[idx - 1][0]

    aliases = aliases or {}
    for value, label in options:
        label_normalized = _normalize_text(label)
        if normalized == label_normalized or normalized == value:
            return value
        for alias in aliases.get(value, ()):
            if normalized == _normalize_text(alias):
                return value
    return None


def _extract_weight_grams(text: str) -> str | None:
    normalized = _normalize_text(text)
    kg_match = re.search(r"\b(\d+(?:\.\d+)?)\s?(?:kgs?|kilograms?)\b", normalized)
    if kg_match:
        grams = float(kg_match.group(1)) * 1000
        return str(int(grams) if grams.is_integer() else round(grams, 2))

    # Accept the common abbreviations buyers actually type: g, gm, gms, gram(s).
    # "gms"/"gm" were previously unmatched (the alternation lacked them and the
    # \b after "g"/"gm" fails before the trailing letters), so "400 gms" was
    # silently ignored and the weight question kept repeating.
    gram_match = re.search(r"\b(\d+(?:\.\d+)?)\s?(?:grams?|gms?|g)\b", normalized)
    if gram_match:
        grams = float(gram_match.group(1))
        return str(int(grams) if grams.is_integer() else round(grams, 2))
    return None


def _extract_gold_family(text: str) -> str | None:
    normalized = _normalize_text(text)
    if any(token in normalized for token in ("ring", "chain", "bangle", "bracelet", "necklace", "earrings", "jewellery", "jewelry", "ornament")):
        return "gold_jewellery"
    if any(token in normalized for token in ("bullion", "bulion", "boolion", "bullian", "bar", "coin", "biscuit", "999", "995", "999 bis", "silver")):
        return "gold_bullion"
    if "boolean" in normalized and any(token in normalized for token in ("999", "995", "24k", "24 k")):
        return "gold_bullion"
    return None


def _extract_bullion_purity(text: str) -> str | None:
    normalized = _normalize_text(text)
    if "999 bis" in normalized:
        return "999 bis"
    if re.search(r"\b995\b", normalized):
        return "995"
    if re.search(r"\b999\b", normalized) or re.search(r"\b24\s?k\b", normalized):
        return "999"
    return None


def _extract_bullion_form(text: str) -> str | None:
    normalized = _normalize_text(text)
    if "coin" in normalized:
        return "coin"
    if "biscuit" in normalized:
        return "biscuit"
    if re.search(r"\bbar\b", normalized):
        return "bar"
    if any(token in normalized for token in ("bullion", "bulion", "boolion", "bullian")):
        return "bullion"
    if "boolean" in normalized and any(token in normalized for token in ("999", "995", "24k", "24 k")):
        return "bullion"
    return None


def _extract_jewellery_type(text: str) -> str | None:
    normalized = _normalize_text(text)
    for value, _ in JEWELLERY_TYPE_OPTIONS:
        if value != "other" and value in normalized:
            return value
    return None


def _extract_jewellery_purity(text: str) -> str | None:
    normalized = _normalize_text(text)
    if re.search(r"\b22\s?k\b", normalized) or "916" in normalized:
        return "22k"
    if re.search(r"\b18\s?k\b", normalized):
        return "18k"
    if re.search(r"\b24\s?k\b", normalized):
        return "24k"
    return None


def _set_awaiting(state: ConversationState, attribute_key: str) -> None:
    state.product_intake_awaiting_attribute = attribute_key


def _gold_variant_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "999_bullion": ("999", "24k bullion", "24k gold", "999 gold"),
        "999_bis_bullion": ("999 bis", "bis 999", "999 bis gold"),
        "995_bullion": ("995", "995 gold"),
        "jewellery": ("jewellery", "jewelry", "ornaments"),
        "not_sure": ("not sure", "not sure yet"),
    }


def _bullion_form_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "bar": ("gold bar",),
        "coin": ("gold coin",),
        "biscuit": ("gold biscuit",),
        "bullion": ("bullion", "other bullion", "bulion", "boolion", "bullian", "boolean"),
    }


def _jewellery_type_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "bangle": ("bangles",),
        "earrings": ("earring",),
        "other": ("other",),
    }


async def handle_gold_intake(
    *,
    state: ConversationState,
    structured_query: StructuredQuery,
    combined_query_text: str,
    latest_message: str,
) -> CategoryIntakeResult:
    attrs = dict(state.product_attributes)
    family_id = state.product_family_id or _extract_gold_family(combined_query_text)

    awaiting = state.product_intake_awaiting_attribute
    latest_normalized = _normalize_text(latest_message)

    if awaiting == "gold_variant":
        picked = _match_option(latest_message, GOLD_VARIANT_OPTIONS, aliases=_gold_variant_aliases())
        if picked == "jewellery":
            family_id = "gold_jewellery"
        elif picked == "999_bullion":
            family_id = "gold_bullion"
            attrs["purity"] = "999"
        elif picked == "999_bis_bullion":
            family_id = "gold_bullion"
            attrs["purity"] = "999 bis"
        elif picked == "995_bullion":
            family_id = "gold_bullion"
            attrs["purity"] = "995"
        elif picked == "not_sure":
            state.product_attributes = attrs
            state.product_family_id = None
            state.product_intake_awaiting_attribute = None
            return CategoryIntakeResult(
                handled=False,
                structured_query=structured_query,
            )

    if family_id == "gold_bullion":
        attrs["metal"] = attrs.get("metal") or infer_bullion_metal(combined_query_text)
        attrs["purity"] = attrs.get("purity") or _extract_bullion_purity(combined_query_text) or ""
        if awaiting == "bullion_form" and "form" not in attrs:
            picked_form = _match_option(latest_message, BULLION_FORM_OPTIONS, aliases=_bullion_form_aliases())
            if picked_form:
                attrs["form"] = picked_form
        attrs["form"] = attrs.get("form") or _extract_bullion_form(combined_query_text) or ""
        attrs["weight_grams"] = attrs.get("weight_grams") or _extract_weight_grams(combined_query_text) or ""
        # LLM fallback: only when we just asked for the weight and the
        # deterministic parser couldn't read the reply. Keeps the fast/free
        # regex path for the common case and only spends an LLM call on a miss.
        if not attrs.get("weight_grams") and awaiting == "bullion_weight":
            llm_weight = await _llm_extract_weight_grams(latest_message)
            if llm_weight:
                attrs["weight_grams"] = llm_weight

        if not attrs.get("purity"):
            state.product_family_id = family_id
            state.product_attributes = {k: v for k, v in attrs.items() if v}
            _set_awaiting(state, "gold_variant")
            return CategoryIntakeResult(
                handled=True,
                assistant_message=_build_option_prompt(
                    "Which bullion purity do you need?",
                    GOLD_VARIANT_OPTIONS[:3],
                ),
                structured_query=structured_query,
            )
        if not attrs.get("form"):
            state.product_family_id = family_id
            state.product_attributes = {k: v for k, v in attrs.items() if v}
            _set_awaiting(state, "bullion_form")
            return CategoryIntakeResult(
                handled=True,
                assistant_message=_build_option_prompt(
                    "What form of bullion do you need?",
                    BULLION_FORM_OPTIONS,
                ),
                structured_query=structured_query,
            )
        if not attrs.get("weight_grams"):
            state.product_family_id = family_id
            state.product_attributes = {k: v for k, v in attrs.items() if v}
            _set_awaiting(state, "bullion_weight")
            return CategoryIntakeResult(
                handled=True,
                assistant_message="What exact weight do you need in grams or kg?",
                structured_query=structured_query,
            )

        state.product_family_id = family_id
        state.product_attributes = {k: v for k, v in attrs.items() if v}
        state.product_intake_awaiting_attribute = None
        metal = attrs.get("metal") or "gold"
        canonical = f"{attrs['purity']} {metal} {attrs['form']} {attrs['weight_grams']} grams"
        structured_query.product = canonical
        structured_query.quantity = f"{attrs['weight_grams']} grams"
        structured_query.gold_form = "bullion"
        return CategoryIntakeResult(
            handled=True,
            handoff_query=canonical,
            structured_query=structured_query,
        )

    if family_id == "gold_jewellery":
        if awaiting == "jewellery_type" and "jewellery_type" not in attrs:
            picked_type = _match_option(latest_message, JEWELLERY_TYPE_OPTIONS, aliases=_jewellery_type_aliases())
            if picked_type:
                attrs["jewellery_type"] = picked_type
        attrs["jewellery_type"] = attrs.get("jewellery_type") or _extract_jewellery_type(combined_query_text) or ""
        if awaiting == "jewellery_purity" and "purity" not in attrs:
            picked_purity = _match_option(latest_message, JEWELLERY_PURITY_OPTIONS)
            if picked_purity:
                attrs["purity"] = picked_purity
        attrs["purity"] = attrs.get("purity") or _extract_jewellery_purity(combined_query_text) or ""
        attrs["weight_grams"] = attrs.get("weight_grams") or _extract_weight_grams(combined_query_text) or ""

        if not attrs.get("jewellery_type"):
            state.product_family_id = family_id
            state.product_attributes = {k: v for k, v in attrs.items() if v}
            _set_awaiting(state, "jewellery_type")
            return CategoryIntakeResult(
                handled=True,
                assistant_message=_build_option_prompt(
                    "What jewellery item do you need?",
                    JEWELLERY_TYPE_OPTIONS,
                ),
                structured_query=structured_query,
            )
        if not attrs.get("purity"):
            state.product_family_id = family_id
            state.product_attributes = {k: v for k, v in attrs.items() if v}
            _set_awaiting(state, "jewellery_purity")
            return CategoryIntakeResult(
                handled=True,
                assistant_message=_build_option_prompt(
                    "What jewellery purity do you need?",
                    JEWELLERY_PURITY_OPTIONS,
                ),
                structured_query=structured_query,
            )

        state.product_family_id = family_id
        state.product_attributes = {k: v for k, v in attrs.items() if v}
        state.product_intake_awaiting_attribute = None
        metal = attrs.get("metal") or infer_bullion_metal(combined_query_text)
        canonical = f"{attrs['purity']} {metal} {attrs['jewellery_type']}"
        if attrs.get("weight_grams"):
            canonical = f"{canonical} {attrs['weight_grams']} grams"
            structured_query.quantity = f"{attrs['weight_grams']} grams"
        structured_query.product = canonical
        structured_query.gold_form = "jewellery"
        return CategoryIntakeResult(
            handled=True,
            handoff_query=canonical,
            structured_query=structured_query,
        )

    if (structured_query.route_handler or "").strip().lower().startswith("gold.") or "gold" in latest_normalized or "gold" in _normalize_text(combined_query_text):
        state.product_family_id = None
        state.product_attributes = attrs
        _set_awaiting(state, "gold_variant")
        return CategoryIntakeResult(
            handled=True,
            assistant_message=_build_option_prompt(
                "What kind of gold should I source?",
                GOLD_VARIANT_OPTIONS,
            ),
            structured_query=structured_query,
        )

    return CategoryIntakeResult(
        handled=False,
        structured_query=structured_query,
    )
