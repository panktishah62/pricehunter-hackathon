from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from app.database import (
    call_attempts_collection,
    chat_messages_collection,
    chat_sessions_collection,
    online_results_collection,
    price_history_collection,
    product_catalog_collection,
    raw_webhooks_collection,
    search_sessions_collection,
    vendor_product_observations_collection,
    vendor_profiles_collection,
    whatsapp_messages_collection,
    whatsapp_sessions_collection,
)
from app.models.schemas import (
    ChatHistoryMessage,
    ConversationState,
    SearchProgressSnapshot,
    SearchResponse,
    StructuredQuery,
    UnifiedResult,
    VendorInfo,
    VoiceCallResult,
)
from app.services.online_discovery import PlatformStrategy
from app.services.price_parsing import parse_price
from app.services.recording_archive import archive_call_recording

logger = logging.getLogger(__name__)

OBSERVATION_TTL_DAYS = 7


def _normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "unknown"


def _extract_pincode(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"\b\d{6}\b", value)
    return match.group(0) if match else None


def _location_doc(value: str | None) -> dict[str, Any]:
    raw = (value or "unknown").strip() or "unknown"
    return {
        "raw": raw,
        "normalized": _normalize_key(raw),
        "pincode": _extract_pincode(raw),
    }


def _query_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) > 1]


def _product_key(product: str, category: str) -> str:
    return f"{category}:{_normalize_key(product)}"


def _vendor_key_from_vendor(vendor: VendorInfo, source_type: str = "offline") -> str:
    if vendor.place_id:
        return f"{source_type}:place:{vendor.place_id}"
    if vendor.phone:
        digits = "".join(char for char in vendor.phone if char.isdigit())
        if digits:
            return f"{source_type}:phone:{digits}"
    return f"{source_type}:name:{_normalize_key(vendor.name)}"


def _vendor_key_from_result(result: UnifiedResult, platform_id: str, source_type: str = "online") -> str:
    if result.url:
        parsed = urlparse(result.url)
        host = parsed.netloc or platform_id
        return f"{source_type}:url:{_normalize_key(host)}:{_normalize_key(result.name)}"
    return f"{source_type}:platform:{platform_id}:{_normalize_key(result.name)}"


def _now() -> datetime:
    return datetime.utcnow()


async def upsert_product(product: str, category: str) -> dict[str, Any]:
    key = _product_key(product, category)
    now = _now()
    doc = {
        "product_key": key,
        "canonical_name": product,
        "category": category,
        "normalized_tokens": _query_tokens(product),
        "updated_at": now,
    }
    await product_catalog_collection.update_one(
        {"product_key": key},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},
            "$addToSet": {"aliases": product},
        },
        upsert=True,
    )
    return {**doc, "aliases": [product]}


async def upsert_offline_vendor(vendor: VendorInfo, category: str | None = None) -> dict[str, Any]:
    vendor_key = _vendor_key_from_vendor(vendor, source_type="offline")
    now = _now()
    location = _location_doc(vendor.address)
    doc = {
        "vendor_key": vendor_key,
        "source_type": "offline",
        "canonical_name": vendor.name,
        "phone": vendor.phone,
        "address": vendor.address,
        "location": location,
        "rating": vendor.rating,
        "user_rating_count": vendor.user_rating_count,
        "last_seen_at": now,
        "is_mock": vendor.is_mock,
    }
    update: dict[str, Any] = {
        "$set": doc,
        "$setOnInsert": {"created_at": now},
        "$addToSet": {
            "aliases": vendor.name,
            **({"categories": category} if category else {}),
        },
    }
    if vendor.place_id:
        doc["place_id"] = vendor.place_id
    else:
        update["$unset"] = {"place_id": ""}
    await vendor_profiles_collection.update_one(
        {"vendor_key": vendor_key},
        update,
        upsert=True,
    )
    return {**doc, "aliases": [vendor.name], "categories": [category] if category else []}


async def upsert_online_vendor(result: UnifiedResult, platform_name: str, platform_id: str) -> dict[str, Any]:
    vendor_key = _vendor_key_from_result(result, platform_id, source_type="online")
    now = _now()
    parsed = urlparse(result.url or "")
    domain = parsed.netloc or None
    doc = {
        "vendor_key": vendor_key,
        "source_type": "online",
        "canonical_name": result.name or platform_name,
        "platform_name": platform_name,
        "platform_id": platform_id,
        "domain": domain,
        "location": _location_doc(result.address),
        "last_seen_at": now,
        "is_mock": result.is_mock,
    }
    await vendor_profiles_collection.update_one(
        {"vendor_key": vendor_key},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": now},
            "$addToSet": {"aliases": result.name or platform_name},
        },
        upsert=True,
    )
    return {**doc, "aliases": [result.name or platform_name]}


