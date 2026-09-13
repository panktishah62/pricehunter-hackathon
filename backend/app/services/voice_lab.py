from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.categories.gold.selection import plan_gold_bullion_query
from app.config import settings
from app.database import (
    call_attempts_collection,
    search_sessions_collection,
    voice_lab_campaign_presets_collection,
    voice_lab_sessions_collection,
)
from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo, VoiceCallResult
from app.services import comparator, online_pipeline, persistence, query_structurer
from app.services.vendor_discovery import discover_vendors
from app.services.voice_agent import call_vendor_with_provider, poll_call_result
from app.services.voice_extractor import (
    schema_from_call,
    extract_from_call_result,
    record_call_for_reliability,
)

logger = logging.getLogger(__name__)


@dataclass
class VoiceLabSession:
    search_id: str
    query: StructuredQuery
    online_results: list[UnifiedResult]
    vendors: list[VendorInfo]
    benchmark_price: float | None
    created_at: datetime


_SESSIONS: dict[str, VoiceLabSession] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _vendor_key(vendor: VendorInfo) -> str:
    if vendor.vendor_id:
        return vendor.vendor_id
    if vendor.place_id:
        return f"offline:place:{vendor.place_id}"
    digits = "".join(char for char in (vendor.phone or "") if char.isdigit())
    if digits:
        return f"offline:phone:{digits}"
    normalized = re.sub(r"[^a-z0-9]+", "-", (vendor.name or "unknown").lower()).strip("-")
    return f"offline:name:{normalized or 'unknown'}"


def _with_vendor_id(vendor: VendorInfo) -> VendorInfo:
    if vendor.vendor_id:
        return vendor
    data = vendor.model_dump()
    data["vendor_id"] = _vendor_key(vendor)
    return VendorInfo(**data)


def _benchmark_price(results: list[UnifiedResult]) -> float | None:
    prices = [
        float(result.price)
        for result in results
        if result.price is not None and math.isfinite(float(result.price)) and float(result.price) > 0
    ]
    return min(prices) if prices else None


def _session_gold_benchmark_per_gram(session: "VoiceLabSession") -> float | None:
    """Per-gram benchmark for a gold session, for quote_vs_benchmark in extraction.

    Returns None for non-gold sessions or when the benchmark can't be
    normalized. Reuses the same normalization guard as the campaign anchor.
    """
    benchmark = getattr(session, "benchmark_price", None)
    if not benchmark:
        return None
    category = (getattr(session.query, "category", None) or "").lower()
    if "gold" not in category:
        return None
    try:
        from app.categories.gold.planner import extract_quantity_grams
        from app.services.voice_agent import _gold_benchmark_per_gram

        grams = extract_quantity_grams(session.query)
        return _gold_benchmark_per_gram(benchmark, grams)
    except Exception:
        return None


def _location_for_discovery(query: StructuredQuery) -> str:
    location = (query.location or "").strip()
    return location if location and location != "unknown" else "Rajkot"


def provider_options() -> list[dict[str, Any]]:
    return [
        {
            "id": "plivo_bridge",
            "label": "Plivo + ElevenLabs bridge",
            "enabled": bool(settings.mock_voice_calls or settings.plivo_bridge_base_url),
            "detail": "Plivo dials/carries the call; ElevenLabs handles the voice agent over the bridge.",
        },
        {
            "id": "pipecat",
            "label": "Pipecat agent",
            "enabled": bool(settings.mock_voice_calls or settings.pipecat_agent_base_url),
            "detail": f"Uses the hosted Pipecat agent over the {settings.pipecat_telephony_provider or 'plivo'} phone rail.",
        },
        {
            "id": "bolna",
            "label": "Bolna",
            "enabled": bool(settings.mock_voice_calls or (settings.bolna_api_key and settings.bolna_agent_id)),
            "detail": "Uses the legacy Bolna outbound call adapter.",
        },
        {
            "id": "elevenlabs_plivo",
            "label": "ElevenLabs SIP",
            "enabled": bool(
                settings.mock_voice_calls
                or (
                    settings.elevenlabs_api_key
                    and settings.elevenlabs_agent_id
                    and settings.elevenlabs_agent_phone_number_id
                )
            ),
            "detail": "Uses ElevenLabs outbound SIP call support.",
        },
        {
            "id": "exotel",
            "label": "Exotel direct",
            "enabled": False,
            "detail": "Telephony rail only. Direct PriceHunter adapter is not wired yet.",
        },
        {
            "id": "gemini_live",
            "label": "Gemini Agent (speech-to-speech)",
            "enabled": bool(
                settings.mock_voice_calls
                or (settings.pipecat_agent_base_url and settings.gemini_live_enabled)
            ),
            "detail": f"Gemini Agent over the {settings.gemini_live_telephony_provider or 'plivo'} phone rail.",
        },
    ]


