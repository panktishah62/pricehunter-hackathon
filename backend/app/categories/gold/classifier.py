from __future__ import annotations

import logging
import re
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.categories.router import QueryRoute
from app.config import settings
from app.models.schemas import StructuredQuery
from app.services.product_registry import classify_product_type

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


class GoldSubcategorySelection(BaseModel):
    subcategory_id: Literal["gold_bullion", "gold_jewellery", "gold_generic"]


GOLD_SUBCATEGORY_SYSTEM_PROMPT = """You are the gold subcategory classifier for an Indian sourcing assistant.

Choose exactly one subcategory:
- "gold_bullion": bars, coins, biscuits, slabs, bullion trading, investment gold, purity+weight driven gold requests
- "gold_jewellery": rings, chains, bangles, bracelets, necklaces, earrings, ornaments, bridal jewellery
- "gold_generic": gold queries that are still too broad to confidently place in bullion or jewellery

Rules:
- Purity + weight requests without jewellery terms are usually bullion.
- Jewellery product names always go to jewellery, even when purity or weight is present.
- If the query is just "gold rate" or broad "gold" with no clear format, use gold_generic.

Return only JSON matching the schema.
""".strip()

GOLD_BULLION_ROUTE = QueryRoute(
    category="gold",
    subcategory_id="gold_bullion",
    handler_key="gold.bullion",
    strategy_tags=("gold", "bullion", "live_scripts", "outreach"),
)
GOLD_JEWELLERY_ROUTE = QueryRoute(
    category="gold",
    subcategory_id="gold_jewellery",
    handler_key="gold.jewellery",
    strategy_tags=("gold", "jewellery"),
)
GOLD_GENERIC_ROUTE = QueryRoute(
    category="gold",
    subcategory_id="gold_generic",
    handler_key="gold.generic",
    strategy_tags=("gold",),
)


def _looks_like_bullion(query: StructuredQuery) -> bool:
    normalized = f"{query.product} {query.raw_query}".lower()
    has_purity = bool(re.search(r"\b(?:24|22|18)\s?k(?:t|arat)?\b", normalized)) or "999" in normalized or "995" in normalized
    has_weight = bool(re.search(r"\b\d+(?:\.\d+)?\s?(?:g|gm|gram|grams|kg|oz|tola)\b", normalized))
    has_bullion_type = any(token in normalized for token in ("coin", "bar", "biscuit", "bullion", "bulion", "boolion", "bullian", "slab"))
    has_bullion_typo = "boolean" in normalized and has_purity
    has_jewellery_type = any(
        token in normalized
        for token in ("ring", "chain", "necklace", "earring", "bracelet", "bangle", "mangalsutra", "pendant")
    )
    return (has_purity and has_weight and not has_jewellery_type) or has_bullion_type or has_bullion_typo


def _heuristic_gold_route(query: StructuredQuery) -> QueryRoute:
    spec = classify_product_type(query.product, query.category)
    if spec.product_type_id == "gold_bullion" or _looks_like_bullion(query):
        return GOLD_BULLION_ROUTE
    if spec.product_type_id == "gold_jewellery":
        return GOLD_JEWELLERY_ROUTE
    return GOLD_GENERIC_ROUTE


def _route_from_subcategory(subcategory_id: str) -> QueryRoute:
    if subcategory_id == "gold_bullion":
        return GOLD_BULLION_ROUTE
    if subcategory_id == "gold_jewellery":
        return GOLD_JEWELLERY_ROUTE
    return GOLD_GENERIC_ROUTE


async def classify_gold_query_route(query: StructuredQuery) -> QueryRoute:
    heuristic_route = _heuristic_gold_route(query)
    if heuristic_route.handler_key == GOLD_BULLION_ROUTE.handler_key:
        return heuristic_route

    if not settings.openai_api_key:
        return heuristic_route

    client = _get_openai_client()
    prompt = "\n".join(
        [
            f"Raw query: {query.raw_query}",
            f"Structured product: {query.product}",
            f"Location: {query.location or 'unknown'}",
        ]
    )
    try:
        response = await client.responses.parse(
            model=settings.openai_gold_subcategory_model or settings.openai_model,
            input=[
                {"role": "system", "content": GOLD_SUBCATEGORY_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=GoldSubcategorySelection,
            temperature=0,
        )
        selection = response.output_parsed
        if selection is None:
            raise ValueError("OpenAI returned no parsed GoldSubcategorySelection.")
        return _route_from_subcategory(selection.subcategory_id)
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning("Gold subcategory classification failed, using fallback: %s", exc)
        return heuristic_route


def is_gold_bullion_query(query: StructuredQuery) -> bool:
    return (query.route_handler or "").strip().lower() == GOLD_BULLION_ROUTE.handler_key or (
        not query.route_handler and _heuristic_gold_route(query).handler_key == GOLD_BULLION_ROUTE.handler_key
    )
