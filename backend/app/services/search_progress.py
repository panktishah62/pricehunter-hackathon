from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from app.config import settings
from app.categories.router import apply_route_to_query, resolve_query_route
from app.models.schemas import (
    BrandFallbackPrompt,
    OtherCityPrompt,
    SearchMoreResultsResponse,
    SearchProgressSnapshot,
    SearchProgressStep,
    SearchResponse,
    SearchStrategy,
    StructuredQuery,
    UnifiedResult,
    VendorInfo,
    VoiceCallResult,
)
from app.services import brand_search, location_ranker, orchestrator, persistence, product_relevance, query_structurer, search_specs
from app.services.demo_always_call import demo_vendor_for_query
from app.services.gold_vendor_ingestion import infer_gold_category_id, record_vendor_call_outcome
from app.categories.gold.selection import plan_gold_bullion_query
from app.services.flash_compare import flash_platform_strategy, search_flash_compare
from app.services.gold_vendor_flow import is_gold_query, select_gold_vendors
from app.services.online_discovery import PlatformStrategy
from app.services.product_vendor_routing import record_price_observation
from app.services.product_registry import classify_product_type
from app.services.vendor_discovery import discover_vendors
from app.services.voice_agent import (
    call_vendor,
    current_voice_call_spacing_seconds,
    current_voice_concurrency_limit,
    current_voice_provider,
    poll_call_result,
)
from app.services.voice_extractor import (
    schema_from_call,
    extract_from_call_result,
    record_call_for_reliability,
)
from app.observability import capture_exception

logger = logging.getLogger(__name__)

_SEARCHES: dict[str, SearchProgressSnapshot] = {}
_TASKS: dict[str, asyncio.Task[None]] = {}
_LIVE_PERSIST_TASKS: dict[str, asyncio.Task[None]] = {}
DB_FIRST_MIN_RESULTS = 8
DB_FIRST_MIN_CALLABLE_VENDORS = 5
DB_FIRST_ROUTE_LIMIT = 2000
DB_FIRST_SUPPLIER_RESULT_LIMIT = 500
RESULT_PAGE_SIZE = 5
# Same-city DB offerings below this count trigger IndiaMART live (additive).
SAME_CITY_DB_IM_THRESHOLD = 10
_LIVE_PERSIST_DEBOUNCE_SECONDS = 0.75


def _arm_other_city_prompt(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    other_city_results: list[UnifiedResult],
) -> None:
    if not other_city_results:
        snapshot.pending_other_city_results = []
        snapshot.other_city_prompt = OtherCityPrompt(status="none")
        return
    cities = location_ranker.top_other_cities(other_city_results, limit=3)
    snapshot.pending_other_city_results = list(other_city_results)
    snapshot.other_city_prompt = OtherCityPrompt(
        status="awaiting",
        message=location_ranker.build_other_city_prompt_message(
            cities,
            other_count=len(other_city_results),
        ),
        cities=cities,
        other_city_count=len(other_city_results),
    )


def _arm_brand_fallback_prompt(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    *,
    same_city: list[UnifiedResult],
    other_city: list[UnifiedResult],
) -> None:
    same_city = list(same_city or [])
    other_city = list(other_city or [])
    if not same_city and not other_city:
        snapshot.pending_brand_fallback_same_city = []
        snapshot.pending_brand_fallback_other_city = []
        snapshot.brand_fallback_prompt = BrandFallbackPrompt(status="none")
        return
    snapshot.pending_brand_fallback_same_city = same_city
    snapshot.pending_brand_fallback_other_city = other_city
    snapshot.brand_fallback_prompt = BrandFallbackPrompt(
        status="awaiting",
        message=brand_search.brand_fallback_prompt_message(
            query,
            same_city_count=len(same_city),
            other_city_count=len(other_city),
        ),
        brand=(query.brand or "").strip() or None,
        same_city_count=len(same_city),
        other_city_count=len(other_city),
    )


def _filter_vendors_same_city(query: StructuredQuery, vendors: list[VendorInfo]) -> list[VendorInfo]:
    buyer_city = location_ranker.buyer_location(query).city
    if not buyer_city:
        return vendors
    return [
        vendor
        for vendor in vendors
        if location_ranker.cities_in_same_market(buyer_city, vendor.city)
    ]


def _use_legacy_gold_flow(query: StructuredQuery, *, handler_key: str | None = None) -> bool:
    if settings.gold_use_unified_taxonomy_flow:
        return False
    return (handler_key or "").strip().lower() == "gold.bullion" or is_gold_query(query)


async def _persist_safely(coroutine, context: str) -> None:
    try:
        await coroutine
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Persistence skipped for %s: %s", context, exc)


async def _persist_live_snapshot(snapshot: SearchProgressSnapshot) -> None:
    await _persist_safely(
        persistence.save_live_search_snapshot(snapshot),
        f"live snapshot {snapshot.search_id}",
    )


def _schedule_live_persist(snapshot: SearchProgressSnapshot) -> None:
    """Debounced Mongo write so other Cloud Run instances can hydrate this search."""
    search_id = snapshot.search_id
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    previous = _LIVE_PERSIST_TASKS.pop(search_id, None)
    if previous is not None and not previous.done():
        previous.cancel()

    async def _delayed() -> None:
        try:
            await asyncio.sleep(_LIVE_PERSIST_DEBOUNCE_SECONDS)
            current = _SEARCHES.get(search_id)
            if current is not None:
                await _persist_live_snapshot(current)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - best-effort background write
            logger.debug("Debounced live persist failed for %s", search_id, exc_info=True)
        finally:
            current_task = asyncio.current_task()
            if _LIVE_PERSIST_TASKS.get(search_id) is current_task:
                _LIVE_PERSIST_TASKS.pop(search_id, None)

    _LIVE_PERSIST_TASKS[search_id] = loop.create_task(_delayed())


async def resolve_snapshot(search_id: str) -> SearchProgressSnapshot | None:
    """Return in-memory snapshot, or hydrate from Mongo when this instance is cold."""
    cached = _SEARCHES.get(search_id)
    if cached is not None:
        return cached
    loaded = await persistence.load_live_search_snapshot(search_id)
    if loaded is None:
        return None
    # Another request may have hydrated concurrently — prefer the in-memory one.
    existing = _SEARCHES.get(search_id)
    if existing is not None:
        return existing
    _SEARCHES[search_id] = loaded
    logger.info(
        "Hydrated search %s from Mongo (status=%s pending_other_city=%s)",
        search_id,
        loaded.status,
        len(loaded.pending_other_city_results or []),
    )
    return loaded


async def _notify_whatsapp_completion(search_id: str, request_metadata: dict[str, str | None] | None) -> None:
    if (request_metadata or {}).get("source") != "whatsapp":
        return
    try:
        from app.services import whatsapp

        await whatsapp.send_completed_search_from_persistence(search_id)
    except Exception:  # pragma: no cover - external WhatsApp API
        logger.exception("WhatsApp completion delivery failed for search=%s", search_id)


async def _notify_whatsapp_other_city_prompt(
    snapshot: SearchProgressSnapshot,
    request_metadata: dict[str, str | None] | None,
) -> None:
    if (request_metadata or {}).get("source") != "whatsapp":
        return
    prompt = snapshot.other_city_prompt
    if not prompt or prompt.status != "awaiting" or not prompt.message:
        return
    phone = (request_metadata or {}).get("phone_number")
    if not phone:
        return
    try:
        from app.services.whatsapp import send_button_message

        await send_button_message(
            phone,
            prompt.message,
            buttons=[
                (f"other_city_yes:{snapshot.search_id}", "Yes, show them"),
                (f"other_city_no:{snapshot.search_id}", "No, thanks"),
            ],
            session_id=(request_metadata or {}).get("session_id"),
            search_id=snapshot.search_id,
        )
    except Exception:  # pragma: no cover - external WhatsApp API
        logger.exception("WhatsApp other-city prompt failed for search=%s", snapshot.search_id)


async def _notify_whatsapp_brand_fallback_prompt(
    snapshot: SearchProgressSnapshot,
    request_metadata: dict[str, str | None] | None,
) -> None:
    if (request_metadata or {}).get("source") != "whatsapp":
        return
    prompt = snapshot.brand_fallback_prompt
    if not prompt or prompt.status != "awaiting" or not prompt.message:
        return
    phone = (request_metadata or {}).get("phone_number")
    if not phone:
        return
    try:
        from app.services.whatsapp import send_button_message

        await send_button_message(
            phone,
            prompt.message,
            buttons=[
                (f"brand_fallback_yes:{snapshot.search_id}", "Yes, show others"),
                (f"brand_fallback_no:{snapshot.search_id}", "No, thanks"),
            ],
            session_id=(request_metadata or {}).get("session_id"),
            search_id=snapshot.search_id,
        )
    except Exception:  # pragma: no cover - external WhatsApp API
        logger.exception("WhatsApp brand-fallback prompt failed for search=%s", snapshot.search_id)