async def initialize_search_session(
    *,
    search_id: str,
    query: StructuredQuery,
    search_strategy: str,
    request_metadata: dict[str, Any] | None = None,
    session_id: str | None = None,
    source_flow: str = "chat",
    discovered_vendors: list[VendorInfo] | None = None,
    online_platforms: list[PlatformStrategy] | None = None,
) -> None:
    now = _now()
    product = await upsert_product(query.product, query.category)
    location = _location_doc(query.location)

    vendor_keys: list[str] = []
    if discovered_vendors:
        for vendor in discovered_vendors:
            vendor_doc = await upsert_offline_vendor(vendor, query.category)
            vendor_keys.append(vendor_doc["vendor_key"])

    await search_sessions_collection.update_one(
        {"search_id": search_id},
        {
            "$set": {
                "search_id": search_id,
                "session_id": session_id,
                "source_flow": source_flow,
                "status": "running",
                "query": query.model_dump(mode="json"),
                "product": product,
                "location": location,
                "search_strategy": search_strategy,
                "request_metadata": request_metadata or {},
                "discovered_vendor_keys": vendor_keys,
                "online_platforms": [strategy.model_dump(mode="json") for strategy in (online_platforms or [])],
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
    await ensure_whatsapp_delivery(
        search_id=search_id,
        request_metadata=request_metadata,
        session_id=session_id,
    )


async def get_search_session(search_id: str) -> dict[str, Any] | None:
    session = await search_sessions_collection.find_one({"search_id": search_id})
    if session is None:
        return None
    session.pop("_id", None)
    return session


async def save_live_search_snapshot(snapshot: SearchProgressSnapshot) -> None:
    """Persist the full in-flight search snapshot for multi-instance Cloud Run.

    Expand / more-results / status polling must work when the next request lands
    on a different process than the one that ran the search.
    """
    now = _now()
    payload = snapshot.model_dump(mode="json")
    update: dict[str, Any] = {
        "live_snapshot": payload,
        "status": snapshot.status,
        "updated_at": now,
        "query": snapshot.query.model_dump(mode="json"),
        "error": snapshot.error,
    }
    if snapshot.final_results is not None:
        update["final_results"] = [
            result.model_dump(mode="json") for result in snapshot.final_results.results
        ]
        update["top_results"] = [
            result.model_dump(mode="json") for result in snapshot.final_results.results[:5]
        ]
        if snapshot.status == "completed":
            update["completed_at"] = now
    await search_sessions_collection.update_one(
        {"search_id": snapshot.search_id},
        {
            "$set": update,
            "$setOnInsert": {
                "created_at": now,
                "search_id": snapshot.search_id,
            },
        },
        upsert=True,
    )


async def load_live_search_snapshot(search_id: str) -> SearchProgressSnapshot | None:
    """Rebuild a SearchProgressSnapshot from Mongo (live_snapshot or final_results)."""
    session = await get_search_session(search_id)
    if session is None:
        return None

    live = session.get("live_snapshot")
    if isinstance(live, dict) and live.get("query") is not None:
        try:
            return SearchProgressSnapshot.model_validate(live)
        except Exception:
            logger.warning("Invalid live_snapshot for search=%s", search_id, exc_info=True)

    # Legacy / partial docs: enough for more-results, not for other-city expand.
    query_raw = session.get("query")
    if not isinstance(query_raw, dict):
        return None
    try:
        query = StructuredQuery.model_validate(query_raw)
    except Exception:
        logger.warning("Invalid persisted query for search=%s", search_id, exc_info=True)
        return None

    results: list[UnifiedResult] = []
    for raw_result in session.get("final_results") or session.get("top_results") or []:
        try:
            results.append(UnifiedResult.model_validate(raw_result))
        except Exception:
            logger.debug("Skipping malformed persisted result search=%s", search_id, exc_info=True)

    status = session.get("status") or ("completed" if results else "running")
    if status not in {"pending", "running", "completed", "failed"}:
        status = "completed" if results else "running"

    summary = session.get("summary") or {}
    total_time = summary.get("total_time_seconds")
    final_results = None
    if results:
        final_results = SearchResponse(
            query=query,
            results=results,
            online_count=len([result for result in results if result.source_type == "online"]),
            offline_count=len([result for result in results if result.source_type == "offline"]),
            total_time_seconds=float(total_time) if total_time is not None else 0.0,
            search_strategy=session.get("search_strategy") or "both",
        )

    visible = min(5, len(results))
    return SearchProgressSnapshot(
        search_id=search_id,
        query=query,
        status=status,
        partial_results=results[:visible],
        final_results=final_results,
        visible_result_count=visible,
        next_result_offset=visible,
        total_ranked_results=len(results),
        has_more_results=len(results) > visible,
        error=session.get("error"),
    )


async def complete_search_session(
    *,
    search_id: str,
    query: StructuredQuery,
    final_results: list[UnifiedResult],
    status: str,
    error: str | None = None,
    total_time_seconds: float | None = None,
) -> None:
    now = _now()
    calls_attempted = await call_attempts_collection.count_documents({"search_id": search_id})
    calls_completed = await call_attempts_collection.count_documents(
        {"search_id": search_id, "status": {"$in": ["completed", "no_answer", "busy", "failed"]}}
    )
    online_results_count = await online_results_collection.count_documents({"search_id": search_id})
    offline_results_count = len([result for result in final_results if result.source_type == "offline"])
    best_price = min((result.price for result in final_results if result.price is not None), default=None)
    best_result = next((result for result in final_results if result.price == best_price), None) if best_price else None

    await search_sessions_collection.update_one(
        {"search_id": search_id},
        {
            "$set": {
                "status": status,
                "updated_at": now,
                "completed_at": now if status == "completed" else None,
                "error": error,
                "summary": {
                    "calls_attempted": calls_attempted,
                    "calls_completed": calls_completed,
                    "online_results_count": online_results_count,
                    "offline_results_count": offline_results_count,
                    "best_price": best_price,
                    "best_source": best_result.name if best_result else None,
                    "total_time_seconds": total_time_seconds,
                },
                "final_results": [result.model_dump(mode="json") for result in final_results],
                "top_results": [result.model_dump(mode="json") for result in final_results[:5]],
                "query": query.model_dump(mode="json"),
            }
        },
    )


async def ensure_whatsapp_delivery(
    *,
    search_id: str,
    request_metadata: dict[str, Any] | None,
    session_id: str | None,
) -> None:
    if (request_metadata or {}).get("source") != "whatsapp":
        return

    now = _now()
    wa_id = (request_metadata or {}).get("wa_id")
    phone_number = (request_metadata or {}).get("phone_number") or wa_id
    await search_sessions_collection.update_one(
        {"search_id": search_id},
        {
            "$set": {
                "whatsapp_delivery.wa_id": wa_id,
                "whatsapp_delivery.phone_number": phone_number,
                "whatsapp_delivery.session_id": session_id,
                "whatsapp_delivery.updated_at": now,
            }
        },
    )
    await search_sessions_collection.update_one(
        {"search_id": search_id, "whatsapp_delivery.status": {"$exists": False}},
        {
            "$set": {
                "whatsapp_delivery.status": "waiting",
                "whatsapp_delivery.created_at": now,
                "whatsapp_delivery.updated_at": now,
            }
        },
    )


async def update_whatsapp_delivery(
    *,
    search_id: str,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    now = _now()
    query: dict[str, Any] = {"search_id": search_id}
    if status in {"partial_sent", "timeout_sent"}:
        query["whatsapp_delivery.status"] = {"$nin": ["final_sent", "failed_sent"]}
    update: dict[str, Any] = {
        "whatsapp_delivery.status": status,
        "whatsapp_delivery.updated_at": now,
    }
    if status == "partial_sent":
        update["whatsapp_delivery.partial_sent_at"] = now
    elif status == "timeout_sent":
        update["whatsapp_delivery.timeout_sent_at"] = now
    elif status in {"final_sent", "failed_sent"}:
        update["whatsapp_delivery.final_sent_at"] = now
    elif status == "send_failed":
        update["whatsapp_delivery.last_failed_at"] = now
    if extra:
        for key, value in extra.items():
            update[f"whatsapp_delivery.{key}"] = value

    await search_sessions_collection.update_one(query, {"$set": update})


_LEAD_TIME_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(min|mins|minute|minutes|hour|hours|hr|hrs|day|days|week|weeks)",
    re.IGNORECASE,
)


def _coerce_price(value: Any) -> float | None:
    return parse_price(value)


def _stock_status_from_extraction(
    call: VoiceCallResult, extracted_result: UnifiedResult | None
) -> str:
    extracted = call.extracted_data or {}
    raw = extracted.get("product_available")
    if raw is None:
        raw = extracted.get("availability")
    if isinstance(raw, bool):
        return "in_stock" if raw else "out_of_stock"
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"in_stock", "in stock", "available", "yes", "true"}:
            return "in_stock"
        if normalized in {"out_of_stock", "out of stock", "unavailable", "no", "false"}:
            return "out_of_stock"
    if extracted_result is not None:
        return "in_stock" if extracted_result.availability else "out_of_stock"
    return "unknown"


def _lead_time_days(value: str | None) -> float | None:
    if not value:
        return None
    match = _LEAD_TIME_RE.search(str(value))
    if not match:
        return None
    qty = float(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("week"):
        return qty * 7
    if unit.startswith("day"):
        return qty
    if unit.startswith(("hour", "hr")):
        return qty / 24.0
    if unit.startswith("min"):
        return qty / (24.0 * 60.0)
    return None


def _build_call_outcome(
    *,
    call: VoiceCallResult,
    extracted_result: UnifiedResult | None,
    benchmark_price: float | None,
) -> dict[str, Any]:
    """Top-level normalized outcome block on call_attempts.

    Mirrors the field set used downstream for analytics so callers don't
    need to unpack `extracted_data` / `result_snapshot` to compute basic
    metrics like discount %, lead time, stock status.
    """
    quoted_price: float | None = None
    if extracted_result is not None and extracted_result.price is not None:
        candidate = float(extracted_result.price)
        if candidate > 0:
            quoted_price = candidate
    extracted_block = call.extracted_data or {}
    if quoted_price is None:
        # Prefer the numeric value normalized at extraction time.
        numeric = extracted_block.get("price_value")
        if isinstance(numeric, (int, float)) and numeric > 0:
            quoted_price = float(numeric)
    if quoted_price is None:
        quoted_price = _coerce_price(extracted_block.get("quoted_price"))
    if quoted_price is None:
        quoted_price = _coerce_price(extracted_block.get("price"))

    discount_amount: float | None = None
    discount_percent: float | None = None
    if quoted_price is not None and benchmark_price and benchmark_price > 0:
        raw_discount = round(benchmark_price - quoted_price, 2)
        if raw_discount > 0:
            discount_amount = raw_discount
            discount_percent = round(raw_discount / benchmark_price, 4)

    delivery_text = (
        extracted_result.delivery_time
        if extracted_result is not None
        else (call.extracted_data or {}).get("delivery_time")
    )
    lead_time = _lead_time_days(delivery_text) if delivery_text else None

    negotiated = (
        bool(extracted_result.negotiated)
        if extracted_result is not None
        else bool(
            (call.extracted_data or {}).get("negotiated")
            or (call.extracted_data or {}).get("discount_details")
        )
    )

    notes = (
        extracted_result.notes
        if extracted_result is not None
        else (call.extracted_data or {}).get("notes")
    )

    # Defer to vendor_reliability for the canonical outcome label so the
    # write here stays consistent with the Beta-update path.
    # NOTE: Skip while the call is still in flight (status == "busy") otherwise
    # the classifier maps "busy" → "no_answer" and the UI flashes a misleading
    # final outcome before the call has actually ended.
    label = None
    if (call.status or "").strip().lower() != "busy":
        try:
            from app.services.vendor_reliability import classify_call_outcome

            label = classify_call_outcome(call, quoted_price=quoted_price).outcome
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("call outcome classifier unavailable: %s", exc)

    return {
        "label": label,
        "quoted_price": quoted_price,
        "benchmark_price": benchmark_price,
        "discount_amount": discount_amount,
        "discount_percent": discount_percent,
        "stock_status": _stock_status_from_extraction(call, extracted_result),
        "negotiated": negotiated,
        "lead_time_days": lead_time,
        "moq": (call.extracted_data or {}).get("moq"),
        "notes": notes,
    }


async def record_call_attempt(
    *,
    search_id: str,
    query: StructuredQuery,
    call: VoiceCallResult,
    extracted_result: UnifiedResult | None = None,
    benchmark_price: float | None = None,
) -> None:
    now = _now()
    product = await upsert_product(query.product, query.category)
    vendor = await upsert_offline_vendor(call.vendor, query.category)
    location = _location_doc(query.location)
    recording_archive = await archive_call_recording(search_id=search_id, call=call)
    outcome_block = _build_call_outcome(
        call=call,
        extracted_result=extracted_result,
        benchmark_price=benchmark_price,
    )

    # Persist the per-query extraction schema (when present) so it is available
    # post-call (Req 5.1). It rides on the call inside
    # provider_metadata["campaign_config"]["extraction_schema"]; absent when the
    # feature flag is off, in which case we store None and behavior is unchanged.
    extraction_schema = None
    provider_metadata = call.provider_metadata if isinstance(call.provider_metadata, dict) else None
    if provider_metadata:
        campaign_config = provider_metadata.get("campaign_config")
        if isinstance(campaign_config, dict):
            extraction_schema = campaign_config.get("extraction_schema")

    update_fields: dict[str, Any] = {
        "search_id": search_id,
        "query_id": search_id,
        "category_id": query.category,
        "subcategory_id": query.subcategory_id,
        "product_name_asked": query.product,
        "call_id": call.call_id,
        "status": call.status,
        "product": product,
        "vendor": vendor,
        "vendor_id": (vendor or {}).get("vendor_key"),
        "location": location,
        "duration_seconds": call.duration_seconds,
        "transcript": call.transcript,
        "extracted_data": call.extracted_data,
        "extraction_schema": extraction_schema,
        "result_snapshot": extracted_result.model_dump(mode="json") if extracted_result else None,
        # Surface schema/model versioning as top-level fields for drift querying
        # (Req 7.1, 7.2). These ride inside result_snapshot too, but promoting
        # them to the document root keeps drift queries cheap. They are set only
        # on the schema-driven path; with the fixed extractor / flag off they are
        # None and the document is unchanged in behavior (Req 6.2/6.4).
        "schema_version": getattr(extracted_result, "schema_version", None),
        "model_version": getattr(extracted_result, "model_version", None),
        "outcome": outcome_block,
        "is_mock": call.is_mock,
        "updated_at": now,
        "completed_at": now if call.status != "busy" else None,
    }

    if call.recording_url or recording_archive.get("provider_recording_url"):
        update_fields.update(
            {
                "recording_url": call.recording_url,
                "provider_recording_url": recording_archive.get("provider_recording_url"),
                "recording_gcs_uri": recording_archive.get("recording_gcs_uri"),
                "recording_storage_provider": recording_archive.get("recording_storage_provider"),
                "recording_archive_status": recording_archive.get("recording_archive_status"),
                "recording_archive_error": recording_archive.get("recording_archive_error"),
                "recording_object_name": recording_archive.get("recording_object_name"),
                "recording_archived_at": recording_archive.get("recording_archived_at"),
                "recording_content_type": recording_archive.get("recording_content_type"),
                "recording_size_bytes": recording_archive.get("recording_size_bytes"),
            }
        )

    await call_attempts_collection.update_one(
        {"call_id": call.call_id},
        {
            "$set": update_fields,
            "$setOnInsert": {
                "started_at": now,
                "called_at": now,
            },
        },
        upsert=True,
    )

    if extracted_result:
        await _record_observation(
            search_id=search_id,
            query=query,
            product=product,
            vendor=vendor,
            result=extracted_result,
            source_channel="live_call",
        )


def _archive_auth_provider(
    *,
    provider: str,
    callback_metadata: dict[str, Any] | None,
    existing_doc: dict[str, Any] | None,
) -> str:
    """Pick the provider string whose credentials can fetch this recording.

    The recording URL is hosted by the underlying telephony rail (Plivo or
    Exotel), which is what `recording_archive._recording_headers` keys auth on.
    Voice Lab stamps calls as "pipecat" regardless of rail, so prefer the
    telephony_provider from the callback's metadata (then the stored doc's
    metadata) before falling back to the top-level provider.
    """
    for metadata in (callback_metadata, (existing_doc or {}).get("recording_provider_metadata"), (existing_doc or {}).get("provider_metadata")):
        if isinstance(metadata, dict):
            rail = str(metadata.get("telephony_provider") or "").strip().lower()
            if rail:
                return rail
    return (provider or "").strip().lower()


def _synthesize_call_for_archive(
    *,
    call_id: str,
    provider: str,
    recording_url: str,
    existing_doc: dict[str, Any] | None,
) -> VoiceCallResult:
    """Build a minimal VoiceCallResult so archive_call_recording can run."""
    vendor_doc = (existing_doc or {}).get("vendor") or {}
    vendor = VendorInfo(
        vendor_id=vendor_doc.get("vendor_key") or vendor_doc.get("vendor_id"),
        name=vendor_doc.get("canonical_name") or vendor_doc.get("name") or "vendor",
        phone=vendor_doc.get("phone") or "",
        address=vendor_doc.get("address") or "",
        place_id=vendor_doc.get("place_id"),
        bucket=vendor_doc.get("source_type") or vendor_doc.get("bucket"),
    )
    status = (existing_doc or {}).get("status") or "completed"
    if status not in {"completed", "failed", "no_answer", "busy"}:
        status = "completed"
    return VoiceCallResult(
        vendor=vendor,
        call_id=call_id,
        provider=provider or "pipecat",
        status=status,
        duration_seconds=(existing_doc or {}).get("duration_seconds") or 0,
        recording_url=recording_url,
        is_mock=False,
    )


def _apply_archive_result_to_update(update: dict[str, Any], archive: dict[str, Any]) -> None:
    """Merge an archive_call_recording result into a Mongo $set update dict."""
    if archive.get("recording_object_name"):
        update.update(
            {
                "recording_archive_status": archive.get("recording_archive_status"),
                "recording_archive_error": archive.get("recording_archive_error"),
                "recording_storage_provider": archive.get("recording_storage_provider"),
                "recording_gcs_uri": archive.get("recording_gcs_uri"),
                "recording_object_name": archive.get("recording_object_name"),
                "recording_archived_at": archive.get("recording_archived_at"),
                "recording_content_type": archive.get("recording_content_type"),
                "recording_size_bytes": archive.get("recording_size_bytes"),
            }
        )
    elif archive.get("recording_archive_status"):
        # No object stored (still pending / disabled / failed) — keep the
        # provider URL playable and record why archival didn't land.
        update["recording_archive_status"] = archive.get("recording_archive_status")
        if archive.get("recording_archive_error"):
            update["recording_archive_error"] = archive.get("recording_archive_error")


async def _archive_recording_for_doc(
    *,
    call_id: str,
    recording_url: str,
    provider: str,
    callback_metadata: dict[str, Any] | None,
    existing_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Download `recording_url` and archive it to GCS for `call_id`.

    Returns the archive result dict (possibly empty on a hard error). Internally
    safe: archive_call_recording returns a status rather than raising for the
    common provider-not-ready / disabled / failed cases.
    """
    auth_provider = _archive_auth_provider(
        provider=provider,
        callback_metadata=callback_metadata,
        existing_doc=existing_doc,
    )
    synthesized = _synthesize_call_for_archive(
        call_id=call_id,
        provider=auth_provider,
        recording_url=recording_url,
        existing_doc=existing_doc,
    )
    try:
        return await archive_call_recording(
            search_id=(existing_doc or {}).get("search_id") or "",
            call=synthesized,
        )
    except Exception as exc:  # pragma: no cover - defensive; helper rarely raises
        logger.warning("Recording archive raised for call=%s: %s", call_id, exc)
        return {}


async def record_call_recording_ready(
    *,
    call_id: str | None,
    provider: str,
    recording_url: str | None,
    provider_metadata: dict[str, Any] | None = None,
) -> None:
    """Attach a late provider recording URL to an existing call attempt and
    archive the recording to GCS.

    This fires from the provider's recording-ready callback (Plivo's
    `/plivo/recording-callback` or Exotel's status callback), which is the
    moment the recording is actually retrievable. The synchronous archive
    attempt at call-completion time often races the provider's media
    processing and 403s; this callback is the reliable point to push the
    recording into long-term GCS storage so it outlives the provider's own
    retention (~30 days for Plivo).
    """

    normalized_call_id = (call_id or "").strip()
    normalized_recording_url = (recording_url or "").strip()
    if not normalized_call_id or not normalized_recording_url:
        return

    existing_doc = await call_attempts_collection.find_one({"call_id": normalized_call_id})

    update: dict[str, Any] = {
        "recording_url": normalized_recording_url,
        "provider_recording_url": normalized_recording_url,
        "recording_archive_status": "provider_url_ready",
        "updated_at": _now(),
    }
    if provider:
        update["provider"] = provider
    if provider_metadata:
        update["recording_provider_metadata"] = provider_metadata

    # Archive to GCS now, unless this row is already archived (duplicate or
    # late callback).
    already_archived = bool((existing_doc or {}).get("recording_object_name"))
    if not already_archived:
        archive = await _archive_recording_for_doc(
            call_id=normalized_call_id,
            recording_url=normalized_recording_url,
            provider=provider,
            callback_metadata=provider_metadata,
            existing_doc=existing_doc,
        )
        _apply_archive_result_to_update(update, archive)

    await call_attempts_collection.update_one(
        {"call_id": normalized_call_id},
        {"$set": update},
    )


async def backfill_pending_recordings(
    *,
    limit: int = 50,
    min_age_minutes: int = 15,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Safety-net sweep: archive recordings whose provider URL is known but
    that never landed in GCS (e.g. a lost recording-ready callback).

    Selects call attempts that have a `provider_recording_url` but no
    `recording_object_name`, last updated at least `min_age_minutes` ago (so we
    don't race the normal callback path), and re-runs archival. The provider
    URL stays the source of truth for playback regardless, so this is purely
    about getting a durable GCS copy before the provider purges the recording.
    """
    limit = max(1, min(int(limit), 500))
    cutoff = _now() - timedelta(minutes=max(0, int(min_age_minutes)))

    query = {
        "provider_recording_url": {"$nin": [None, ""]},
        "$or": [
            {"recording_object_name": {"$in": [None, ""]}},
            {"recording_object_name": {"$exists": False}},
        ],
        "updated_at": {"$lte": cutoff},
    }

    summary: dict[str, int] = {}
    scanned = 0
    archived: list[str] = []
    cursor = call_attempts_collection.find(query).sort("updated_at", -1).limit(limit)
    async for doc in cursor:
        scanned += 1
        call_id = (doc.get("call_id") or "").strip()
        recording_url = (doc.get("provider_recording_url") or doc.get("recording_url") or "").strip()
        if not call_id or not recording_url:
            summary["skipped_no_url"] = summary.get("skipped_no_url", 0) + 1
            continue

        if dry_run:
            summary["would_attempt"] = summary.get("would_attempt", 0) + 1
            continue

        archive = await _archive_recording_for_doc(
            call_id=call_id,
            recording_url=recording_url,
            provider=str(doc.get("provider") or ""),
            callback_metadata=None,
            existing_doc=doc,
        )
        status = archive.get("recording_archive_status") or "error"
        summary[status] = summary.get(status, 0) + 1

        update: dict[str, Any] = {"updated_at": _now()}
        _apply_archive_result_to_update(update, archive)
        await call_attempts_collection.update_one({"call_id": call_id}, {"$set": update})
        if archive.get("recording_object_name"):
            archived.append(call_id)

    return {
        "scanned": scanned,
        "archived_count": len(archived),
        "archived_call_ids": archived,
        "by_status": summary,
        "dry_run": dry_run,
        "min_age_minutes": min_age_minutes,
        "limit": limit,
    }


_PIPECAT_TERMINAL_STATUSES = {"completed", "failed", "no_answer", "busy"}


def _normalize_pipecat_status(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if text in {"completed", "complete", "done"}:
        return "completed"
    if text in {"failed", "error", "canceled", "cancelled"}:
        return "failed"
    if text in {"no-answer", "no_answer", "no answer"}:
        return "no_answer"
    if text == "busy":
        return "busy"
    return None


async def apply_pipecat_webhook_to_call_attempt(
    *,
    call_id: str,
    payload: dict[str, Any],
) -> None:
    """Update the call_attempts row directly from a Pipecat webhook body.

    The completion path normally relies on `_complete_call_attempt` polling
    for the webhook payload and calling `record_call_attempt`. That fails
    when the webhook lands on a different Cloud Run replica from the one
    that is polling, leaving the row stuck at status="busy". By writing
    here we close that loop synchronously regardless of which replica
    receives the webhook.
    """
    normalized_call_id = (call_id or "").strip()
    if not normalized_call_id:
        return

    raw_status = payload.get("status") or payload.get("event")
    event = str(payload.get("event") or "").strip().lower()
    status = _normalize_pipecat_status(raw_status)

    # A telephony "busy" status is ambiguous: on an in-progress callback it
    # means "still dialing" (don't flip the row), but on a hangup event it means
    # the vendor's line was BUSY / the call never connected — a TERMINAL outcome.
    # Without this, USER_BUSY (and similar non-answer hangups) left the row stuck
    # at status="busy" forever, which the batch runner then reported as a bogus
    # "timeout". Treat a hangup-with-busy as a terminal no_answer.
    if event == "hangup" and (status == "busy" or status is None):
        status = "no_answer"

    if status is None or status == "busy":
        # No terminal state to write yet. Recording-ready callbacks come
        # through with no status and should not flip the row.
        return

    now = _now()
    set_fields: dict[str, Any] = {
        "status": status,
        "provider": "pipecat",
        "updated_at": now,
    }
    if status != "busy":
        set_fields["completed_at"] = now

    transcript = payload.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        set_fields["transcript"] = transcript.strip()

    extracted = payload.get("extracted_data")
    if isinstance(extracted, dict) and extracted:
        set_fields["extracted_data"] = extracted

    duration = payload.get("duration") or payload.get("duration_seconds") or payload.get("call_length")
    if isinstance(duration, (int, float)):
        set_fields["duration_seconds"] = max(0, int(duration))
    elif isinstance(duration, str):
        try:
            set_fields["duration_seconds"] = max(0, int(float(duration)))
        except ValueError:
            pass

    metadata = payload.get("provider_metadata")
    if isinstance(metadata, dict) and metadata:
        set_fields["provider_metadata"] = metadata

    # Only flip the outcome label out of "calling" once we have a terminal
    # status; quote/discount calculations stay in `record_call_attempt`'s
    # path because they need the structured query context.
    update: dict[str, Any] = {"$set": set_fields}
    if status in {"failed", "no_answer"}:
        update.setdefault("$set", {})
        update["$set"]["outcome.label"] = status
        update["$set"]["outcome.notes"] = (
            payload.get("hangup_cause")
            or payload.get("reason")
            or update["$set"].get("outcome.notes")
        )

    await call_attempts_collection.update_one(
        {"call_id": normalized_call_id},
        update,
    )


async def record_online_results(
    *,
    search_id: str,
    query: StructuredQuery,
    strategy: PlatformStrategy,
    results: list[UnifiedResult],
) -> None:
    if not results:
        return

    product = await upsert_product(query.product, query.category)
    location = _location_doc(query.location)
    now = _now()

    for rank, result in enumerate(results, start=1):
        vendor = await upsert_online_vendor(result, strategy.platform_name, strategy.platform_id)
        result_key = f"{search_id}:{strategy.platform_id}:{_normalize_key(result.url or result.name)}:{rank}"
        await online_results_collection.update_one(
            {"result_key": result_key},
            {
                "$set": {
                    "result_key": result_key,
                    "search_id": search_id,
                    "product": product,
                    "vendor": vendor,
                    "platform": strategy.model_dump(mode="json"),
                    "location": location,
                    "result": result.model_dump(mode="json"),
                    "rank": rank,
                    "fetched_at": now,
                }
            },
            upsert=True,
        )
        await _record_observation(
            search_id=search_id,
            query=query,
            product=product,
            vendor=vendor,
            result=result,
            source_channel="online_fetch",
        )


async def record_raw_webhook(execution_id: str | None, payload: dict[str, Any]) -> None:
    await raw_webhooks_collection.insert_one(
        {
            "execution_id": execution_id,
            "payload": payload,
            "received_at": _now(),
        }
    )


async def get_whatsapp_session(wa_id: str) -> dict[str, Any] | None:
    session = await whatsapp_sessions_collection.find_one({"wa_id": wa_id})
    if session is None:
        return None
    session.pop("_id", None)
    return session


async def get_whatsapp_session_by_session_id(session_id: str) -> dict[str, Any] | None:
    session = await whatsapp_sessions_collection.find_one({"session_id": session_id})
    if session is None:
        return None
    session.pop("_id", None)
    return session


async def upsert_whatsapp_session(
    *,
    wa_id: str,
    phone_number: str,
    profile_name: str | None,
    session_id: str,
    last_message: str | None = None,
    last_search_id: str | None = None,
) -> None:
    now = _now()
    update: dict[str, Any] = {
        "wa_id": wa_id,
        "phone_number": phone_number,
        "profile_name": profile_name,
        "session_id": session_id,
        "updated_at": now,
    }
    if last_message is not None:
        update["last_message"] = last_message
    if last_search_id is not None:
        update["last_search_id"] = last_search_id

    await whatsapp_sessions_collection.update_one(
        {"wa_id": wa_id},
        {
            "$set": update,
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )


WHATSAPP_TURN_TTL_SECONDS = 15 * 60
WHATSAPP_PENDING_INBOUND_LIMIT = 20


async def try_acquire_whatsapp_turn(wa_id: str) -> bool:
    """Take the per-phone turn lock. Stale busy locks (past TTL) can be stolen."""
    now = _now()
    expires = now + timedelta(seconds=WHATSAPP_TURN_TTL_SECONDS)
    result = await whatsapp_sessions_collection.update_one(
        {
            "wa_id": wa_id,
            "$or": [
                {"turn_status": {"$exists": False}},
                {"turn_status": "idle"},
                {"turn_expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "turn_status": "busy",
                "turn_started_at": now,
                "turn_expires_at": expires,
                "updated_at": now,
            }
        },
    )
    return result.modified_count == 1


async def enqueue_whatsapp_inbound(wa_id: str, inbound: dict[str, Any]) -> None:
    session = await get_whatsapp_session(wa_id)
    pending = list((session or {}).get("pending_inbounds") or [])
    if len(pending) >= WHATSAPP_PENDING_INBOUND_LIMIT:
        logger.warning(
            "WhatsApp pending inbound queue full for %s; dropping %s",
            wa_id,
            inbound.get("message_id"),
        )
        return
    await whatsapp_sessions_collection.update_one(
        {"wa_id": wa_id},
        {"$push": {"pending_inbounds": inbound}, "$set": {"updated_at": _now()}},
    )


async def pop_whatsapp_pending_inbound(wa_id: str) -> dict[str, Any] | None:
    """Pop the next parked inbound, or release the turn if the queue is empty."""
    now = _now()
    expires = now + timedelta(seconds=WHATSAPP_TURN_TTL_SECONDS)
    doc = await whatsapp_sessions_collection.find_one_and_update(
        {"wa_id": wa_id, "pending_inbounds.0": {"$exists": True}},
        {
            "$pop": {"pending_inbounds": -1},
            "$set": {
                "turn_status": "busy",
                "turn_started_at": now,
                "turn_expires_at": expires,
                "updated_at": now,
            },
        },
    )
    if doc:
        pending = doc.get("pending_inbounds") or []
        if pending:
            return pending[0]

    released = await whatsapp_sessions_collection.update_one(
        {
            "wa_id": wa_id,
            "$or": [
                {"pending_inbounds": {"$exists": False}},
                {"pending_inbounds": {"$size": 0}},
                {"pending_inbounds": []},
            ],
        },
        {
            "$set": {"turn_status": "idle", "updated_at": now},
            "$unset": {
                "turn_expires_at": "",
                "turn_started_at": "",
                "active_search_id": "",
            },
        },
    )
    if released.matched_count == 0:
        session = await get_whatsapp_session(wa_id)
        if session and (session.get("pending_inbounds") or []):
            return await pop_whatsapp_pending_inbound(wa_id)
    return None


async def set_whatsapp_active_search(wa_id: str, search_id: str | None) -> None:
    now = _now()
    if search_id:
        await whatsapp_sessions_collection.update_one(
            {"wa_id": wa_id},
            {"$set": {"active_search_id": search_id, "updated_at": now}},
        )
        return
    await whatsapp_sessions_collection.update_one(
        {"wa_id": wa_id},
        {"$unset": {"active_search_id": ""}, "$set": {"updated_at": now}},
    )


async def whatsapp_message_exists(message_id: str) -> bool:
    return await whatsapp_messages_collection.count_documents({"message_id": message_id}, limit=1) > 0


async def try_record_whatsapp_message(
    *,
    message_id: str,
    wa_id: str,
    phone_number: str,
    session_id: str | None,
    direction: str,
    message_type: str,
    text: str | None = None,
    payload: dict[str, Any] | None = None,
    search_id: str | None = None,
    status: str = "received",
) -> bool:
    now = _now()
    try:
        result = await whatsapp_messages_collection.update_one(
            {"message_id": message_id},
            {
                "$setOnInsert": {
                    "message_id": message_id,
                    "wa_id": wa_id,
                    "phone_number": phone_number,
                    "session_id": session_id,
                    "direction": direction,
                    "message_type": message_type,
                    "text": text,
                    "payload": payload or {},
                    "search_id": search_id,
                    "status": status,
                    "received_at": now,
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    return result.upserted_id is not None


async def record_whatsapp_message(
    *,
    message_id: str,
    wa_id: str,
    phone_number: str,
    session_id: str | None,
    direction: str,
    message_type: str,
    text: str | None = None,
    payload: dict[str, Any] | None = None,
    search_id: str | None = None,
    status: str = "received",
) -> None:
    await try_record_whatsapp_message(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=phone_number,
        session_id=session_id,
        direction=direction,
        message_type=message_type,
        text=text,
        payload=payload,
        search_id=search_id,
        status=status,
    )


async def update_whatsapp_message_status(
    *,
    message_id: str,
    status: str,
    payload: dict[str, Any] | None = None,
) -> None:
    update: dict[str, Any] = {
        "status": status,
        "updated_at": _now(),
    }
    if payload is not None:
        update["payload"] = payload
    await whatsapp_messages_collection.update_one(
        {"message_id": message_id},
        {"$set": update},
    )


async def claim_failed_whatsapp_message_retry(message_id: str) -> bool:
    result = await whatsapp_messages_collection.update_one(
        {"message_id": message_id, "status": "failed"},
        {"$set": {"status": "sending", "updated_at": _now()}},
    )
    return result.modified_count == 1


def _session_title(state: ConversationState | None, fallback: str | None = None) -> str:
    if state:
        if state.product:
            return state.product
        if state.raw_query.strip():
            return state.raw_query.strip()[:80]
    if fallback:
        return fallback.strip()[:80]
    return "New chat"


async def upsert_chat_session(
    *,
    session_id: str,
    state: ConversationState,
    request_metadata: dict[str, Any] | None = None,
    last_message: str | None = None,
    title: str | None = None,
) -> None:
    now = _now()
    await chat_sessions_collection.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "title": title or _session_title(state, last_message),
                "state": state.model_dump(mode="json"),
                "location": state.location,
                "last_message": last_message,
                "request_metadata": request_metadata or {},
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )


async def append_chat_message(
    *,
    session_id: str,
    message_id: str | None = None,
    role: str,
    content: str,
    kind: str = "text",
    payload: dict[str, Any] | None = None,
) -> ChatHistoryMessage:
    message = ChatHistoryMessage(
        message_id=message_id or str(uuid4()),
        role=role,  # type: ignore[arg-type]
        content=content,
        kind=kind,  # type: ignore[arg-type]
        payload=payload,
    )
    await chat_messages_collection.update_one(
        {"message_id": message.message_id},
        {
            "$setOnInsert": {
                "message_id": message.message_id,
                "session_id": session_id,
                "role": message.role,
                "content": message.content,
                "kind": message.kind,
                "payload": message.payload,
                "created_at": message.created_at,
            }
        },
        upsert=True,
    )
    await chat_sessions_collection.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "last_message": content,
                "updated_at": _now(),
            }
        },
        upsert=True,
    )
    return message


async def list_chat_sessions(device_id: str | None, limit: int = 30) -> list[dict[str, Any]]:
    normalized_device_id = (device_id or "").strip()
    if not normalized_device_id:
        return []

    query: dict[str, Any] = {"request_metadata.device_id": normalized_device_id}
    cursor = chat_sessions_collection.find(query).sort("updated_at", -1).limit(limit)
    sessions = await cursor.to_list(length=limit)
    for session in sessions:
        session.pop("_id", None)
    return sessions


async def get_chat_session_detail(session_id: str) -> dict[str, Any] | None:
    session = await chat_sessions_collection.find_one({"session_id": session_id})
    if session is None:
        return None
    session.pop("_id", None)

    messages_cursor = chat_messages_collection.find({"session_id": session_id}).sort("created_at", 1)
    messages = await messages_cursor.to_list(length=500)
    for message in messages:
        message.pop("_id", None)

    latest_search_cursor = search_sessions_collection.find({"session_id": session_id}).sort("updated_at", -1).limit(1)
    latest_searches = await latest_search_cursor.to_list(length=1)
    latest_search = latest_searches[0] if latest_searches else None
    if latest_search:
        latest_search.pop("_id", None)

    return {
        **session,
        "messages": messages,
        "latest_search": latest_search,
    }


async def load_conversation_state(session_id: str) -> ConversationState | None:
    session = await chat_sessions_collection.find_one({"session_id": session_id})
    if not session:
        return None
    state = session.get("state")
    if not isinstance(state, dict):
        return None
    return ConversationState.model_validate(state)


async def _record_observation(
    *,
    search_id: str,
    query: StructuredQuery,
    product: dict[str, Any],
    vendor: dict[str, Any],
    result: UnifiedResult,
    source_channel: str,
) -> None:
    now = _now()
    location = _location_doc(query.location or result.address)
    observation_key = ":".join(
        [
            vendor["vendor_key"],
            product["product_key"],
            location["pincode"] or location["normalized"],
            result.source_type,
        ]
    )

    observation_doc = {
        "observation_key": observation_key,
        "search_id": search_id,
        "product": product,
        "vendor": vendor,
        "location": location,
        "source_type": result.source_type,
        "source_channel": source_channel,
        "latest_price": result.price,
        "availability": result.availability,
        "delivery_time": result.delivery_time,
        "confidence": result.confidence,
        "url": result.url,
        "notes": result.notes,
        "last_observed_at": now,
        "expires_at": now + timedelta(days=OBSERVATION_TTL_DAYS),
        "is_mock": result.is_mock,
    }

    await vendor_product_observations_collection.update_one(
        {"observation_key": observation_key},
        {
            "$set": observation_doc,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    await price_history_collection.insert_one(
        {
            "search_id": search_id,
            "product": product,
            "vendor": vendor,
            "location": location,
            "source_type": result.source_type,
            "source_channel": source_channel,
            "price": result.price,
            "availability": result.availability,
            "delivery_time": result.delivery_time,
            "confidence": result.confidence,
            "url": result.url,
            "notes": result.notes,
            "observed_at": now,
            "is_mock": result.is_mock,
        }
    )