def _provider_enabled(provider_id: str) -> bool:
    return any(option["id"] == provider_id and option["enabled"] for option in provider_options())


async def _rank_vendors_for_lab(vendors: list[VendorInfo], category: str, max_vendors: int) -> list[VendorInfo]:
    prepared = [_with_vendor_id(vendor) for vendor in vendors if vendor.phone]
    if not prepared:
        return []

    try:
        from app.services.vendor_reliability import (
            CandidateVendor,
            is_vendor_eligible_to_call,
            select_vendors_to_call,
        )

        eligible: list[CandidateVendor] = []
        ineligible: list[CandidateVendor] = []
        for vendor in prepared:
            key = _vendor_key(vendor)
            candidate = CandidateVendor(
                vendor_key=key,
                handle=vendor,
                source=vendor.bucket or vendor.preferred_contact_channel,
                phone=vendor.phone,
                place_id=vendor.place_id,
            )
            ok, _reason = await is_vendor_eligible_to_call(key)
            if ok:
                eligible.append(candidate)
            else:
                ineligible.append(candidate)

        ordered: list[CandidateVendor] = []
        if eligible:
            ordered.extend(
                await select_vendors_to_call(
                    candidates=eligible,
                    category=category or "electronics",
                    n=len(eligible),
                )
            )
        ordered.extend(ineligible)
        return [candidate.handle for candidate in ordered[:max(1, max_vendors)]]
    except Exception as exc:  # pragma: no cover - reliability should not block manual tooling
        logger.warning("Voice Lab reliability ranking skipped: %s", exc)
        return prepared[:max(1, max_vendors)]


async def _find_lab_inputs(
    query: StructuredQuery,
    *,
    search_id: str,
    max_vendors: int,
    include_online: bool,
    include_vendors: bool,
) -> tuple[list[UnifiedResult], list[VendorInfo]]:
    if not include_online and not include_vendors:
        return [], []

    if query.category == "gold" and query.route_handler == "gold.bullion":
        plan = await plan_gold_bullion_query(query)
        vendors = [vendor for vendor in plan.discovered_vendors if vendor.phone] if include_vendors else []
        return plan.live_results if include_online else [], vendors

    online_results: list[UnifiedResult] = []
    if include_online:
        online_results = await online_pipeline.run(query, search_id=search_id)

    vendors: list[VendorInfo] = []
    if include_vendors:
        vendors = await discover_vendors(query.product, query.category, _location_for_discovery(query))
    return online_results, vendors[:max_vendors]


async def create_session(
    *,
    raw_query: str,
    location: str | None,
    max_vendors: int,
    include_online: bool,
    include_vendors: bool = True,
    request_metadata: dict[str, Any] | None = None,
) -> VoiceLabSession:
    query = await query_structurer.structure_query(raw_query)
    if location and location.strip():
        query.location = location.strip()

    search_id = str(uuid.uuid4())
    online_results, vendors = await _find_lab_inputs(
        query,
        search_id=search_id,
        max_vendors=max_vendors,
        include_online=include_online,
        include_vendors=include_vendors,
    )
    ranked_vendors = await _rank_vendors_for_lab(vendors, query.category, max_vendors)
    benchmark = _benchmark_price(online_results)

    await persistence.initialize_search_session(
        search_id=search_id,
        query=query,
        search_strategy="voice_lab",
        request_metadata=request_metadata,
        source_flow="voice_lab",
        discovered_vendors=ranked_vendors,
    )

    session = VoiceLabSession(
        search_id=search_id,
        query=query,
        online_results=comparator.rank(online_results, query.intent, category=query.category),
        vendors=ranked_vendors,
        benchmark_price=benchmark,
        created_at=_now(),
    )
    _SESSIONS[search_id] = session
    await _persist_session(session)
    return session


def get_session(search_id: str) -> VoiceLabSession | None:
    return _SESSIONS.get(search_id)