async def _run_with_semaphore(
    semaphore: asyncio.Semaphore,
    coroutine,
) -> list[UnifiedResult]:
    async with semaphore:
        return await coroutine


async def _run_vendor_with_rate_limit(
    semaphore: asyncio.Semaphore,
    coroutine,
    spacing_seconds: float,
) -> list[UnifiedResult]:
    async with semaphore:
        result = await coroutine
        if spacing_seconds > 0:
            await asyncio.sleep(spacing_seconds)
        return result


def _step_id(prefix: str, value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    collapsed = "-".join(chunk for chunk in normalized.split("-") if chunk)
    return f"{prefix}-{collapsed or 'item'}"


def _touch(snapshot: SearchProgressSnapshot) -> None:
    snapshot.updated_at = datetime.utcnow()
    _schedule_live_persist(snapshot)


def _set_step(
    snapshot: SearchProgressSnapshot,
    step_id: str,
    status: str,
    detail: str | None = None,
) -> None:
    for step in snapshot.steps:
        if step.id == step_id:
            step.status = status
            if detail is not None:
                step.detail = detail
            _touch(snapshot)
            return


def _ensure_online_steps(
    snapshot: SearchProgressSnapshot,
    *,
    detail: str | None = None,
) -> None:
    if not any(step.id == "online-discovery" for step in snapshot.steps):
        snapshot.steps.append(
            SearchProgressStep(
                id="online-discovery",
                label="Preparing online price search",
                status="completed",
                    detail=(
                    detail
                    or (
                        "No matching local suppliers yet — checking online stores next."
                        if settings.flash_compare_enabled
                        else "No online price provider is enabled."
                    )
                ),
            )
        )
    if settings.flash_compare_enabled and not any(step.id == "online-flash-compare" for step in snapshot.steps):
        snapshot.steps.append(
            SearchProgressStep(
                id="online-flash-compare",
                label="Searching online stores",
                status="pending",
                detail="Queued for live online price collection.",
            )
        )
    _touch(snapshot)


def _dedupe_results(results: list[UnifiedResult]) -> list[UnifiedResult]:
    deduped: list[UnifiedResult] = []
    seen: set[str] = set()
    for result in results:
        offering_id = (result.attributes or {}).get("offering_id") if result.result_type == "vendor_offering" else None
        key = (
            f"vendor_offering:{offering_id}"
            if offering_id
            else result.url or f"{result.source_type}:{result.name}:{result.price}:{result.phone}:{result.delivery_time}"
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped


def _result_product_title(result: UnifiedResult) -> str:
    preview = (result.attributes or {}).get("product_preview") if result.attributes else {}
    if isinstance(preview, dict) and preview.get("title"):
        return str(preview["title"])
    # DB results are named "Vendor | Product"; fall back to the product part.
    name = result.name or ""
    return name.split("|", 1)[1].strip() if "|" in name else name


def _rank_results_for_query(
    query: StructuredQuery,
    results: list[UnifiedResult],
    intent: str,
    *,
    category: str | None = None,
) -> list[UnifiedResult]:
    ranked = location_ranker.rank_results_by_location(query, results)
    if (category or query.category or "").strip().lower() == "gold":
        return ranked
    # Core products for the queried head noun outrank accessories/siblings,
    # even out-of-city ones: a real speaker from Nagpur beats a local earphone
    # that only matched via its "Audio & Speakers" category label. Both sorts
    # are stable, so within a tier the location order (and within that,
    # Algolia's order) is preserved.
    query_product = query.product or query.raw_query or ""
    return sorted(
        ranked,
        key=lambda result: search_specs.rank_key(
            query_product,
            _result_product_title(result),
            descriptors=query.descriptors or None,
            search_specs=query.search_specs or None,
            match_tier_fn=product_relevance.match_tier,
        ),
    )


def _set_visible_results(
    snapshot: SearchProgressSnapshot,
    ranked_results: list[UnifiedResult],
    *,
    visible_count: int = RESULT_PAGE_SIZE,
) -> None:
    visible_count = max(0, visible_count)
    snapshot.visible_result_count = visible_count
    snapshot.total_ranked_results = len(ranked_results)
    snapshot.next_result_offset = min(visible_count, len(ranked_results))
    snapshot.has_more_results = len(ranked_results) > snapshot.next_result_offset
    snapshot.partial_results = ranked_results[: snapshot.next_result_offset]


def _update_partial_results(
    snapshot: SearchProgressSnapshot,
    new_results: list[UnifiedResult],
    intent: str,
    *,
    category: str | None = None,
    grow_visible: bool = False,
) -> None:
    combined = _dedupe_results(snapshot.partial_results + new_results)
    ranked = _rank_results_for_query(snapshot.query, combined, intent, category=category)
    # Preserve pages the user already unlocked via "more quotes"; never dump the
    # full ranked catalog into the visible window (that skipped the prompt).
    # grow_visible previously meant "show every new IndiaMART row" and bypassed paging.
    _ = grow_visible
    visible_count = max(RESULT_PAGE_SIZE, snapshot.visible_result_count or RESULT_PAGE_SIZE)
    _set_visible_results(snapshot, ranked, visible_count=visible_count)
    _touch(snapshot)


def _has_sufficient_db_coverage(
    db_results: list[UnifiedResult],
    db_vendor_infos: list[VendorInfo],
) -> bool:
    callable_count = len({vendor.vendor_id or vendor.phone for vendor in db_vendor_infos if vendor.phone})
    unique_result_vendors = len({result.vendor_id or result.phone or result.name for result in db_results})
    priced_count = len([result for result in db_results if result.price is not None])
    return (
        callable_count >= DB_FIRST_MIN_CALLABLE_VENDORS
        or len(db_results) >= DB_FIRST_MIN_RESULTS
        or unique_result_vendors >= DB_FIRST_MIN_CALLABLE_VENDORS
        or priced_count >= DB_FIRST_MIN_CALLABLE_VENDORS
    )


def _offline_failure_detail(vendor_name: str, exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "domestic anchored terms not met" in lowered or "sip 403" in lowered:
        return (
            f"I reached {vendor_name}, but the calling provider blocked this local call route, "
            "so I could not collect a quote."
        )
    if "service unavailable" in lowered or "sip 503" in lowered:
        return f"I tried calling {vendor_name}, but the telephony route was temporarily unavailable."
    return f"I could not reach {vendor_name} for a quote."


def _build_steps(
    query: StructuredQuery,
    search_strategy: SearchStrategy,
    vendors: list[VendorInfo],
    platforms: list[PlatformStrategy],
    *,
    vendor_discovery_pending: bool = False,
) -> list[SearchProgressStep]:
    if _use_legacy_gold_flow(query):
        steps = [
            SearchProgressStep(
                id="understanding",
                label=f"Understanding {query.product} in {query.location}",
                status="completed",
                detail="Gold sourcing brief locked in.",
            ),
            SearchProgressStep(
                id="vendor-discovery",
                label=f"Ranking live and local gold vendors in {query.location}",
                status="running" if not vendor_discovery_pending else "pending",
                detail="City live scripts are being prioritized before popular local dealers.",
            ),
        ]
        if search_strategy in {"both", "offline"}:
            for vendor in vendors:
                steps.append(
                    SearchProgressStep(
                        id=_step_id("offline", vendor.name),
                        label=f"Calling {vendor.name}",
                        status="pending",
                        detail=f"Queued {vendor.name} for a price check.",
                    )
                )
        return steps

    steps = [
        SearchProgressStep(
            id="understanding",
            label=f"Understanding {query.product} in {query.location}",
            status="completed",
            detail="Search brief locked in.",
        )
    ]

    if search_strategy in {"both", "offline"}:
        if vendor_discovery_pending:
            steps.append(
                SearchProgressStep(
                    id="vendor-discovery",
                    label="Finding nearby offline vendors",
                    status="pending",
                    detail="Vendor discovery will run in the background.",
                )
            )
        else:
            steps.append(
                SearchProgressStep(
                    id="vendor-discovery",
                    label=f"Found {len(vendors)} nearby offline vendors",
                    status="completed",
                    detail="Vendor shortlist is ready.",
                )
            )
            if not settings.gold_use_unified_taxonomy_flow and (query.route_handler or "").strip().lower() == "gold.bullion":
                steps.append(
                    SearchProgressStep(
                        id="gold-live-rates",
                        label="Fetching live bullion dealer website rates",
                        status="pending",
                        detail="Checking cached live scripts for this city first.",
                    )
                )
            for vendor in vendors:
                steps.append(
                    SearchProgressStep(
                        id=_step_id("offline", vendor.name),
                        label=f"Calling {vendor.name}",
                        status="pending",
                        detail=f"Queued {vendor.name} for a price check.",
                    )
                )

    return steps


def _replace_vendor_steps_after_discovery(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    search_strategy: SearchStrategy,
    vendors: list[VendorInfo],
    platforms: list[PlatformStrategy],
) -> None:
    """Add discovered vendor call steps without clobbering already-running online steps."""

    existing_by_id = {step.id: step for step in snapshot.steps}
    next_steps = _build_steps(query, search_strategy, vendors, platforms)
    merged_steps = list(snapshot.steps)
    merged_index_by_id = {step.id: index for index, step in enumerate(merged_steps)}
    for step in next_steps:
        existing = existing_by_id.get(step.id)
        if existing is None:
            merged_index_by_id[step.id] = len(merged_steps)
            merged_steps.append(step)
        elif step.id == "understanding" or step.id.startswith("online-"):
            continue
        else:
            merged_steps[merged_index_by_id[step.id]] = step
    snapshot.steps = merged_steps


def _append_demo_vendor_for_calling(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    vendors_to_contact: list[VendorInfo],
) -> list[VendorInfo]:
    """Append the demo call target without changing normal discovery/ranking."""

    demo_vendor = demo_vendor_for_query(query)
    if demo_vendor is None:
        return vendors_to_contact

    if any(vendor.phone == demo_vendor.phone for vendor in vendors_to_contact):
        return vendors_to_contact

    if not any(vendor.phone == demo_vendor.phone for vendor in snapshot.discovered_vendors):
        snapshot.discovered_vendors.append(demo_vendor)

    step_id = _step_id("offline", demo_vendor.name)
    if not any(step.id == step_id for step in snapshot.steps):
        snapshot.steps.append(
            SearchProgressStep(
                id=step_id,
                label=f"Calling {demo_vendor.name}",
                status="pending",
                detail=f"Queued {demo_vendor.name} for a price check.",
            )
        )
    return [*vendors_to_contact, demo_vendor]


async def _resolve_vendor(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    vendor: VendorInfo,
) -> list[UnifiedResult]:
    step_id = _step_id("offline", vendor.name)
    _set_step(snapshot, step_id, "running", f"Calling {vendor.name} now for live pricing and availability.")
    try:
        # Pass the full structured query (not just the product string) so the
        # Pipecat/Gemini-Live campaign config picks up quantity, category,
        # location, use case, GST, and urgency. Without `query=` the config
        # builder receives query=None and silently falls back to retail
        # single-piece defaults.
        call = await call_vendor(vendor, query.product, query=query)
        await _persist_safely(
            persistence.record_call_attempt(
                search_id=snapshot.search_id,
                query=query,
                call=call,
            ),
            f"call start {call.call_id}",
        )
        completed = await poll_call_result(call)
        if completed.is_mock:
            await _persist_safely(
                persistence.record_call_attempt(
                    search_id=snapshot.search_id,
                    query=query,
                    call=completed,
                ),
                f"mock call skipped {completed.call_id}",
            )
            logger.info(
                "Skipping mock voice result for vendor=%s provider=%s call_id=%s",
                vendor.name,
                completed.provider,
                completed.call_id,
            )
            _set_step(snapshot, step_id, "failed", "Skipped mock call result; no live quote was collected.")
            return []
        if completed.status != "completed" and not completed.extracted_data:
            await _persist_safely(
                persistence.record_call_attempt(
                    search_id=snapshot.search_id,
                    query=query,
                    call=completed,
                ),
                f"call completion {completed.call_id}",
            )
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
            _set_step(snapshot, step_id, "failed", f"Call ended with status: {completed.status}.")
            return []
        if not completed.transcript and not completed.extracted_data:
            await _persist_safely(
                persistence.record_call_attempt(
                    search_id=snapshot.search_id,
                    query=query,
                    call=completed,
                ),
                f"call completion {completed.call_id}",
            )
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
            _set_step(snapshot, step_id, "failed", "Call completed without usable result data.")
            return []

        result = await extract_from_call_result(
            completed,
            query.product,
            category=getattr(query, "category", None),
            product_key=None,
            location_pincode=None,
            extraction_schema=schema_from_call(completed),
        )
        await _persist_safely(
            persistence.record_call_attempt(
                search_id=snapshot.search_id,
                query=query,
                call=completed,
                extracted_result=result,
            ),
            f"call result {completed.call_id}",
        )
        product_type_id = classify_product_type(query.product, query.category).product_type_id
        gold_category_id = infer_gold_category_id(query.product, query.category, product_type_id)
        if gold_category_id and vendor.vendor_id:
            await _persist_safely(
                record_vendor_call_outcome(
                    vendor_id=vendor.vendor_id,
                    query_id=snapshot.search_id,
                    category_id=gold_category_id,
                    outcome=completed.status,
                    duration=completed.duration_seconds,
                    quote_received=result.price is not None,
                    quoted_price=result.price,
                    benchmark_rate=None,
                    negotiated=result.negotiated,
                    final_price=result.price,
                    deal_closed=False,
                ),
                f"gold vendor call outcome {completed.call_id}",
            )
        if vendor.vendor_id and vendor.offering_id and vendor.category_id:
            await _persist_safely(
                record_price_observation(
                    vendor_id=vendor.vendor_id,
                    offering_id=vendor.offering_id,
                    product_id=vendor.product_id,
                    category_id=vendor.category_id,
                    query_id=snapshot.search_id,
                    price=result.price,
                    currency=result.currency,
                    availability=result.availability,
                    source_type="call",
                    call_id=completed.call_id,
                    notes=result.notes,
                    confidence=result.confidence,
                ),
                f"vendor offering price observation {completed.call_id}",
            )
        _update_partial_results(snapshot, [result], query.intent, category=getattr(query, 'category', None))
        detail = f"Received an availability update from {vendor.name}."
        if result.price is not None:
            detail = f"Received a quote from {vendor.name} at INR {result.price:,.0f}."
        _set_step(snapshot, step_id, "completed", detail)
        return [result]
    except Exception as exc:  # pragma: no cover - external integrations
        logger.warning("Offline vendor resolution failed for %s: %s", vendor.name, exc)
        # A failed call here (e.g. the agent's /start rejecting the request)
        # previously left no Sentry error and no call_attempts row, so the call
        # rail could go dark silently. Report it as an exception so it alerts.
        capture_exception(
            exc,
            phase="resolve_vendor",
            search_id=snapshot.search_id,
            vendor_name=vendor.name,
            category=getattr(query, "category", None),
        )
        _set_step(snapshot, step_id, "failed", _offline_failure_detail(vendor.name, exc))
        return []


async def _resolve_flash_compare(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
) -> list[UnifiedResult]:
    step_id = "online-flash-compare"
    _set_step(snapshot, step_id, "running", "Searching major online stores and collecting direct product links.")
    try:
        results = await search_flash_compare(query)
        await _persist_safely(
            persistence.record_online_results(
                search_id=snapshot.search_id,
                query=query,
                strategy=flash_platform_strategy(),
                results=results,
            ),
            "flash compare results",
        )
        _update_partial_results(snapshot, results, query.intent, category=getattr(query, 'category', None))
        detail = (
            f"Found {len(results)} live online price match{'es' if len(results) != 1 else ''}."
            if results
            else "No online prices were found from the marketplaces checked."
        )
        _set_step(snapshot, step_id, "completed", detail)
        return results
    except Exception as exc:  # pragma: no cover - external integrations
        logger.warning("Flash compare fetch failed: %s", exc)
        _set_step(snapshot, step_id, "failed", "I could not finish the online marketplace search.")
        return []


async def _run_gold_background_search(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    search_strategy: SearchStrategy,
    platforms: list[PlatformStrategy],
) -> None:
    plan = await plan_gold_bullion_query(query)
    snapshot.discovered_vendors = plan.discovered_vendors
    _replace_vendor_steps_after_discovery(snapshot, query, search_strategy, plan.discovered_vendors, platforms)
    _set_step(
        snapshot,
        "vendor-discovery",
        "completed",
        f"Prepared {len(plan.discovered_vendors)} bullion vendors ranked by live scripts, city, and confidence.",
    )

    _set_step(snapshot, "gold-live-rates", "running", "Loading same-city live bullion website rates.")
    if plan.live_results:
        _update_partial_results(snapshot, plan.live_results, query.intent, category=getattr(query, 'category', None))
        refresh_note = ""
        if plan.live_refresh_summary.refreshed:
            refresh_note = (
                f" Refreshed {plan.live_refresh_summary.refreshed} website feed"
                f"{'s' if plan.live_refresh_summary.refreshed != 1 else ''} on demand."
            )
        _set_step(
            snapshot,
            "gold-live-rates",
            "completed",
            (
                f"Loaded {len(plan.live_results)} live bullion rate script"
                f"{'s' if len(plan.live_results) != 1 else ''}.{refresh_note}"
            ).strip(),
        )
    else:
        refresh_note = ""
        if plan.live_refresh_summary.attempted:
            refresh_note = (
                f" Tried {plan.live_refresh_summary.attempted} website feed"
                f"{'s' if plan.live_refresh_summary.attempted != 1 else ''}"
                f" and refreshed {plan.live_refresh_summary.refreshed}."
            )
        _set_step(
            snapshot,
            "gold-live-rates",
            "completed",
            (
                "No same-city live script snapshot is available yet, so continuing with trusted dealer outreach."
                f"{refresh_note}"
            ).strip(),
        )

    offline_results: list[UnifiedResult] = []
    should_call_vendors = search_strategy in {"both", "offline"}
    if should_call_vendors and plan.outreach_vendors:
        vendors_to_contact = plan.outreach_vendors[:1] if settings.test_call_phone.strip() else plan.outreach_vendors
        vendors_to_contact = _append_demo_vendor_for_calling(snapshot, query, vendors_to_contact)
        concurrency_limit = current_voice_concurrency_limit()
        spacing_seconds = current_voice_call_spacing_seconds()
        semaphore = asyncio.Semaphore(concurrency_limit)
        vendor_tasks = [
            asyncio.create_task(
                _run_vendor_with_rate_limit(
                    semaphore,
                    _resolve_vendor(snapshot, query, vendor),
                    spacing_seconds,
                )
            )
            for vendor in vendors_to_contact
        ]
        vendor_completed = await asyncio.gather(*vendor_tasks, return_exceptions=True)
        for item in vendor_completed:
            if isinstance(item, Exception):
                logger.warning("Gold vendor call task failed: %s", item)
                continue
            offline_results.extend(item)

    all_results = _rank_results_for_query(
        query,
        _dedupe_results(plan.live_results + offline_results),
        query.intent,
        category=getattr(query, "category", None),
    )
    snapshot.final_results = SearchResponse(
        query=query,
        results=all_results,
        online_count=len(plan.live_results),
        offline_count=len(offline_results),
        total_time_seconds=round(time.time() - snapshot.started_at.timestamp(), 2),
        search_strategy=search_strategy,
    )
    _set_visible_results(snapshot, all_results)
    snapshot.status = "completed"
    _touch(snapshot)


async def _run_gold_selection_call_search(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    search_strategy: SearchStrategy,
    *,
    request_metadata: dict[str, str | None] | None = None,
) -> None:
    started = snapshot.started_at.timestamp()
    offline_results: list[UnifiedResult] = []
    try:
        should_call_vendors = search_strategy in {"both", "offline"}
        if should_call_vendors and snapshot.discovered_vendors:
            vendors_to_contact = (
                snapshot.discovered_vendors[:1]
                if settings.test_call_phone.strip()
                else snapshot.discovered_vendors
            )
            vendors_to_contact = _append_demo_vendor_for_calling(snapshot, query, vendors_to_contact)
            concurrency_limit = current_voice_concurrency_limit()
            spacing_seconds = current_voice_call_spacing_seconds()
            semaphore = asyncio.Semaphore(concurrency_limit)
            vendor_tasks = [
                asyncio.create_task(
                    _run_vendor_with_rate_limit(
                        semaphore,
                        _resolve_vendor(snapshot, query, vendor),
                        spacing_seconds,
                    )
                )
                for vendor in vendors_to_contact
            ]
            vendor_completed = await asyncio.gather(*vendor_tasks, return_exceptions=True)
            for item in vendor_completed:
                if isinstance(item, Exception):
                    logger.warning("Gold vendor call task failed: %s", item)
                    continue
                offline_results.extend(item)

        ranked = _rank_results_for_query(
            query,
            _dedupe_results(snapshot.partial_results + offline_results),
            query.intent,
            category=getattr(query, "category", None),
        )
        snapshot.final_results = SearchResponse(
            query=query,
            results=ranked,
            online_count=len([result for result in ranked if result.result_type == "live_rate"]),
            offline_count=len([result for result in ranked if result.result_type != "live_rate"]),
            total_time_seconds=round(time.time() - started, 2),
            search_strategy=search_strategy,
        )
        _set_visible_results(snapshot, ranked)
        snapshot.status = "completed"
        _touch(snapshot)
        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=query,
                final_results=ranked,
                status="completed",
                total_time_seconds=round(time.time() - started, 2),
            ),
            f"search completion {snapshot.search_id}",
        )
        await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
    except Exception as exc:  # pragma: no cover - background orchestration
        logger.exception("Gold selection call search failed for %s", query.product)
        snapshot.status = "failed"
        snapshot.error = str(exc)
        _touch(snapshot)
        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=query,
                final_results=snapshot.partial_results,
                status="failed",
                error=str(exc),
                total_time_seconds=round(time.time() - started, 2),
            ),
            f"search failure {snapshot.search_id}",
        )
        await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
    finally:
        _TASKS.pop(snapshot.search_id, None)


