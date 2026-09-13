from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
import re

from app.categories.gold.planner import build_gold_query_plan
from app.database import db
from app.models.schemas import StructuredQuery, UnifiedResult
from app.services import persistence
from app.whatsapp_flows.models import (
    WhatsAppFlowDataExchangeRequest,
    WhatsAppFlowRateRow,
    WhatsAppFlowScreenResponse,
)

logger = logging.getLogger(__name__)

vendor_searches_collection = db["vendor_searches"]

# The Flow renders read-only (refresh=False) because the data_exchange call has
# a hard timeout — a synchronous live fetch would show "could not load content"
# (see build_gold_query_plan). To still cut staleness for cities the 6-min
# background poller doesn't cover, we fire a NON-BLOCKING refresh when the board
# is stale/empty so the next open is fresh. Guarded by an age threshold so a
# freshly-polled city doesn't trigger a redundant fetch on every open.
_FLOW_REFRESH_STALE_AFTER = timedelta(minutes=15)
_flow_refresh_tasks: set[asyncio.Task] = set()
# Cities with a refresh currently in flight — a per-city concurrency guard so
# repeated reopens of a stale board don't stack up parallel fetches for the
# same city (the task set is only a GC anchor, not a dedup mechanism).
_flow_refresh_inflight: set[str] = set()


def _plan_needs_background_refresh(live_results: list[UnifiedResult]) -> bool:
    if not live_results:
        return True
    newest: datetime | None = None
    for result in live_results:
        ts = result.timestamp
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if newest is None or ts > newest:
            newest = ts
    if newest is None:
        return True
    return datetime.now(timezone.utc) - newest > _FLOW_REFRESH_STALE_AFTER


def _schedule_background_city_refresh(query: StructuredQuery) -> None:
    """Fire-and-forget live-rate refresh for this city so the next Flow open is
    fresh, without blocking the current (timeout-bound) data_exchange response.

    De-duplicated per city so repeated reopens of a stale board don't stack up
    concurrent fetches. Must be called from within a running event loop
    (build_response is async); no-ops with a log otherwise."""
    from app.categories.gold.live_rates import refresh_website_live_rates_for_query

    city_key = (query.location or "").strip().lower() or "_all"
    if city_key in _flow_refresh_inflight:
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - defensive: only called from async build_response
        logger.warning("flow background refresh skipped: no running event loop")
        return

    async def _run() -> None:
        try:
            await refresh_website_live_rates_for_query(query)
        except Exception:  # pragma: no cover - best effort, never surface to the Flow
            logger.warning("flow background live-rate refresh failed", exc_info=True)
        finally:
            _flow_refresh_inflight.discard(city_key)

    _flow_refresh_inflight.add(city_key)
    task = loop.create_task(_run())
    _flow_refresh_tasks.add(task)
    task.add_done_callback(_flow_refresh_tasks.discard)


def _format_currency(value: float | None) -> str:
    if value is None:
        return "Rate pending"
    return f"Rs {value:,.0f}"


def _relative_updated_at(result: UnifiedResult) -> str:
    ts = result.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = max((datetime.now(timezone.utc) - ts).total_seconds(), 0.0)
    if age < 60:
        return "Updated just now"
    if age < 3600:
        return f"Updated {int(age // 60)} min ago"
    return f"Updated {int(age // 3600)} hr ago"


def _extract_script_name(result: UnifiedResult) -> str:
    if " | " in result.name:
        return result.name.split(" | ", 1)[1].strip()
    if result.notes:
        match = re.search(r"from .*?\|\s*buy=.*?\s*sell=.*?\s*\|\s*updated=", result.notes)
        if match:
            return "Live Script"
    return "Live Rate"


def _extract_dealer_name(result: UnifiedResult) -> str:
    if " | " in result.name:
        return result.name.split(" | ", 1)[0].strip()
    return result.name


def _row_from_result(result: UnifiedResult, index: int) -> WhatsAppFlowRateRow:
    script_name = _extract_script_name(result)
    meta_parts = [script_name, _relative_updated_at(result)]
    if result.phone:
        meta_parts.append("Call available")
    if result.url:
        meta_parts.append("Website")
    return WhatsAppFlowRateRow(
        row_id=result.id or f"row-{index}",
        dealer_name=_extract_dealer_name(result),
        rate_text=_format_currency(result.price),
        meta_text=" | ".join(meta_parts),
        action_text="Select dealer",
    )


def _normalize_search_id(search_id: str | None, flow_token: str | None) -> str:
    candidate = (search_id or "").strip()
    if candidate:
        return candidate
    token = (flow_token or "").strip()
    if not token:
        return ""
    if ":" in token:
        parts = token.split(":")
        if len(parts) >= 2 and parts[1].strip():
            return parts[1].strip()
    return token


