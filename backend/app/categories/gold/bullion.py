from __future__ import annotations

from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo
from app.categories.gold.classifier import is_gold_bullion_query
from app.categories.gold.planner import GoldQueryPlan, build_gold_query_plan


async def build_bullion_query_plan(query: StructuredQuery) -> GoldQueryPlan:
    if not is_gold_bullion_query(query):
        return GoldQueryPlan(city=query.location)
    return await build_gold_query_plan(query)


async def select_bullion_vendors(query: StructuredQuery) -> list[VendorInfo]:
    plan = await build_bullion_query_plan(query)
    return plan.discovered_vendors


async def select_bullion_live_results(query: StructuredQuery) -> list[UnifiedResult]:
    plan = await build_bullion_query_plan(query)
    return plan.live_results