async def _run_background_search(
    snapshot: SearchProgressSnapshot,
    query: StructuredQuery,
    search_strategy: SearchStrategy,
    vendors: list[VendorInfo],
    platforms: list[PlatformStrategy],
    *,
    defer_vendor_discovery: bool = False,
    request_metadata: dict[str, str | None] | None = None,
    session_id: str | None = None,
) -> None:
    started = snapshot.started_at.timestamp()
    snapshot.status = "running"
    _touch(snapshot)

    online_results: list[UnifiedResult] = []
    offline_results: list[UnifiedResult] = []

    try:
        background_route = await resolve_query_route(query)
        if _use_legacy_gold_flow(query, handler_key=background_route.handler_key):
            await _run_gold_background_search(snapshot, query, search_strategy, platforms)
            final_results = snapshot.final_results.results if snapshot.final_results else snapshot.partial_results
            await _persist_safely(
                persistence.complete_search_session(
                    search_id=snapshot.search_id,
                    query=query,
                    final_results=final_results,
                    status="completed",
                    total_time_seconds=round(time.time() - started, 2),
                ),
                f"search completion {snapshot.search_id}",
            )
            await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
            return

        # Lazy imports to avoid circular dependency with vendor_search
        from app.services.product_vendor_routing import find_gold_live_rate_results, find_vendor_offerings
        from app.services.vendor_search import (
            generate_search_queries,
            search_google_places,
            search_indiamart,
            search_online_prices,
            filter_relevant_vendors,
            select_vendors_for_calling,
        )

        db_vendor_infos: list[VendorInfo] = []
        db_results: list[UnifiedResult] = []
        gold_live_snapshot_results: list[UnifiedResult] = []
        brand_miss = False
        query = brand_search.ensure_brand_on_query(query)
        snapshot.query = query
        wants_brand = brand_search.wants_specific_brand(query)
        if search_strategy in {"both", "offline"}:
            if settings.gold_use_unified_taxonomy_flow and is_gold_query(query):
                from app.categories.gold.canonical_live_sync import sync_gold_live_rates_to_canonical
                from app.categories.gold.live_rates import refresh_website_live_rates_for_query

                async def _publish_refreshed_gold_vendor(source_vendor_id: str) -> None:
                    try:
                        await sync_gold_live_rates_to_canonical([source_vendor_id])
                        live_route = await find_vendor_offerings(query, limit=DB_FIRST_ROUTE_LIMIT)
                        live_results = [
                            result
                            for result in live_route.to_offering_results(limit=DB_FIRST_SUPPLIER_RESULT_LIMIT)
                            if (result.attributes or {}).get("legacy_vendor_id") == source_vendor_id
                        ]
                        if live_results:
                            _update_partial_results(snapshot, live_results, query.intent, category=getattr(query, "category", None))
                            offline_results.extend(live_results)
                            logger.info(
                                "Published fresh Gold live result for vendor=%s result_count=%s search_id=%s",
                                source_vendor_id,
                                len(live_results),
                                snapshot.search_id,
                            )
                    except Exception as exc:  # pragma: no cover - depends on DB/network state
                        logger.warning("Could not publish refreshed Gold vendor=%s: %s", source_vendor_id, exc)

                refresh_summary = await refresh_website_live_rates_for_query(
                    query,
                    force=True,
                    on_vendor_refreshed=_publish_refreshed_gold_vendor,
                )
                sync_summary = await sync_gold_live_rates_to_canonical(refresh_summary.refreshed_vendor_ids)
                logger.info(
                    "Unified Gold live refresh for %s: attempted=%s refreshed=%s skipped_fresh=%s failed=%s canonical_snapshots=%s",
                    query.product,
                    refresh_summary.attempted,
                    refresh_summary.refreshed,
                    refresh_summary.skipped_fresh,
                    refresh_summary.failed,
                    sync_summary.get("current_snapshots", 0),
                )
                gold_live_snapshot_results = await find_gold_live_rate_results(
                    query,
                    limit=DB_FIRST_SUPPLIER_RESULT_LIMIT,
                )
            route = await find_vendor_offerings(query, limit=DB_FIRST_ROUTE_LIMIT)
            if route.intent.missing_attributes:
                logger.info(
                    "Product-vendor routing needs more attributes for %s: %s",
                    query.product,
                    route.intent.missing_attributes,
                )
            elif route.candidates or route.capability_candidates:
                db_results = _dedupe_results(
                    [
                        *gold_live_snapshot_results,
                        *route.to_offering_results(limit=DB_FIRST_SUPPLIER_RESULT_LIMIT),
                    ]
                )
                if not db_results and settings.gold_use_unified_taxonomy_flow and is_gold_query(query):
                    board_query = query.copy(
                        update={
                            "product": "gold live rates",
                            "raw_query": f"gold live rates {query.location}",
                            "quantity": None,
                        }
                    )
                    board_route = await find_vendor_offerings(board_query, limit=DB_FIRST_ROUTE_LIMIT)
                    db_results = board_route.to_offering_results(limit=DB_FIRST_SUPPLIER_RESULT_LIMIT)
                    if db_results:
                        logger.info(
                            "Unified Gold exact match was empty; showing %s latest stored live board result(s)",
                            len(db_results),
                        )
                db_vendor_infos = route.to_vendors(limit=DB_FIRST_SUPPLIER_RESULT_LIMIT)
            elif gold_live_snapshot_results:
                db_results = gold_live_snapshot_results
                db_vendor_infos = []
            # Non-gold: show same-city DB first; hold other-city behind consent.
            # Gold keeps the existing unified path (no city-split / IM threshold).
            if db_results and not (settings.gold_use_unified_taxonomy_flow and is_gold_query(query)):
                same_city_db, other_city_db = location_ranker.split_results_by_city_market(query, db_results)
                held_same_city: list[UnifiedResult] = []
                held_other_city: list[UnifiedResult] = []
                if wants_brand:
                    brand = query.brand or ""
                    same_brand = [r for r in same_city_db if brand_search.result_matches_brand(r, brand)]
                    other_brand = [r for r in other_city_db if brand_search.result_matches_brand(r, brand)]
                    if same_brand:
                        logger.info(
                            "DB brand hits for %s brand=%s: same_city_brand=%s other_city_brand=%s",
                            query.product,
                            brand,
                            len(same_brand),
                            len(other_brand),
                        )
                        same_city_db = same_brand
                        other_city_db = other_brand
                    else:
                        # No same-city brand SKUs — chase brand online first; hold generics.
                        brand_miss = True
                        held_same_city = list(same_city_db)
                        held_other_city = list(other_city_db)
                        same_city_db = []
                        other_city_db = list(other_brand)  # rare: brand only in other cities
                        logger.info(
                            "DB brand miss for %s brand=%s — holding generic same_city=%s other_city=%s",
                            query.product,
                            brand,
                            len(held_same_city),
                            len(held_other_city),
                        )
                        snapshot.pending_brand_fallback_same_city = held_same_city
                        snapshot.pending_brand_fallback_other_city = held_other_city
                if brand_miss:
                    # Do not ask other-city yet; brand-fallback consent comes after online.
                    snapshot.pending_other_city_results = []
                    snapshot.other_city_prompt = OtherCityPrompt(status="none")
                else:
                    _arm_other_city_prompt(snapshot, query, other_city_db)
                    await _persist_live_snapshot(snapshot)
                db_results = same_city_db
                db_vendor_infos = _filter_vendors_same_city(query, db_vendor_infos) if db_results else []
                logger.info(
                    "DB city split for %s: same_city=%s other_city_pending=%s held_generic=%s+%s "
                    "(IM if same_city<%s) brand_miss=%s",
                    query.product,
                    len(same_city_db),
                    len(other_city_db),
                    len(held_same_city),
                    len(held_other_city),
                    SAME_CITY_DB_IM_THRESHOLD,
                    brand_miss,
                )
            elif wants_brand and db_results:
                brand = query.brand or ""
                branded = [r for r in db_results if brand_search.result_matches_brand(r, brand)]
                if branded:
                    db_results = branded
                else:
                    brand_miss = True
                    snapshot.pending_brand_fallback_same_city = list(db_results)
                    snapshot.pending_brand_fallback_other_city = []
                    db_results = []
                    db_vendor_infos = []

            if brand_miss and wants_brand:
                miss_detail = brand_search.brand_miss_message(query)
                if not any(step.id == "brand-match" for step in snapshot.steps):
                    snapshot.steps.append(
                        SearchProgressStep(
                            id="brand-match",
                            label="Brand availability",
                            status="completed",
                            detail=miss_detail,
                        )
                    )
                else:
                    _set_step(snapshot, "brand-match", "completed", miss_detail)
                _touch(snapshot)

            if db_results:
                ranked_db_results = _rank_results_for_query(
                    query,
                    _dedupe_results([*snapshot.partial_results, *db_results]),
                    query.intent,
                    category=getattr(query, "category", None),
                )
                _set_visible_results(snapshot, ranked_db_results, visible_count=RESULT_PAGE_SIZE)
                _touch(snapshot)
                offline_results.extend(db_results)
            elif wants_brand and brand_miss:
                # Brand miss with generics held — announce before online; no DB cards yet.
                if not any(step.id == "brand-match" for step in snapshot.steps):
                    snapshot.steps.append(
                        SearchProgressStep(
                            id="brand-match",
                            label="Brand availability",
                            status="completed",
                            detail=brand_search.brand_miss_message(query),
                        )
                    )
                    _touch(snapshot)
            if db_vendor_infos and not defer_vendor_discovery and not brand_miss:
                snapshot.discovered_vendors = list(
                    {vendor.vendor_id or vendor.phone or vendor.name: vendor for vendor in [*snapshot.discovered_vendors, *db_vendor_infos]}.values()
                )
                _replace_vendor_steps_after_discovery(snapshot, query, search_strategy, snapshot.discovered_vendors, platforms)
                _touch(snapshot)

        skip_live_discovery = False
        if settings.gold_use_unified_taxonomy_flow and is_gold_query(query):
            skip_live_discovery = bool(db_results and _has_sufficient_db_coverage(db_results, db_vendor_infos))
        else:
            # Same-city DB >= 10 → skip IndiaMART; still may offer other-city consent.
            # Brand miss always continues to online + IndiaMART to chase the brand.
            skip_live_discovery = (
                len(db_results) >= SAME_CITY_DB_IM_THRESHOLD and not brand_miss
            )

        if db_results and skip_live_discovery:
            logger.info(
                "DB-first local coverage satisfied for %s: same_city_db=%s callable_vendors=%s",
                query.product,
                len(db_results),
                len(db_vendor_infos),
            )
            if db_vendor_infos:
                snapshot.discovered_vendors = list(
                    {vendor.vendor_id or vendor.phone or vendor.name: vendor for vendor in db_vendor_infos}.values()
                )
                _replace_vendor_steps_after_discovery(snapshot, query, search_strategy, snapshot.discovered_vendors, platforms)
                _set_step(
                    snapshot,
                    "vendor-discovery",
                    "completed",
                    f"Found {len(db_results)} matching supplier option{'s' if len(db_results) != 1 else ''} nearby; {len(db_vendor_infos)} with callable numbers.",
                )

            should_call_vendors = search_strategy in {"both", "offline"} and not (
                settings.gold_use_unified_taxonomy_flow and is_gold_query(query)
            )
            if should_call_vendors and db_vendor_infos:
                vendors_to_contact = db_vendor_infos[:1] if settings.test_call_phone.strip() else db_vendor_infos
                vendors_to_contact = _append_demo_vendor_for_calling(snapshot, query, vendors_to_contact)
                concurrency_limit = current_voice_concurrency_limit()
                spacing_seconds = current_voice_call_spacing_seconds()
                semaphore = asyncio.Semaphore(concurrency_limit)
                vendor_tasks = [
                    asyncio.create_task(
                        _run_vendor_with_rate_limit(
                            semaphore,
                            _resolve_vendor(snapshot, query, vendor),
                            spacing_seconds,
                        )
                    )
                    for vendor in vendors_to_contact
                ]
                vendor_completed = await asyncio.gather(*vendor_tasks, return_exceptions=True)
                for item in vendor_completed:
                    if isinstance(item, Exception):
                        logger.warning("DB vendor call task failed: %s", item)
                        continue
                    offline_results.extend(item)

            ranked = _rank_results_for_query(
                query,
                _dedupe_results(offline_results),
                query.intent,
                category=getattr(query, "category", None),
            )
            snapshot.final_results = SearchResponse(
                query=query,
                results=ranked,
                online_count=0,
                offline_count=len(ranked),
                total_time_seconds=round(time.time() - started, 2),
                search_strategy=search_strategy,
            )
            _set_visible_results(snapshot, ranked, visible_count=RESULT_PAGE_SIZE)
            snapshot.status = "completed"
            _touch(snapshot)
            await _persist_safely(
                persistence.complete_search_session(
                    search_id=snapshot.search_id,
                    query=query,
                    final_results=ranked,
                    status="completed",
                    total_time_seconds=round(time.time() - started, 2),
                ),
                f"search completion {snapshot.search_id}",
            )
            await _persist_live_snapshot(snapshot)
            await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
            await _notify_whatsapp_other_city_prompt(snapshot, request_metadata)
            return

        # ─── Step 1: Generate LLM search queries ───
        online_detail = None
        if brand_miss and wants_brand:
            online_detail = (
                f"No matches found locally for {query.brand}. "
                f"I’ll check online stores next."
            )
        if search_strategy in {"both", "online"}:
            _ensure_online_steps(snapshot, detail=online_detail)

        search_product = brand_search.product_without_brand(query.product, query.brand) or query.product
        collected = {
            "spec": " ".join(query.search_specs or []) or search_product,
            "location": query.location if query.location != "unknown" else "",
            "use_case": query.use_case or "",
            "brand": (query.brand or "") if wants_brand else "",
            "search_specs": ", ".join(query.search_specs or []),
        }
        queries_dict = await generate_search_queries(search_product, collected)
        google_queries = queries_dict.get("google_places_queries", [])
        indiamart_keywords = list(queries_dict.get("indiamart_keywords") or [])
        if wants_brand:
            for phrase in reversed(brand_search.brand_keyword_phrases(query)):
                if phrase and phrase.lower() not in {k.lower() for k in indiamart_keywords}:
                    indiamart_keywords.insert(0, phrase)
            # Prefer branded online shopping query when chasing a brand.
            collected["spec"] = f"{query.brand} {search_product}".strip()

        # ─── Step 2: Run tracks — on brand miss, online first, then IndiaMART ───
        location_str = query.location if query.location != "unknown" else ""

        # Launch places early; online/IM sequencing depends on brand_miss.
        places_task = asyncio.create_task(
            search_google_places(google_queries, location_str)
        )

        def _indiamart_vendor_to_unified(r: dict) -> UnifiedResult | None:
            import re as _re2

            if not (r.get("phone") and (r.get("price") or r.get("product_name") or r.get("company_name"))):
                return None
            price_val = None
            if r.get("price"):
                price_str = str(r["price"]).replace("\u20b9", "").replace(",", "").strip()
                nums = _re2.findall(r"[\d]+\.?\d*", price_str)
                price_val = float(nums[0]) if nums else None
            specs = []
            unit = None
            raw_specs = r.get("specs") if isinstance(r.get("specs"), dict) else {}
            for label, value in raw_specs.items():
                normalized_label = _re2.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()
                compact_label = normalized_label.replace(" ", "")
                if compact_label in {"unit", "units", "priceunit", "packagingunit"}:
                    if value and not unit:
                        unit = str(value).strip()
                    continue
                if (
                    "indiamart" in compact_label
                    or compact_label in {"id", "mcat", "catid", "mcatid", "source", "sourceurl", "url", "link"}
                    or (
                        compact_label.endswith("id")
                        and any(token in compact_label for token in ("cat", "mcat", "listing", "product", "supplier", "vendor"))
                    )
                ):
                    continue
                if label and value:
                    specs.append({"label": str(label), "value": str(value)})
            supplier_city = r.get("city") or ""
            supplier_state = r.get("state") or ""
            supplier_location = ", ".join(part for part in [supplier_city, supplier_state] if part)
            product_name = r.get("product_name") or r.get("company_name", "")
            company_name = r.get("company_name") or ""
            moq = r.get("moq")
            return UnifiedResult(
                source_type="offline",
                name=product_name,
                price=price_val,
                url=r.get("product_url"),
                phone=r.get("phone"),
                address=r.get("address", ""),
                notes=" | ".join(
                    part
                    for part in [
                        company_name,
                        f"MOQ: {moq}" if moq else "",
                    ]
                    if part
                ),
                confidence=0.7,
                vendor_id=r.get("vendor_key"),
                city=supplier_city or None,
                attributes={
                    "source": r.get("source"),
                    "source_url": r.get("product_url"),
                    "product_preview": {
                        "title": product_name,
                        "image_url": r.get("image_url"),
                        "price": price_val,
                        "currency": "INR",
                        "unit": unit,
                        "available": "In Stock",
                        "description": r.get("description") or "",
                        "specs": specs,
                        "supplier_name": company_name,
                        "supplier_phone": r.get("phone"),
                        "supplier_address": r.get("address", ""),
                        "supplier_city": supplier_city,
                        "supplier_state": supplier_state,
                        "supplier_location": supplier_location,
                        "supplier_rating": r.get("rating"),
                        "supplier_rating_count": r.get("rating_count"),
                        "moq": moq,
                        "source_url": r.get("product_url"),
                    },
                },
            )

        async def _on_indiamart_vendor(vendor: dict) -> None:
            # Append only after phone enrichment — no phoneless preview card.
            unified = _indiamart_vendor_to_unified(vendor)
            if not unified:
                return
            _update_partial_results(
                snapshot,
                [unified],
                query.intent,
                category=getattr(query, "category", None),
                grow_visible=True,
            )
            offline_results.append(unified)

        online_product = (
            f"{query.brand} {search_product}".strip()
            if wants_brand and query.brand
            else search_product
        )
        online_task = asyncio.create_task(search_online_prices(online_product, collected))
        flash_task = None
        if search_strategy in {"both", "online"} and settings.flash_compare_enabled:
            flash_task = asyncio.create_task(_resolve_flash_compare(snapshot, query))

        # Helper to convert and push online results as soon as ready
        async def _await_online() -> list[UnifiedResult]:
            import re as _re
            results: list[UnifiedResult] = []
            try:
                serpapi_results = await online_task
                for r in serpapi_results:
                    price_val = r.get("price")
                    if isinstance(price_val, str):
                        nums = _re.findall(r"[\d,]+\.?\d*", price_val.replace(",", ""))
                        price_val = float(nums[0]) if nums else None
                    results.append(UnifiedResult(
                        source_type="online",
                        name=r.get("product_name", ""),
                        price=price_val if isinstance(price_val, (int, float)) else None,
                        url=r.get("product_url"),
                        delivery_time=r.get("delivery"),
                        notes=f"Platform: {r.get('platform', 'online')}",
                        confidence=0.8,
                    ))
            except Exception as exc:
                logger.warning("SerpAPI online track failed: %s", exc)
            if results:
                _update_partial_results(snapshot, results, query.intent, category=getattr(query, 'category', None))
                online_results.extend(results)
            return results

        # Helper to push Google Places results (no UnifiedResult yet, used for calls)
        async def _await_places() -> list[dict]:
            try:
                return await places_task
            except Exception as exc:
                logger.warning("Google Places track failed: %s", exc)
                return []

        # Helper to push flash compare results
        async def _await_flash() -> list[UnifiedResult]:
            if not flash_task:
                return []
            try:
                results = await flash_task
                if results:
                    _update_partial_results(snapshot, results, query.intent, category=getattr(query, 'category', None))
                    online_results.extend(results)
                return results
            except Exception as exc:
                logger.warning("Flash compare failed: %s", exc)
                return []

        indiamart_results: list[dict] = []
        if brand_miss and wants_brand:
            # Brand miss: surface online/retail first, then chase brand on IndiaMART.
            await asyncio.gather(_await_online(), _await_flash())
            indiamart_task = asyncio.create_task(
                search_indiamart(
                    indiamart_keywords,
                    location_str,
                    on_vendor=_on_indiamart_vendor,
                )
            )

            async def _await_indiamart() -> list[dict]:
                try:
                    return await indiamart_task
                except Exception as exc:
                    logger.warning("IndiaMart track failed: %s", exc)
                    return []

            indiamart_results, places_results = await asyncio.gather(
                _await_indiamart(),
                _await_places(),
            )
        else:
            indiamart_task = asyncio.create_task(
                search_indiamart(
                    indiamart_keywords,
                    location_str,
                    on_vendor=_on_indiamart_vendor,
                )
            )

            async def _await_indiamart() -> list[dict]:
                try:
                    return await indiamart_task
                except Exception as exc:
                    logger.warning("IndiaMart track failed: %s", exc)
                    return []

            # Run all awaits concurrently — each pushes partial_results as it finishes
            _, indiamart_results, places_results, _ = await asyncio.gather(
                _await_online(),
                _await_indiamart(),
                _await_places(),
                _await_flash(),
            )
        # ─── Step 3: Filter offline vendors, then select call targets via Thompson sampling ───
        combined_vendors = places_results + indiamart_results
        if combined_vendors:
            filtered = await filter_relevant_vendors(
                combined_vendors, query.product, query.product
            )
            top_vendors, reserve_vendors = await select_vendors_for_calling(
                filtered,
                category=getattr(query, "category", None),
                limit=10,
            )
        else:
            top_vendors, reserve_vendors = [], []

        # Convert top vendors to VendorInfo for voice calling
        vendor_infos: list[VendorInfo] = []
        for v in top_vendors:
            if v.get("phone"):
                vendor_infos.append(VendorInfo(
                    vendor_id=v.get("vendor_key"),
                    place_id=v.get("place_id"),
                    name=v["company_name"],
                    phone=v["phone"],
                    address=v.get("address", ""),
                    rating=v.get("rating"),
                    user_rating_count=v.get("rating_count"),
                    is_mock=False,
                ))

        if db_vendor_infos:
            merged_vendor_map = {
                vendor.vendor_id or vendor.phone or vendor.name: vendor
                for vendor in [*db_vendor_infos, *vendor_infos]
            }
            vendor_infos = list(merged_vendor_map.values())
        # Update snapshot with discovered vendors
        snapshot.discovered_vendors = vendor_infos
        _replace_vendor_steps_after_discovery(snapshot, query, search_strategy, vendor_infos, platforms)
        _touch(snapshot)

        # ─── Step 4: Call vendors (existing voice pipeline) ───
        # On brand miss we are still chasing brand online; don't cold-call generic DB vendors.
        should_call_vendors = (
            search_strategy in {"both", "offline"}
            and not (settings.gold_use_unified_taxonomy_flow and is_gold_query(query))
            and not brand_miss
        )
        if should_call_vendors and vendor_infos:
            vendors_to_contact = vendor_infos[:1] if settings.test_call_phone.strip() else vendor_infos
            vendors_to_contact = _append_demo_vendor_for_calling(snapshot, query, vendors_to_contact)
            concurrency_limit = current_voice_concurrency_limit()
            spacing_seconds = current_voice_call_spacing_seconds()
            semaphore = asyncio.Semaphore(concurrency_limit)

            vendor_tasks = [
                asyncio.create_task(
                    _run_vendor_with_rate_limit(
                        semaphore,
                        _resolve_vendor(snapshot, query, vendor),
                        spacing_seconds,
                    )
                )
                for vendor in vendors_to_contact
            ]
            vendor_completed = await asyncio.gather(*vendor_tasks, return_exceptions=True)
            for item in vendor_completed:
                if isinstance(item, Exception):
                    logger.warning("Vendor call task failed: %s", item)
                    continue
                offline_results.extend(item)

        # ─── Finalize ───
        all_results = online_results + offline_results
        ranked = _rank_results_for_query(
            query,
            _dedupe_results(all_results),
            query.intent,
            category=getattr(query, "category", None),
        )

        snapshot.final_results = SearchResponse(
            query=query,
            results=ranked,
            online_count=len(online_results),
            offline_count=len(offline_results),
            total_time_seconds=round(time.time() - started, 2),
            search_strategy=search_strategy,
        )
        # Page at RESULT_PAGE_SIZE so web/WhatsApp can ask "more quotes?" instead
        # of dumping the full ranked list at completion.
        _set_visible_results(snapshot, ranked, visible_count=RESULT_PAGE_SIZE)
        if brand_miss and wants_brand:
            _arm_brand_fallback_prompt(
                snapshot,
                query,
                same_city=snapshot.pending_brand_fallback_same_city,
                other_city=snapshot.pending_brand_fallback_other_city,
            )
        snapshot.status = "completed"
        _touch(snapshot)
        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=query,
                final_results=ranked,
                status="completed",
                total_time_seconds=round(time.time() - started, 2),
            ),
            f"search completion {snapshot.search_id}",
        )
        await _persist_live_snapshot(snapshot)
        await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
        if brand_miss and wants_brand:
            await _notify_whatsapp_brand_fallback_prompt(snapshot, request_metadata)
        else:
            await _notify_whatsapp_other_city_prompt(snapshot, request_metadata)
    except Exception as exc:  # pragma: no cover - background orchestration
        logger.exception("Background search failed for %s", query.product)
        snapshot.status = "failed"
        snapshot.error = str(exc)
        _touch(snapshot)
        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=query,
                final_results=snapshot.partial_results,
                status="failed",
                error=str(exc),
                total_time_seconds=round(time.time() - started, 2),
            ),
            f"search failure {snapshot.search_id}",
        )
        await _persist_live_snapshot(snapshot)
        await _notify_whatsapp_completion(snapshot.search_id, request_metadata)
    finally:
        _TASKS.pop(snapshot.search_id, None)


