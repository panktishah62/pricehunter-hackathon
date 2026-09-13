from __future__ import annotations

from app.categories.router import QueryRoute
from app.models.schemas import StructuredQuery

MEDICINE_GENERIC_ROUTE = QueryRoute(
    category="medicine",
    subcategory_id="medicine_generic",
    handler_key="medicine.generic",
    strategy_tags=("medicine", "generic"),
)


async def classify_medicine_query_route(query: StructuredQuery) -> QueryRoute:
    return MEDICINE_GENERIC_ROUTE
