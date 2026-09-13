from __future__ import annotations

from app.models.schemas import StructuredQuery
from app.categories.gold.classifier import GOLD_JEWELLERY_ROUTE, classify_gold_query_route


def is_gold_jewellery_query(query: StructuredQuery) -> bool:
    return classify_gold_query_route(query).handler_key == GOLD_JEWELLERY_ROUTE.handler_key