async def start_search(
    query: StructuredQuery,
    search_strategy: SearchStrategy = "both",
    request_metadata: dict[str, str | None] | None = None,
    session_id: str | None = None,
    defer_vendor_discovery: bool = False,
    exclude_vendor_ids: set[str] | None = None,
) -> SearchProgressSnapshot:
    if not query_structurer.is_supported_category(query.category):
        raise orchestrator.UnsupportedCategoryError(query_structurer.unsupported_category_message())
    started_at = datetime.utcnow()
    route = await resolve_query_route(query)
    apply_route_to_query(query, route)

    if _use_legacy_gold_flow(query, handler_key=route.handler_key):
        selection = await select_gold_vendors(query, exclude_vendor_ids=exclude_vendor_ids or set())
        platforms: list[PlatformStrategy] = []
        should_call_gold_vendors = search_strategy in {"both", "offline"} and bool(selection.discovered_vendors)
        snapshot = SearchProgressSnapshot(
            query=query,
            status="running" if should_call_gold_vendors else "completed",
            discovered_vendors=selection.discovered_vendors,
            online_platforms=[],
            steps=_build_steps(query, search_strategy, selection.discovered_vendors, platforms),
            partial_results=selection.primary_results,
            final_results=None
            if should_call_gold_vendors
            else SearchResponse(
                    query=query,
                    results=selection.primary_results,
                    online_count=len([result for result in selection.primary_results if result.result_type == "live_rate"]),
                    offline_count=len([result for result in selection.primary_results if result.result_type != "live_rate"]),
                    total_time_seconds=round(time.time() - started_at.timestamp(), 2),
                    search_strategy=search_strategy,
                ),
            started_at=started_at,
            updated_at=datetime.utcnow(),
        )
        for step in snapshot.steps:
            if step.id == "vendor-discovery":
                step.status = "completed"
                step.detail = (
                    f"Found {len(selection.primary_results)} local gold vendors ranked live-first for {query.location}."
                )
            elif step.status == "pending" and not should_call_gold_vendors:
                step.status = "completed"
        await _persist_safely(
            persistence.initialize_search_session(
                search_id=snapshot.search_id,
                query=query,
                search_strategy=search_strategy,
                request_metadata=request_metadata,
                session_id=session_id,
                source_flow="chat",
                discovered_vendors=selection.discovered_vendors,
                online_platforms=platforms,
            ),
            f"search init {snapshot.search_id}",
        )
        _SEARCHES[snapshot.search_id] = snapshot
        if should_call_gold_vendors:
            _TASKS[snapshot.search_id] = asyncio.create_task(
                _run_gold_selection_call_search(
                    snapshot,
                    query,
                    search_strategy,
                    request_metadata=request_metadata,
                )
            )
            return snapshot

        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=query,
                final_results=selection.primary_results,
                status="completed",
                total_time_seconds=snapshot.final_results.total_time_seconds if snapshot.final_results else 0,
            ),
            f"search completion {snapshot.search_id}",
        )
        return snapshot

    if search_strategy in {"both", "offline"} and not defer_vendor_discovery:
        from app.services.product_vendor_routing import find_vendor_offerings

        vendor_route = await find_vendor_offerings(query)
        vendors = vendor_route.to_vendors()
        if not vendors and not (settings.gold_use_unified_taxonomy_flow and is_gold_query(query)):
            vendors = await discover_vendors(
                query.product,
                query.category,
                query.location if query.location != "unknown" else "Rajkot",
            )
    else:
        vendors = []
    platforms: list[PlatformStrategy] = []

    snapshot = SearchProgressSnapshot(
        query=query,
        status="running",
        discovered_vendors=vendors,
        online_platforms=[
            *(["Online marketplaces"] if search_strategy in {"both", "online"} and settings.flash_compare_enabled else []),
        ],
        steps=_build_steps(
            query,
            search_strategy,
            vendors,
            platforms,
            vendor_discovery_pending=defer_vendor_discovery and search_strategy in {"both", "offline"},
        ),
        started_at=started_at,
        updated_at=started_at,
    )
    await _persist_safely(
        persistence.initialize_search_session(
            search_id=snapshot.search_id,
            query=query,
            search_strategy=search_strategy,
            request_metadata=request_metadata,
            session_id=session_id,
            source_flow="chat",
            discovered_vendors=vendors,
            online_platforms=platforms,
        ),
        f"search init {snapshot.search_id}",
    )
    _SEARCHES[snapshot.search_id] = snapshot
    _TASKS[snapshot.search_id] = asyncio.create_task(
        _run_background_search(
            snapshot,
            query,
            search_strategy,
            vendors,
            platforms,
            defer_vendor_discovery=defer_vendor_discovery,
            request_metadata=request_metadata,
            session_id=session_id,
        )
    )
    return snapshot