def _build_structured_query_from_search_doc(search_doc: dict) -> StructuredQuery:
    if search_doc.get("query"):
        return StructuredQuery.model_validate(search_doc["query"])

    product = str(search_doc.get("product") or "")
    collected = search_doc.get("collected") or {}
    location = str(search_doc.get("location") or collected.get("location") or "unknown")
    return StructuredQuery(
        product=product or str(collected.get("product") or "gold"),
        category=str(search_doc.get("category") or collected.get("category") or "gold"),
        location=location,
        intent=str(search_doc.get("intent") or collected.get("intent") or "cheapest"),
        urgency=str(search_doc.get("urgency") or collected.get("urgency") or "immediate"),
        raw_query=str(
            search_doc.get("raw_query")
            or collected.get("raw_query")
            or f"{product or 'gold'} in {location}"
        ),
        subcategory_id=search_doc.get("subcategory_id") or collected.get("subcategory_id"),
        route_handler=search_doc.get("route_handler") or collected.get("route_handler"),
    )


async def _load_search_doc(search_id: str) -> dict | None:
    search_doc = await vendor_searches_collection.find_one({"search_id": search_id})
    if search_doc:
        return search_doc
    return await persistence.get_search_session(search_id)


def build_gold_live_rates_screen_data(
    *,
    product: str,
    city: str,
    live_results: list[UnifiedResult],
    outreach_count: int = 0,
    search_id: str = "",
    offset: int = 0,
) -> dict:
    """Build the scalar screen-data keys the deployed Flow template binds
    (title, subtitle, summary, best_rate_value, updated_label, row_1..row_8,
    result_offset, search_id, outreach_vendor_count).

    Single source of truth so the navigate-time send (baked-in data) and the
    data_exchange refresh produce IDENTICAL keys — a past mismatch (old
    result_1..3 keys vs the row_1..8 template) rendered the board blank.
    """
    rows = [_row_from_result(result, index) for index, result in enumerate(live_results[:8], start=1)]
    row_texts: dict[str, str] = {}
    for index in range(1, 9):
        if index <= len(rows):
            row = rows[index - 1]
            row_texts[f"row_{index}"] = f"{index}. {row.dealer_name} | {row.rate_text}"
        else:
            row_texts[f"row_{index}"] = ""
    best_result = live_results[0] if live_results else None
    return {
        "title": "Live Gold Rates",
        "subtitle": f"{product} · {city}",
        "summary": f"{len(live_results)} live dealer rates found",
        "best_rate_label": "Best rate",
        "best_rate_value": _format_currency(best_result.price if best_result else None),
        "updated_label": _relative_updated_at(best_result) if best_result else "No live updates yet",
        **row_texts,
        "result_offset": offset,
        "search_id": search_id,
        "outreach_vendor_count": outreach_count,
    }


class GoldLiveRatesFlowProvider:
    flow_key = "gold_live_rates"
    flow_id_setting_name = "whatsapp_gold_live_rates_flow_id"

    async def build_response(self, request: WhatsAppFlowDataExchangeRequest) -> WhatsAppFlowScreenResponse:
        search_id = _normalize_search_id(
            str(request.data.get("search_id") or "").strip(),
            request.flow_token,
        )
        if not search_id:
            return WhatsAppFlowScreenResponse(
                screen="GOLD_LIVE_RATES",
                data={
                    "title": "Live Gold Rates",
                    "summary": "Missing search context.",
                    "rows": [],
                    "actions": [],
                },
            )

        search_doc = await _load_search_doc(search_id)
        if not search_doc:
            return WhatsAppFlowScreenResponse(
                screen="GOLD_LIVE_RATES",
                data={
                    "title": "Live Gold Rates",
                    "summary": "This rate board is no longer available.",
                    "rows": [],
                    "actions": [],
                },
            )

        query = _build_structured_query_from_search_doc(search_doc)
        # Build the board directly rather than via plan_gold_bullion_query: this
        # flow IS the gold live-rates board, so it must render for ANY gold rate
        # query — including "gold rate" ones the classifier routes to
        # gold_generic (plan_gold_bullion_query gates on is_gold_bullion_query
        # and would return an empty board for those). refresh=False keeps it fast
        # (the Flow data_exchange times out and shows "could not load content" if
        # slow); the 6-min background poller keeps snapshots fresh.
        plan = await build_gold_query_plan(query, refresh=False)

        # Read-only render, but if the board is stale/empty (a city the 6-min
        # poller doesn't cover), kick off a background refresh so the next open
        # is fresh — never blocking this timeout-bound response.
        if _plan_needs_background_refresh(plan.live_results):
            _schedule_background_city_refresh(query)

        if search_doc.get("search_id") and search_doc.get("phone") is not None:
            await vendor_searches_collection.update_one(
                {"search_id": search_id},
                {
                    "$set": {
                        "online_results": [result.model_dump(mode="json") for result in plan.live_results],
                        "last_flow_refresh_at": datetime.now(timezone.utc),
                    }
                },
            )

        # The deployed Flow template binds only the scalar keys the shared
        # builder emits (row_1..8 / best_rate_value / …); the old "rows" array
        # and "actions" were never bound by the template (the RadioButtonsGroup
        # is a static data-source), so they're dropped — and with them the
        # duplicate _row_from_result pass.
        screen_data = build_gold_live_rates_screen_data(
            product=query.product or "Gold",
            city=plan.city or query.location or "your location",
            live_results=plan.live_results,
            outreach_count=len(plan.outreach_vendors),
            search_id=search_id,
        )
        return WhatsAppFlowScreenResponse(screen="GOLD_LIVE_RATES", data=screen_data)