async def _persist_session(session: VoiceLabSession) -> None:
    try:
        doc = {
            "search_id": session.search_id,
            "query": session.query.model_dump(mode="json"),
            "online_results": [result.model_dump(mode="json") for result in session.online_results],
            "vendors": [vendor.model_dump(mode="json") for vendor in session.vendors],
            "benchmark_price": session.benchmark_price,
            "updated_at": _now(),
        }
        await voice_lab_sessions_collection.update_one(
            {"search_id": session.search_id},
            {"$set": doc, "$setOnInsert": {"created_at": session.created_at}},
            upsert=True,
        )
    except Exception as exc:  # pragma: no cover - storage best effort
        logger.warning("Voice Lab session persist failed for %s: %s", session.search_id, exc)


async def _hydrate_session(search_id: str) -> VoiceLabSession | None:
    try:
        doc = await voice_lab_sessions_collection.find_one({"search_id": search_id})
    except Exception as exc:  # pragma: no cover - storage best effort
        logger.warning("Voice Lab session hydrate failed for %s: %s", search_id, exc)
        return None
    if not doc:
        return None
    try:
        query = StructuredQuery.model_validate(doc.get("query") or {})
        online_results = [
            UnifiedResult.model_validate(result)
            for result in (doc.get("online_results") or [])
        ]
        vendors = [
            VendorInfo.model_validate(vendor)
            for vendor in (doc.get("vendors") or [])
        ]
    except Exception as exc:
        logger.warning("Voice Lab session hydrate decode failed for %s: %s", search_id, exc)
        return None
    created_at = doc.get("created_at") or _now()
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            created_at = _now()
    session = VoiceLabSession(
        search_id=search_id,
        query=query,
        online_results=online_results,
        vendors=vendors,
        benchmark_price=doc.get("benchmark_price"),
        created_at=created_at,
    )
    _SESSIONS[search_id] = session
    return session


async def ensure_session(search_id: str) -> VoiceLabSession | None:
    session = _SESSIONS.get(search_id)
    if session is not None:
        return session
    return await _hydrate_session(search_id)


async def _call_attempt_docs(search_id: str) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    cursor = call_attempts_collection.find({"search_id": search_id}).sort("updated_at", -1).limit(50)
    async for doc in cursor:
        docs.append(_public_call_doc(doc))
    return docs