def get_snapshot(search_id: str) -> SearchProgressSnapshot | None:
    """In-memory only. Prefer resolve_snapshot() for request handlers."""
    return _SEARCHES.get(search_id)


async def respond_other_city_expand(search_id: str, *, accept: bool) -> SearchProgressSnapshot | None:
    """Unlock or decline held other-city DB offerings after user consent."""
    snapshot = await resolve_snapshot(search_id)
    if snapshot is None:
        return None
    prompt = snapshot.other_city_prompt
    if not prompt or prompt.status != "awaiting":
        return snapshot

    if not accept:
        snapshot.other_city_prompt = OtherCityPrompt(
            status="declined",
            message=prompt.message,
            cities=list(prompt.cities),
            other_city_count=prompt.other_city_count,
        )
        snapshot.pending_other_city_results = []
        _touch(snapshot)
        await _persist_live_snapshot(snapshot)
        return snapshot

    pending = list(snapshot.pending_other_city_results)
    accepted_prompt = OtherCityPrompt(
        status="accepted",
        message=prompt.message,
        cities=list(prompt.cities),
        other_city_count=prompt.other_city_count,
    )
    if pending:
        # Keep already-shown (usually same-city) order; append other-city after,
        # then reveal only the next RESULT_PAGE_SIZE quotes — not the full dump.
        already_shown = list(snapshot.partial_results)
        shown_ids = {result.id for result in already_shown}
        other_ranked = _rank_results_for_query(
            snapshot.query,
            pending,
            snapshot.query.intent,
            category=getattr(snapshot.query, "category", None),
        )
        base_ranked = (
            list(snapshot.final_results.results)
            if snapshot.final_results
            else list(already_shown)
        )
        rest_base = [result for result in base_ranked if result.id not in shown_ids]
        other_new = [
            result
            for result in other_ranked
            if result.id not in shown_ids and result.id not in {r.id for r in rest_base}
        ]
        full_ranked = _dedupe_results([*already_shown, *rest_base, *other_new])
        reveal_count = min(len(already_shown) + RESULT_PAGE_SIZE, len(full_ranked))

        prior = snapshot.final_results
        # Build response before mutating pending — total_time may be unset while
        # search is still running (other-city prompt can appear mid-flight).
        prior_time = prior.total_time_seconds if prior else None
        final_results = SearchResponse(
            query=prior.query if prior else snapshot.query,
            results=full_ranked,
            online_count=(
                prior.online_count
                if prior
                else len([r for r in full_ranked if r.source_type == "online"])
            ),
            offline_count=len([r for r in full_ranked if r.source_type == "offline"]),
            total_time_seconds=float(prior_time) if prior_time is not None else 0.0,
            search_strategy=prior.search_strategy if prior else "both",
        )
        snapshot.pending_other_city_results = []
        snapshot.other_city_prompt = accepted_prompt
        snapshot.final_results = final_results
        _set_visible_results(snapshot, full_ranked, visible_count=reveal_count)
        await _persist_safely(
            persistence.complete_search_session(
                search_id=snapshot.search_id,
                query=snapshot.query,
                final_results=snapshot.final_results.results,
                status=snapshot.status if snapshot.status in {"completed", "failed"} else "completed",
                total_time_seconds=snapshot.final_results.total_time_seconds,
            ),
            f"other-city expand {snapshot.search_id}",
        )
    else:
        snapshot.pending_other_city_results = []
        snapshot.other_city_prompt = accepted_prompt
    _touch(snapshot)
    await _persist_live_snapshot(snapshot)
    return snapshot


