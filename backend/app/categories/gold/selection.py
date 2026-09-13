from __future__ import annotations

from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo
from app.categories.gold.classifier import is_gold_bullion_query
from app.categories.gold.planner import (
    GoldQueryPlan,
    build_gold_query_plan,
    extract_quantity_grams,
    infer_buyer_type,
)


async def select_gold_bullion_vendors(query: StructuredQuery) -> list[VendorInfo]:
    if not is_gold_bullion_query(query):
        return []
    plan = await build_gold_query_plan(query)
    return plan.discovered_vendors


async def select_gold_bullion_live_results(query: StructuredQuery) -> list[UnifiedResult]:
    if not is_gold_bullion_query(query):
        return []
    plan = await build_gold_query_plan(query)
    return plan.live_results


async def plan_gold_bullion_query(query: StructuredQuery, *, refresh: bool = True) -> GoldQueryPlan:
    if not is_gold_bullion_query(query):
        return GoldQueryPlan(city=query.location)
    return await build_gold_query_plan(query, refresh=refresh)
