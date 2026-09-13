from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.categories.router import resolve_query_route
from app.categories.gold.selection import plan_gold_bullion_query
from app.models.schemas import StructuredQuery, UnifiedResult, VoiceCallResult
from app.services import persistence
from app.services.gold_vendor_ingestion import infer_gold_category_id, record_vendor_call_outcome
from app.services.product_vendor_routing import find_vendor_offerings, record_price_observation
from app.services.product_registry import classify_product_type
from app.services.vendor_discovery import discover_vendors
from app.services.voice_agent import call_all_vendors, poll_call_result
from app.services.voice_extractor import (
    schema_from_call,
    extract_from_call_result,
    record_call_for_reliability,
)

logger = logging.getLogger(__name__)


async def _persist_safely(coroutine, context: str) -> None:
    try:
        await coroutine
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Persistence skipped for %s: %s", context, exc)


async def _resolve_call(call: VoiceCallResult) -> VoiceCallResult:
    return await poll_call_result(call)


async def run(query: StructuredQuery, search_id: str | None = None) -> list[UnifiedResult]:
    logger.info("Starting offline pipeline for %s", query.product)
    location = query.location if query.location != "unknown" else "Rajkot"
    live_results: list[UnifiedResult] = []
    if (await resolve_query_route(query)).handler_key == "gold.bullion":
        gold_plan = await plan_gold_bullion_query(query)
        vendors = gold_plan.outreach_vendors or gold_plan.discovered_vendors
        live_results = gold_plan.live_results
    else:
        route = await find_vendor_offerings(query)
        live_results = route.to_results()
        vendors = route.to_vendors()
        if not vendors:
            vendors = await discover_vendors(query.product, query.category, location)
    product_type_id = classify_product_type(query.product, query.category).product_type_id
    gold_category_id = infer_gold_category_id(query.product, query.category, product_type_id)

    if not vendors:
        if live_results:
            logger.info("Offline pipeline found no callable vendors but has %s live bullion results.", len(live_results))
            return live_results
        logger.warning("Offline pipeline found no vendors.")
        return []

    if settings.test_call_phone.strip():
        logger.info(
            "Offline test-call mode is enabled; limiting vendor calls from %s to 1.",
            len(vendors),
        )
        vendors = vendors[:1]

    calls = await call_all_vendors(vendors, query.product)
    completed_calls = await asyncio.gather(*[_resolve_call(call) for call in calls], return_exceptions=True)

    results: list[UnifiedResult] = list(live_results)
    extraction_tasks = []
    successful_calls: list[VoiceCallResult] = []

    for completed in completed_calls:
        if isinstance(completed, Exception):
            logger.warning("Call resolution failed: %s", completed)
            continue
        if search_id:
            await _persist_safely(
                persistence.record_call_attempt(
                    search_id=search_id,
                    query=query,
                    call=completed,
                ),
                f"call attempt {completed.call_id}",
            )
        if completed.status != "completed" or not completed.transcript:
            if not completed.extracted_data:
                logger.info("Skipping call %s with status=%s", completed.call_id, completed.status)
                # Still record reliability for no_answer/busy/failed so the vendor's
                # pickup_β reflects reality.
                try:
                    await record_call_for_reliability(
                        call=completed,
                        result=None,
                        category=getattr(query, "category", None),
                        product_key=None,
                        location_pincode=None,
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    logger.debug("reliability skip-update failed: %s", exc)
                continue
        successful_calls.append(completed)
        extraction_tasks.append(
            extract_from_call_result(
                completed,
                query.product,
                category=getattr(query, "category", None),
                product_key=None,
                location_pincode=None,
                extraction_schema=schema_from_call(completed),
            )
        )

    extracted = await asyncio.gather(*extraction_tasks, return_exceptions=True)
    for call, item in zip(successful_calls, extracted, strict=False):
        if isinstance(item, Exception):
            logger.warning("Transcript extraction failed for call %s: %s", call.call_id, item)
            continue
        if search_id:
            await _persist_safely(
                persistence.record_call_attempt(
                    search_id=search_id,
                    query=query,
                    call=call,
                    extracted_result=item,
                ),
                f"call result {call.call_id}",
            )
        if gold_category_id and call.vendor.vendor_id and search_id:
            await _persist_safely(
                record_vendor_call_outcome(
                    vendor_id=call.vendor.vendor_id,
                    query_id=search_id,
                    category_id=gold_category_id,
                    outcome=call.status,
                    duration=call.duration_seconds,
                    quote_received=item.price is not None,
                    quoted_price=item.price,
                    benchmark_rate=None,
                    negotiated=item.negotiated,
                    final_price=item.price,
                    deal_closed=False,
                ),
                f"gold vendor call outcome {call.call_id}",
            )
        if call.vendor.vendor_id and call.vendor.offering_id and call.vendor.category_id and search_id:
            await _persist_safely(
                record_price_observation(
                    vendor_id=call.vendor.vendor_id,
                    offering_id=call.vendor.offering_id,
                    product_id=call.vendor.product_id,
                    category_id=call.vendor.category_id,
                    query_id=search_id,
                    price=item.price,
                    currency=item.currency,
                    availability=item.availability,
                    source_type="call",
                    call_id=call.call_id,
                    notes=item.notes,
                    confidence=item.confidence,
                ),
                f"vendor offering price observation {call.call_id}",
            )
        results.append(item)

    logger.info("Offline pipeline completed with %s results", len(results))
    return results