async def respond_brand_fallback_expand(search_id: str, *, accept: bool) -> SearchProgressSnapshot | None:
    """After brand miss + online chase: unlock or decline other-brand DB suppliers."""
    snapshot = await resolve_snapshot(search_id)
    if snapshot is None:
        return None
    prompt = snapshot.brand_fallback_prompt
    if not prompt or prompt.status != "awaiting":
        return snapshot

    if not accept:
        snapshot.brand_fallback_prompt = BrandFallbackPrompt(
            status="declined",
            message=prompt.message,
            brand=prompt.brand,
            same_city_count=prompt.same_city_count,
            other_city_count=prompt.other_city_count,
        )
        snapshot.pending_brand_fallback_same_city = []
        snapshot.pending_brand_fallback_other_city = []
        _touch(snapshot)
        await _persist_live_snapshot(snapshot)
        return snapshot

    same_city = list(snapshot.pending_brand_fallback_same_city)
    other_city = list(snapshot.pending_brand_fallback_other_city)
    accepted_prompt = BrandFallbackPrompt(
        status="accepted",
        message=prompt.message,
        brand=prompt.brand,
        same_city_count=prompt.same_city_count,
        other_city_count=prompt.other_city_count,
    )

    already_shown = list(snapshot.partial_results)
    shown_ids = {result.id for result in already_shown}
    same_ranked = _rank_results_for_query(
        snapshot.query,
        same_city,
        snapshot.query.intent,
        category=getattr(snapshot.query, "category", None),
    )
    same_new = [result for result in same_ranked if result.id not in shown_ids]
    base_ranked = (
        list(snapshot.final_results.results)
        if snapshot.final_results
        else list(already_shown)
    )
    rest_base = [result for result in base_ranked if result.id not in shown_ids]
    full_ranked = _dedupe_results([*already_shown, *rest_base, *same_new])
    # Reveal next page of same-city alternatives; hold other-city behind consent.
    reveal_count = min(len(already_shown) + RESULT_PAGE_SIZE, len(full_ranked))
    # Prefer revealing newly unlocked same-city rows even if already_shown was online-only.
    if same_new:
        reveal_count = min(len(already_shown) + min(RESULT_PAGE_SIZE, len(same_new)), len(full_ranked))

    prior = snapshot.final_results
    prior_time = prior.total_time_seconds if prior else None
    final_results = SearchResponse(
        query=prior.query if prior else snapshot.query,
        results=full_ranked,
        online_count=(
            prior.online_count
            if prior
            else len([r for r in full_ranked if r.source_type == "online"])
        ),
        offline_count=len([r for r in full_ranked if r.source_type == "offline"]),
        total_time_seconds=float(prior_time) if prior_time is not None else 0.0,
        search_strategy=prior.search_strategy if prior else "both",
    )
    snapshot.pending_brand_fallback_same_city = []
    snapshot.pending_brand_fallback_other_city = []
    snapshot.brand_fallback_prompt = accepted_prompt
    snapshot.final_results = final_results
    _set_visible_results(snapshot, full_ranked, visible_count=reveal_count)
    if other_city:
        _arm_other_city_prompt(snapshot, snapshot.query, other_city)
    else:
        snapshot.other_city_prompt = OtherCityPrompt(status="none")
        snapshot.pending_other_city_results = []

    await _persist_safely(
        persistence.complete_search_session(
            search_id=snapshot.search_id,
            query=snapshot.query,
            final_results=snapshot.final_results.results,
            status=snapshot.status if snapshot.status in {"completed", "failed"} else "completed",
            total_time_seconds=snapshot.final_results.total_time_seconds,
        ),
        f"brand-fallback expand {snapshot.search_id}",
    )
    _touch(snapshot)
    await _persist_live_snapshot(snapshot)
    return snapshot