async def list_recent_calls(limit: int = 20, offset: int = 0) -> tuple[list[dict[str, Any]], bool]:
    """Recent voice call attempts joined with their search session metadata.

    Shows recent call attempts regardless of how the owning search session was
    created (voice_lab, chat or api). Calls placed from the chat flow are the
    common case in production, so restricting to `source_flow="voice_lab"` made
    the recordings panel look empty even though calls were being placed and
    recorded.

    `offset` enables scroll pagination in the recordings panel: the UI requests
    successive pages (offset 0, 20, 40, …) keyed on the same `started_at` sort.

    Returns ``(calls, has_more)``. We over-fetch one row (`limit + 1`) and trim
    it off so `has_more` is accurate even when the total count is an exact
    multiple of the page size — otherwise the client would make a wasted
    round-trip for an always-empty final page.
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    fetch_n = limit + 1  # over-fetch one to detect a genuine next page

    # Start from the most recent call attempts directly so chat/api calls are
    # included, not just those owned by a voice_lab session. Sort by call time
    # (`started_at`), not `updated_at`: the recording backfill/archival path
    # rewrites `updated_at` on old rows, which would otherwise yo-yo days-old
    # calls back to the top of the list. The trailing `_id` is a unique
    # tiebreaker so offset paging is deterministic — without it, documents that
    # tie on all the timestamp fields can reorder between page requests and a
    # boundary row gets silently skipped (or duplicated).
    cursor = (
        call_attempts_collection.find({})
        .sort([("started_at", -1), ("called_at", -1), ("updated_at", -1), ("_id", -1)])
        .skip(offset)
        .limit(fetch_n)
    )
    rows: list[dict[str, Any]] = []
    search_ids: set[str] = set()
    async for doc in cursor:
        rows.append(doc)
        sid = doc.get("search_id")
        if sid:
            search_ids.add(sid)

    has_more = len(rows) > limit
    rows = rows[:limit]
    # Recompute search_ids for the trimmed page so we don't fetch session
    # metadata for a row we just dropped.
    if has_more:
        search_ids = {doc.get("search_id") for doc in rows if doc.get("search_id")}

    if not rows:
        return [], False

    # Join session metadata (query/product/location) for any owning sessions.
    session_meta: dict[str, dict[str, Any]] = {}
    if search_ids:
        sessions = search_sessions_collection.find(
            {"search_id": {"$in": list(search_ids)}},
            {"search_id": 1, "query": 1, "product": 1, "location": 1, "created_at": 1, "source_flow": 1},
        )
        async for session_doc in sessions:
            sid = session_doc.get("search_id")
            if sid:
                session_meta[sid] = {
                    "search_id": sid,
                    "query": session_doc.get("query"),
                    "product": session_doc.get("product"),
                    "location": session_doc.get("location"),
                    "created_at": session_doc.get("created_at"),
                    "source_flow": session_doc.get("source_flow"),
                }

    public_rows: list[dict[str, Any]] = []
    for doc in rows:
        public = _public_call_doc(doc)
        meta = session_meta.get(public.get("search_id"))
        if meta:
            public["session"] = _clean_json(meta)
        public_rows.append(public)
    return public_rows, has_more


def _clean_json(value: Any) -> Any:
    if isinstance(value, datetime):
        # Mongo stores naive UTC datetimes; emit explicit UTC so JS parses them
        # in UTC instead of as local time.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, list):
        return [_clean_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items() if key != "_id"}
    if value.__class__.__name__ == "ObjectId":
        return str(value)
    return value


def _public_call_doc(doc: dict[str, Any]) -> dict[str, Any]:
    public = _clean_json(doc)
    call_id = public.get("call_id")
    has_recording = bool(
        public.get("recording_object_name")
        or public.get("provider_recording_url")
        or public.get("recording_url")
    )
    public["recording_stream_url"] = f"/api/voice-lab/calls/{call_id}/recording" if call_id and has_recording else None
    public["provider_label"] = _provider_display_label(doc)
    return public


# Human-readable names for the underlying telephony rail. Voice Lab stamps
# every call attempt with provider="pipecat" regardless of the rail it rode,
# so the rail (Plivo vs Exotel) lives in provider metadata. We surface a
# distinct label so the UI can tell them apart.
_PROVIDER_DISPLAY_NAMES = {
    "pipecat": "Pipecat",
    "plivo": "Pipecat · Plivo",
    "plivo_bridge": "Plivo Bridge",
    "exotel": "Pipecat · Exotel",
    "bolna": "Bolna",
    "elevenlabs_plivo": "ElevenLabs · Plivo",
    "gemini_live": "Gemini Agent",
}


def _telephony_rail(doc: dict[str, Any]) -> str:
    """Return the underlying telephony rail for a call/recording, if known.

    Reads `telephony_provider` from the call's provider metadata (set on
    completion) or the recording's provider metadata (set when a late
    recording URL arrives), falling back to empty when unknown.
    """
    for source_key in ("provider_metadata", "recording_provider_metadata"):
        metadata = doc.get(source_key)
        if isinstance(metadata, dict):
            telephony = str(metadata.get("telephony_provider") or "").strip().lower()
            if telephony:
                return telephony
    return ""


def _provider_display_label(doc: dict[str, Any]) -> str:
    """Build a UI label that distinguishes the telephony rail under pipecat."""
    base_provider = str(doc.get("provider") or "").strip().lower()
    rail = _telephony_rail(doc)
    # When the call rode the pipecat agent, surface the rail it used.
    if base_provider == "pipecat" and rail:
        return _PROVIDER_DISPLAY_NAMES.get(rail, f"Pipecat · {rail.title()}")
    return _PROVIDER_DISPLAY_NAMES.get(base_provider or rail, (base_provider or rail or "").title())


async def session_payload(session: VoiceLabSession) -> dict[str, Any]:
    vendor_rows = await _vendor_rows(session)
    return {
        "search_id": session.search_id,
        "query": session.query.model_dump(mode="json"),
        "benchmark_price": session.benchmark_price,
        "online_results": [result.model_dump(mode="json") for result in session.online_results],
        "vendors": vendor_rows,
        "calls": await _call_attempt_docs(session.search_id),
        "provider_options": provider_options(),
        "created_at": session.created_at.isoformat(),
    }


async def _vendor_rows(session: VoiceLabSession) -> list[dict[str, Any]]:
    try:
        from app.services.vendor_reliability import bulk_load_reliability, is_vendor_eligible_to_call

        keys = [_vendor_key(vendor) for vendor in session.vendors]
        snapshots = await bulk_load_reliability(keys, category=session.query.category)
        rows = []
        for vendor in session.vendors:
            key = _vendor_key(vendor)
            snap = snapshots.get(key)
            eligible, reason = await is_vendor_eligible_to_call(key)
            rows.append(
                {
                    **vendor.model_dump(mode="json"),
                    "id": key,
                    "callable": bool(vendor.phone and eligible),
                    "eligibility_reason": reason or None,
                    "reliability": {
                        "score": round(snap.reliability_score, 4) if snap else None,
                        "pickup_rate": round(snap.pickup_mean, 4) if snap else None,
                        "quote_rate": round(snap.quote_mean, 4) if snap else None,
                        "n_calls": snap.n_calls if snap else 0,
                        "last_called_at": snap.last_called_at.isoformat() if snap and snap.last_called_at else None,
                    },
                }
            )
        return rows
    except Exception as exc:  # pragma: no cover - DB availability should not blank the lab
        logger.warning("Voice Lab vendor reliability rows failed: %s", exc)
        return [
            {
                **vendor.model_dump(mode="json"),
                "id": _vendor_key(vendor),
                "callable": bool(vendor.phone),
                "eligibility_reason": None,
                "reliability": {
                    "score": None,
                    "pickup_rate": None,
                    "quote_rate": None,
                    "n_calls": 0,
                    "last_called_at": None,
                },
            }
            for vendor in session.vendors
        ]


def _find_vendor(session: VoiceLabSession, vendor_id: str) -> VendorInfo | None:
    return next((vendor for vendor in session.vendors if _vendor_key(vendor) == vendor_id), None)


def _direct_vendor(*, phone: str, name: str | None, address: str | None) -> VendorInfo:
    label = (name or "").strip() or phone.strip()
    return VendorInfo(
        name=label,
        phone=phone.strip(),
        address=(address or "Direct number").strip() or "Direct number",
        vendor_id=_vendor_key(
            VendorInfo(
                name=label,
                phone=phone.strip(),
                address=(address or "Direct number").strip() or "Direct number",
            )
        ),
        call_available=True,
        preferred_contact_channel="phone",
        bucket="voice_lab_direct",
    )


def _upsert_direct_vendor(session: VoiceLabSession, vendor: VendorInfo) -> None:
    vendor_id = _vendor_key(vendor)
    if any(_vendor_key(existing) == vendor_id for existing in session.vendors):
        return
    session.vendors = [vendor, *session.vendors]


def _product_key(query: StructuredQuery) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", query.product.lower()).strip("-")
    return f"{query.category}:{normalized or 'unknown'}"


async def _complete_call_attempt(
    *,
    session: VoiceLabSession,
    started: VoiceCallResult,
    timeout_seconds: int,
) -> None:
    try:
        completed = await poll_call_result(started, timeout_seconds=timeout_seconds)
        extracted_result: UnifiedResult | None = None
        if completed.status == "completed" or completed.transcript or completed.extracted_data:
            extracted_result = await extract_from_call_result(
                completed,
                session.query.product,
                category=session.query.category,
                product_key=_product_key(session.query),
                location_pincode=None,
                benchmark_rate_per_gram=_session_gold_benchmark_per_gram(session),
                extraction_schema=schema_from_call(completed),
            )
        elif completed.status in {"failed", "no_answer"}:
            await record_call_for_reliability(
                call=completed,
                result=None,
                category=session.query.category,
                product_key=None,
                location_pincode=None,
            )

        await persistence.record_call_attempt(
            search_id=session.search_id,
            query=session.query,
            call=completed,
            extracted_result=extracted_result,
            benchmark_price=session.benchmark_price,
        )
    except Exception:
        logger.exception("Voice Lab async call completion failed for call_id=%s", started.call_id)


async def call_selected_vendor(
    *,
    search_id: str,
    vendor_id: str | None,
    provider_id: str,
    timeout_seconds: int | None = None,
    direct_phone: str | None = None,
    direct_name: str | None = None,
    direct_address: str | None = None,
    campaign_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = await ensure_session(search_id)
    if session is None:
        raise KeyError("Voice Lab session not found.")
    if not _provider_enabled(provider_id):
        raise ValueError(f"Provider '{provider_id}' is not enabled for Voice Lab calls.")

    if direct_phone and direct_phone.strip():
        vendor = _direct_vendor(
            phone=direct_phone,
            name=direct_name,
            address=direct_address,
        )
        _upsert_direct_vendor(session, vendor)
        await _persist_session(session)
    elif vendor_id:
        vendor = _find_vendor(session, vendor_id)
        if vendor is None:
            raise KeyError("Vendor not found in this Voice Lab session.")
    else:
        raise ValueError("Choose a vendor or enter a direct phone number.")

    started = await call_vendor_with_provider(
        vendor,
        session.query.product,
        provider_id,
        campaign_config=campaign_config,
        query=session.query,
        benchmark_price=session.benchmark_price,
    )
    await persistence.record_call_attempt(
        search_id=session.search_id,
        query=session.query,
        call=started,
        benchmark_price=session.benchmark_price,
    )

    asyncio.create_task(
        _complete_call_attempt(
            session=session,
            started=started,
            timeout_seconds=timeout_seconds or 120,
        )
    )

    return {
        "call": await get_call_attempt(started.call_id),
        "session": await session_payload(session),
    }


async def get_call_attempt(call_id: str) -> dict[str, Any] | None:
    doc = await call_attempts_collection.find_one({"call_id": call_id})
    return _public_call_doc(doc) if doc else None


# ---------------------------------------------------------------------------
# Campaign presets
# ---------------------------------------------------------------------------


def _slugify_preset(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or uuid.uuid4().hex[:8]


async def list_campaign_presets() -> list[dict[str, Any]]:
    cursor = voice_lab_campaign_presets_collection.find().sort("updated_at", -1).limit(100)
    rows: list[dict[str, Any]] = []
    async for doc in cursor:
        rows.append(_clean_json(doc))
    return rows


async def get_campaign_preset(preset_id: str) -> dict[str, Any] | None:
    doc = await voice_lab_campaign_presets_collection.find_one({"preset_id": preset_id})
    return _clean_json(doc) if doc else None


async def upsert_campaign_preset(
    *,
    preset_id: str | None,
    name: str,
    description: str | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Preset name is required.")
    if not isinstance(config, dict):
        raise ValueError("Preset config must be an object.")
    pid = (preset_id or "").strip() or _slugify_preset(name)
    now = _now()
    doc = {
        "preset_id": pid,
        "name": name,
        "description": (description or "").strip() or None,
        "config": config,
        "updated_at": now,
    }
    await voice_lab_campaign_presets_collection.update_one(
        {"preset_id": pid},
        {"$set": doc, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    fresh = await voice_lab_campaign_presets_collection.find_one({"preset_id": pid})
    return _clean_json(fresh) if fresh else doc


async def delete_campaign_preset(preset_id: str) -> bool:
    result = await voice_lab_campaign_presets_collection.delete_one({"preset_id": preset_id})
    return result.deleted_count > 0


# ---------------------------------------------------------------------------
# Bulk vendor calling
# ---------------------------------------------------------------------------


async def call_vendors_bulk(
    *,
    search_id: str,
    vendor_ids: list[str] | None,
    provider_id: str,
    timeout_seconds: int | None = None,
    campaign_config: dict[str, Any] | None = None,
    concurrency: int | None = None,
) -> dict[str, Any]:
    """Place calls to multiple vendors in a session in parallel.

    Dialing fans out under an `asyncio.Semaphore` whose size is the requested
    `concurrency`, clamped to ``[1, settings.voice_lab_bulk_max_concurrency]`` so
    an operator typo can never launch an unbounded number of simultaneous live
    calls. A small `pipecat_call_spacing_seconds` stagger is applied between
    dispatch starts (not between completions) to avoid a thundering herd on the
    telephony rail while still overlapping the calls. Each call's lifecycle runs
    async via `_complete_call_attempt`, same as `call_selected_vendor`.

    `concurrency=1` reproduces the previous sequential behavior.
    """
    session = await ensure_session(search_id)
    if session is None:
        raise KeyError("Voice Lab session not found.")
    if not _provider_enabled(provider_id):
        raise ValueError(f"Provider '{provider_id}' is not enabled for Voice Lab calls.")

    if vendor_ids:
        ids = [vid for vid in vendor_ids if vid]
    else:
        ids = [
            _vendor_key(vendor)
            for vendor in session.vendors
            if vendor.phone
        ]
    if not ids:
        raise ValueError("No vendors selected for bulk call.")

    # Clamp the requested concurrency to a safe server-side ceiling. Default to
    # the hard cap when the caller doesn't specify one.
    hard_cap = max(1, int(settings.voice_lab_bulk_max_concurrency))
    requested = hard_cap if concurrency is None else int(concurrency)
    effective_concurrency = max(1, min(requested, hard_cap))

    spacing = max(0.0, float(settings.pipecat_call_spacing_seconds))
    semaphore = asyncio.Semaphore(effective_concurrency)
    started_calls: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    async def _dial(index: int, vendor_id: str) -> None:
        vendor = _find_vendor(session, vendor_id)
        if vendor is None:
            errors.append({"vendor_id": vendor_id, "error": "vendor not found"})
            return
        async with semaphore:
            # Stagger dispatch starts so we don't slam the telephony rail with N
            # simultaneous /start posts; overlap is still bounded by the
            # semaphore, not by this sleep.
            if spacing:
                await asyncio.sleep(spacing * (index % effective_concurrency))
            try:
                started = await call_vendor_with_provider(
                    vendor,
                    session.query.product,
                    provider_id,
                    campaign_config=campaign_config,
                    query=session.query,
                    benchmark_price=session.benchmark_price,
                )
                await persistence.record_call_attempt(
                    search_id=session.search_id,
                    query=session.query,
                    call=started,
                    benchmark_price=session.benchmark_price,
                )
                asyncio.create_task(
                    _complete_call_attempt(
                        session=session,
                        started=started,
                        timeout_seconds=timeout_seconds or 120,
                    )
                )
                started_calls.append({"vendor_id": vendor_id, "call_id": started.call_id})
            except Exception as exc:
                logger.warning("Bulk call failed for vendor=%s: %s", vendor_id, exc)
                errors.append({"vendor_id": vendor_id, "error": str(exc)})

    await asyncio.gather(
        *[_dial(index, vendor_id) for index, vendor_id in enumerate(ids)]
    )

    return {
        "started": started_calls,
        "errors": errors,
        "concurrency": effective_concurrency,
        "session": await session_payload(session),
    }


async def recording_bytes(call_id: str) -> tuple[bytes, str] | None:
    doc = await call_attempts_collection.find_one({"call_id": call_id})
    if not doc:
        return None

    content_type = doc.get("recording_content_type") or "audio/mpeg"
    object_name = doc.get("recording_object_name")
    if object_name and settings.voice_recordings_bucket:
        try:
            from google.cloud import storage
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("google-cloud-storage is not installed") from exc

        def _download_from_gcs() -> bytes:
            client = storage.Client()
            bucket = client.bucket(settings.voice_recordings_bucket)
            return bucket.blob(object_name).download_as_bytes()

        return await asyncio.to_thread(_download_from_gcs), content_type

    recording_url = doc.get("provider_recording_url") or doc.get("recording_url")
    if not recording_url:
        return None

    headers = _recording_headers(_recording_auth_provider(doc))
    import httpx

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            response = await client.get(recording_url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {403, 404}:
                logger.info(
                    "Voice Lab recording is not ready yet for call=%s status=%s",
                    call_id,
                    exc.response.status_code,
                )
                return None
            raise
        return response.content, response.headers.get("content-type") or content_type


def _recording_auth_provider(doc: dict[str, Any]) -> str:
    """Pick the provider whose credentials can fetch this recording.

    Voice Lab stamps every call attempt with provider="pipecat" regardless of
    the underlying telephony rail, but the recording URL is hosted by the rail
    (Plivo or Exotel) and needs that rail's basic-auth credentials. Prefer the
    telephony provider recorded on the recording/provider metadata, falling
    back to the call's top-level provider.
    """
    rail = _telephony_rail(doc)
    if rail:
        return rail
    return str(doc.get("provider") or "")


def _recording_headers(provider: str) -> dict[str, str]:
    def _basic(username: str, password: str) -> str:
        import base64

        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    if provider in {"plivo", "plivo_bridge", "pipecat"} and settings.plivo_auth_id and settings.plivo_auth_token:
        return {"Authorization": _basic(settings.plivo_auth_id, settings.plivo_auth_token)}
    if provider == "exotel" and settings.exotel_api_key and settings.exotel_api_token:
        return {"Authorization": _basic(settings.exotel_api_key, settings.exotel_api_token)}
    return {}
