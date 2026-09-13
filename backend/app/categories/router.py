from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings
from app.models.schemas import StructuredQuery

logger = logging.getLogger(__name__)

SUPPORTED_ROUTE_CATEGORIES = ("electronics", "medicine", "gold")
_openai_client: AsyncOpenAI | None = None


def _looks_like_gold_bullion_text(query: StructuredQuery) -> bool:
    normalized = f"{query.product} {query.raw_query}".lower()
    has_gold_word = any(token in normalized for token in ("gold", "bullion", "bulion", "boolion", "bullian"))
    has_bullion_typo = "boolean" in normalized and any(token in normalized for token in ("999", "995", "24k", "24 k"))
    has_purity = any(token in normalized for token in ("999", "995", "9999", "24k", "24 k"))
    has_form = any(token in normalized for token in ("coin", "bar", "biscuit", "slab"))
    return has_gold_word or has_bullion_typo or (has_purity and has_form)


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


@dataclass(frozen=True)
class QueryRoute:
    category: str
    subcategory_id: str | None
    handler_key: str
    strategy_tags: tuple[str, ...] = field(default_factory=tuple)


class CategoryRouteSelection(BaseModel):
    category: Literal["electronics", "medicine", "gold"]


CATEGORY_ROUTER_SYSTEM_PROMPT = """You are the first-stage category router for an Indian sourcing assistant.

Classify the query into exactly one supported category:
- "electronics"
- "medicine"
- "gold"

Rules:
- Use "gold" for bullion, coins, bars, jewellery, ornaments, precious metal buying/selling.
- Use "medicine" for medicines, pharmacy products, tablets, capsules, syrups, ointments, inhalers.
- Use "electronics" for all tech, appliances, chargers, gadgets, accessories, and also as the fallback when something does not fit the other two supported categories.

Return only JSON matching the schema.
""".strip()


def _generic_route(category: str) -> QueryRoute:
    normalized = (category or "").strip().lower() or "generic"
    return QueryRoute(
        category=normalized,
        subcategory_id=None,
        handler_key=f"{normalized}.generic",
        strategy_tags=("generic",),
    )


def route_handler_matches(query: StructuredQuery, handler_key: str) -> bool:
    return (query.route_handler or "").strip().lower() == handler_key


def apply_route_to_query(query: StructuredQuery, route: QueryRoute) -> StructuredQuery:
    query.category = route.category
    query.subcategory_id = route.subcategory_id
    query.route_handler = route.handler_key
    return query


async def _classify_top_level_category(query: StructuredQuery) -> str:
    existing = (query.category or "").strip().lower()
    if _looks_like_gold_bullion_text(query):
        return "gold"
    if existing in SUPPORTED_ROUTE_CATEGORIES and query.route_handler:
        return existing
    if not settings.openai_api_key:
        return existing if existing in SUPPORTED_ROUTE_CATEGORIES else "electronics"

    client = _get_openai_client()
    prompt = "\n".join(
        [
            f"Raw query: {query.raw_query}",
            f"Structured product: {query.product}",
            f"Existing category hint: {query.category or 'unknown'}",
            f"Location: {query.location or 'unknown'}",
        ]
    )
    try:
        response = await client.responses.parse(
            model=settings.openai_category_router_model or settings.openai_model,
            input=[
                {"role": "system", "content": CATEGORY_ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            text_format=CategoryRouteSelection,
            temperature=0,
        )
        selection = response.output_parsed
        if selection is None:
            raise ValueError("OpenAI returned no parsed CategoryRouteSelection.")
        return selection.category
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning("Category route classification failed, using fallback: %s", exc)
        return existing if existing in SUPPORTED_ROUTE_CATEGORIES else "electronics"


async def resolve_query_route(query: StructuredQuery) -> QueryRoute:
    if _looks_like_gold_bullion_text(query):
        query.category = "gold"
        from app.categories.gold.classifier import classify_gold_query_route

        return await classify_gold_query_route(query)

    if query.route_handler:
        return QueryRoute(
            category=(query.category or "").strip().lower() or "electronics",
            subcategory_id=query.subcategory_id,
            handler_key=query.route_handler,
            strategy_tags=tuple(tag for tag in ((query.category or "").strip().lower(), query.subcategory_id or "") if tag),
        )

    normalized_category = await _classify_top_level_category(query)
    query.category = normalized_category

    if normalized_category == "gold":
        from app.categories.gold.classifier import classify_gold_query_route

        return await classify_gold_query_route(query)
    if normalized_category == "electronics":
        from app.categories.electronics.classifier import classify_electronics_query_route

        return await classify_electronics_query_route(query)
    if normalized_category == "medicine":
        from app.categories.medicine.classifier import classify_medicine_query_route

        return await classify_medicine_query_route(query)
    return _generic_route(normalized_category)


async def enrich_query_route(query: StructuredQuery) -> StructuredQuery:
    route = await resolve_query_route(query)
    return apply_route_to_query(query, route)
