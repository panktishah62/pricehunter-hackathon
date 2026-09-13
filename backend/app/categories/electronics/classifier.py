from __future__ import annotations

from app.categories.router import QueryRoute
from app.models.schemas import StructuredQuery

ELECTRONICS_GENERIC_ROUTE = QueryRoute(
    category="electronics",
    subcategory_id="electronics_generic",
    handler_key="electronics.generic",
    strategy_tags=("electronics", "generic"),
)


async def classify_electronics_query_route(query: StructuredQuery) -> QueryRoute:
    return ELECTRONICS_GENERIC_ROUTE