async def get_more_results(
    search_id: str,
    *,
    offset: int | None = None,
    limit: int = RESULT_PAGE_SIZE,
) -> SearchMoreResultsResponse | None:
    limit = max(1, min(limit, 25))
    snapshot = await resolve_snapshot(search_id)
    ranked_results: list[UnifiedResult] = []

    if snapshot and snapshot.final_results:
        ranked_results = snapshot.final_results.results
        start = snapshot.next_result_offset if offset is None else max(0, offset)
    else:
        search_doc = await persistence.get_search_session(search_id)
        if not search_doc:
            return None
        raw_results = search_doc.get("final_results") or search_doc.get("top_results") or []
        for raw_result in raw_results:
            try:
                ranked_results.append(UnifiedResult.model_validate(raw_result))
            except Exception:
                logger.debug("Skipping malformed persisted search result search=%s", search_id, exc_info=True)
        delivery = search_doc.get("whatsapp_delivery") or {}
        start = int(delivery.get("next_result_offset") or RESULT_PAGE_SIZE) if offset is None else max(0, offset)

    end = min(start + limit, len(ranked_results))
    results = ranked_results[start:end]
    has_more = end < len(ranked_results)
    if snapshot:
        snapshot.next_result_offset = end
        snapshot.visible_result_count = max(snapshot.visible_result_count, end)
        snapshot.has_more_results = has_more
        snapshot.total_ranked_results = len(ranked_results)
        snapshot.partial_results = ranked_results[:end]
        _touch(snapshot)
        await _persist_live_snapshot(snapshot)
    return SearchMoreResultsResponse(
        search_id=search_id,
        results=results,
        next_offset=end,
        has_more=has_more,
        total_results=len(ranked_results),
    )
