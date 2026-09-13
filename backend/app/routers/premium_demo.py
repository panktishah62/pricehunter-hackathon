from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import logging
import random
import re
import time
import uuid
from typing import Literal, Optional

import httpx
from amplitude import BaseEvent
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.config import settings
from app.amplitude import get_amplitude_client
from app import meta_conversions
from app.models.schemas import StructuredQuery, UnifiedResult
from app.services import chat_session, online_pipeline, request_context

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/demo-premium", tags=["demo-premium"])

DemoCategory = Literal["electronics", "medical", "gold"]
_DEMO_UNLOCK_CACHE_TTL_SECONDS = 30 * 60
_DEMO_UNLOCK_CACHE_MAX_SIZE = 256


class DemoSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=160)
    location: Optional[str] = Field(default=None, max_length=80)


class DemoResult(BaseModel):
    id: str
    source_type: Literal["online", "offline"]
    name: str
    price: int
    currency: str = "INR"
    delivery_time: Optional[str] = None
    availability: bool = True
    url: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    notes: Optional[str] = None
    locked: bool = False


class DemoSavings(BaseModel):
    best_online_price: int
    best_offline_price: int
    savings_amount: int
    savings_percent: int
    basis: str


class DemoSearchResponse(BaseModel):
    search_id: str
    query: str
    product: str
    location: str
    category: DemoCategory
    online_results: list[DemoResult]
    locked_preview: list[DemoResult]
    savings: DemoSavings
    amount_paise: int
    currency: str = "INR"
    full_results: list[DemoResult] = Field(default_factory=list, exclude=True)


class DemoOrderRequest(BaseModel):
    search_id: str = Field(min_length=8, max_length=80)
    query: str = Field(min_length=1, max_length=160)


class DemoOrderResponse(BaseModel):
    order_id: str
    razorpay_key_id: str
    amount: int
    currency: str = "INR"
    demo_mode: bool = False


class DemoVerifyPaymentRequest(BaseModel):
    search_id: str = Field(min_length=8, max_length=80)
    query: str = Field(min_length=1, max_length=160)
    location: Optional[str] = Field(default=None, max_length=80)
    category: Optional[DemoCategory] = None
    best_online_price: Optional[int] = None
    savings_percent: Optional[int] = None
    razorpay_order_id: Optional[str] = None
    razorpay_payment_id: Optional[str] = None
    razorpay_signature: Optional[str] = None
    demo_mode: bool = False


class DemoUnlockResponse(BaseModel):
    search_id: str
    full_results: list[DemoResult]
    savings: DemoSavings
    payment_verified: bool


@dataclass
class DemoUnlockCacheEntry:
    full_results: list[DemoResult]
    savings: DemoSavings
    expires_at: float


_DEMO_UNLOCK_CACHE: OrderedDict[str, DemoUnlockCacheEntry] = OrderedDict()


def _prune_demo_unlock_cache(now: float | None = None) -> None:
    current_time = now if now is not None else time.monotonic()
    expired_keys = [
        search_id
        for search_id, entry in _DEMO_UNLOCK_CACHE.items()
        if entry.expires_at <= current_time
    ]
    for search_id in expired_keys:
        _DEMO_UNLOCK_CACHE.pop(search_id, None)

    while len(_DEMO_UNLOCK_CACHE) > _DEMO_UNLOCK_CACHE_MAX_SIZE:
        _DEMO_UNLOCK_CACHE.popitem(last=False)


def _cache_demo_unlock(search_id: str, full_results: list[DemoResult], savings: DemoSavings) -> None:
    now = time.monotonic()
    _prune_demo_unlock_cache(now)
    _DEMO_UNLOCK_CACHE[search_id] = DemoUnlockCacheEntry(
        full_results=full_results,
        savings=savings,
        expires_at=now + _DEMO_UNLOCK_CACHE_TTL_SECONDS,
    )
    _DEMO_UNLOCK_CACHE.move_to_end(search_id)
    while len(_DEMO_UNLOCK_CACHE) > _DEMO_UNLOCK_CACHE_MAX_SIZE:
        _DEMO_UNLOCK_CACHE.popitem(last=False)


def _get_cached_demo_unlock(search_id: str) -> tuple[list[DemoResult], DemoSavings] | None:
    now = time.monotonic()
    entry = _DEMO_UNLOCK_CACHE.get(search_id)
    if entry is None:
        _prune_demo_unlock_cache(now)
        return None
    if entry.expires_at <= now:
        _DEMO_UNLOCK_CACHE.pop(search_id, None)
        return None
    _DEMO_UNLOCK_CACHE.move_to_end(search_id)
    return entry.full_results, entry.savings


def _clean_query(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query.strip())
    return normalized or "iPhone 16 128GB in Rajkot"


def _infer_location(query: str, explicit_location: Optional[str] = None) -> str:
    if explicit_location and explicit_location.strip():
        return explicit_location.strip()

    match = re.search(r"\bin\s+([A-Za-z][A-Za-z\s.-]{1,50})$", query.strip(), re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return "India"


def _product_without_location(query: str) -> str:
    return re.sub(r"\s+in\s+[A-Za-z][A-Za-z\s.-]{1,50}$", "", query.strip(), flags=re.IGNORECASE).strip() or query


def _infer_category(product: str) -> DemoCategory:
    lowered = product.lower()
    if any(token in lowered for token in ("dolo", "paracetamol", "tablet", "capsule", "medicine", "pharmacy")):
        return "medical"
    if any(token in lowered for token in ("gold", "coin", "jewellery", "jewelry", "22k", "24k", "18k")):
        return "gold"
    return "electronics"


def _structured_category(category: DemoCategory) -> str:
    return "medicine" if category == "medical" else category


def _online_result_from_unified(result: UnifiedResult, index: int) -> DemoResult | None:
    if result.is_mock or result.price is None or result.price <= 0:
        return None
    return DemoResult(
        id=f"online-live-{index}",
        source_type="online",
        name=result.name,
        price=int(round(result.price)),
        delivery_time=result.delivery_time,
        availability=result.availability,
        url=result.url,
        notes=result.notes,
    )


def _dedupe_online_results(results: list[DemoResult]) -> list[DemoResult]:
    deduped: list[DemoResult] = []
    seen: set[str] = set()
    for result in results:
        key = result.url or f"{result.name}:{result.price}:{result.notes}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
    return deduped[:4]


async def _real_online_results(product: str, category: DemoCategory, location: str) -> list[DemoResult]:
    structured_query = StructuredQuery(
        product=product,
        category=_structured_category(category),
        location=location,
        intent="cheapest",
        urgency="immediate",
        raw_query=product if location == "India" else f"{product} in {location}",
    )
    unified_results = await online_pipeline.run(structured_query)
    online_results: list[DemoResult] = []
    for result in unified_results:
        demo_result = _online_result_from_unified(result, len(online_results) + 1)
        if demo_result:
            online_results.append(demo_result)
    return _dedupe_online_results(online_results)


def _offline_vendor_templates(category: DemoCategory) -> list[tuple[str, str, str]]:
    if category == "medical":
        return [
            ("City Care Pharmacy", "Near main market", "Confirmed available; bill provided."),
            ("Wellness Medicos", "Station road", "Confirmed available; pickup preferred."),
        ]
    if category == "gold":
        return [
            ("Raj Jewellers", "Jewellery market", "Final price depends on live rate and purity check."),
            ("Kalyan Gold House", "MG road", "Making charge negotiable."),
        ]
    return [
        ("Rajkot Digital Hub", "Yagnik road", "Confirmed sealed unit with GST bill."),
        ("Shree Mobile World", "Kalawad road", "Confirmed available; card payment accepted."),
        ("City Electronics", "Main market", "Available on request; pickup suggested."),
    ]


def _build_demo_offline_results(
    *,
    category: DemoCategory,
    area: str,
    best_online_price: int,
    forced_discount_percent: int | None = None,
) -> tuple[list[DemoResult], list[DemoResult], DemoSavings]:
    discount_percent = forced_discount_percent if forced_discount_percent is not None else random.randint(15, 23)
    discount_percent = min(23, max(15, discount_percent))
    best_offline_price = int(round(best_online_price * (100 - discount_percent) / 100))
    second_discount = max(12, discount_percent - random.randint(2, 5))
    third_discount = max(10, second_discount - random.randint(1, 3))
    prices = [
        best_offline_price,
        int(round(best_online_price * (100 - second_discount) / 100)),
        int(round(best_online_price * (100 - third_discount) / 100)),
    ]
    templates = _offline_vendor_templates(category)

    full_results = [
        DemoResult(
            id=f"offline-demo-{index + 1}",
            source_type="offline",
            name=name,
            price=prices[index],
            delivery_time="Today" if index < 2 else "Tomorrow",
            phone=f"+91 98765 43{index + 21:03d}",
            address=f"{street}, {area}",
            notes=notes,
        )
        for index, (name, street, notes) in enumerate(templates)
    ]
    locked_preview = [
        DemoResult(
            id=f"offline-preview-{index + 1}",
            source_type="offline",
            name="Verified local vendor quote",
            price=result.price,
            delivery_time=result.delivery_time,
            locked=True,
            notes="Store and phone unlock after payment",
        )
        for index, result in enumerate(full_results[:2])
    ]
    savings = DemoSavings(
        best_online_price=best_online_price,
        best_offline_price=best_offline_price,
        savings_amount=best_online_price - best_offline_price,
        savings_percent=discount_percent,
        basis="demo offline quote vs live online result",
    )
    return locked_preview, full_results, savings


async def _demo_dataset(query: str, location: Optional[str] = None) -> DemoSearchResponse:
    cleaned_query = _clean_query(query)
    product = _product_without_location(cleaned_query)
    area = _infer_location(cleaned_query, location)
    category = _infer_category(product)
    search_id = f"demo-{uuid.uuid4()}"
    online_results = await _real_online_results(product, category, area)

    if not online_results:
        raise HTTPException(
            status_code=424,
            detail="No live online results were found. Try a more specific product or check SerpAPI/live online configuration.",
        )

    best_online = min(result.price for result in online_results if result.price > 0)
    locked_preview, full_results, savings = _build_demo_offline_results(
        category=category,
        area=area,
        best_online_price=best_online,
    )
    _cache_demo_unlock(search_id, full_results, savings)

    return DemoSearchResponse(
        search_id=search_id,
        query=cleaned_query,
        product=product,
        location=area,
        category=category,
        online_results=online_results,
        locked_preview=locked_preview,
        savings=savings,
        amount_paise=settings.demo_paywall_amount_paise,
        full_results=full_results,
    )


async def _full_results_for(
    search_id: str,
    query: str,
    location: str | None = None,
    category: DemoCategory | None = None,
    best_online_price: int | None = None,
    savings_percent: int | None = None,
) -> tuple[list[DemoResult], DemoSavings]:
    cached = _get_cached_demo_unlock(search_id)
    if cached:
        return cached

    if best_online_price and best_online_price > 0:
        cleaned_query = _clean_query(query)
        product = _product_without_location(cleaned_query)
        area = _infer_location(cleaned_query, location)
        resolved_category = category or _infer_category(product)
        _, full_results, savings = _build_demo_offline_results(
            category=resolved_category,
            area=area,
            best_online_price=best_online_price,
            forced_discount_percent=savings_percent,
        )
        return full_results, savings

    dataset = await _demo_dataset(query)
    return dataset.full_results, dataset.savings


class DemoChatRequest(BaseModel):
    """User message for the conversational demo flow."""

    message: str = Field(min_length=1, max_length=300)
    session_id: Optional[str] = Field(default=None, max_length=80)
    location: Optional[str] = Field(default=None, max_length=80)


class DemoChatResponse(BaseModel):
    """Assistant reply in the conversational demo flow."""

    session_id: str
    assistant_message: str
    ready_to_search: bool = False
    suggested_replies: list[str] = Field(default_factory=list)


class DemoSearchFromSessionRequest(BaseModel):
    """Trigger demo search using the structured query from a chat session."""

    session_id: str = Field(min_length=1, max_length=80)


@router.post("/chat", response_model=DemoChatResponse)
async def demo_chat(request: Request, payload: DemoChatRequest) -> DemoChatResponse:
    """Multi-turn conversational intake for the demo flow.

    Reuses the same LLM query structuring, category validation, and product
    precision checks as the normal web chat — but marks the source as ``demo``
    so the search pipeline is not triggered (the demo runs its own pipeline
    via ``/search-from-session``).
    """
    metadata = request_context.extract_request_metadata(request)
    metadata["source"] = "demo"

    result = await chat_session.process_message(
        payload.message,
        payload.session_id,
        payload.location,
        request_metadata=metadata,
    )

    return DemoChatResponse(
        session_id=result.session_id,
        assistant_message=result.assistant_message,
        ready_to_search=result.ready_to_search,
        suggested_replies=result.suggested_replies or [],
    )


@router.post("/search-from-session", response_model=DemoSearchResponse)
async def demo_search_from_session(request: Request, payload: DemoSearchFromSessionRequest) -> DemoSearchResponse:
    """Run the demo search pipeline using the structured query from a completed
    chat session.

    Call this after ``/chat`` returns ``ready_to_search=true``.  It pulls the
    conversation state, builds a ``StructuredQuery``, runs the online pipeline
    for real prices, and generates demo offline results with the paywall.
    """
    device_id = request.headers.get("x-device-id", "anonymous")
    state = await chat_session.get_session_if_exists(payload.session_id)
    if not state or not state.product:
        raise HTTPException(
            status_code=400,
            detail="Session not found or has no product. Complete the conversation first.",
        )

    category = _infer_category(state.product)
    area = state.location if state.location and state.location != "unknown" else "India"

    online_results = await _real_online_results(state.product, category, area)
    if not online_results:
        raise HTTPException(
            status_code=424,
            detail="No live online results were found. Try a more specific product.",
        )

    positive_prices = [r.price for r in online_results if r.price > 0]
    if not positive_prices:
        raise HTTPException(
            status_code=424,
            detail="No live online results with a valid price were found. Try a more specific product.",
        )
    best_online = min(positive_prices)
    locked_preview, full_results, savings = _build_demo_offline_results(
        category=category,
        area=area,
        best_online_price=best_online,
    )

    search_id = f"demo-{uuid.uuid4()}"
    _cache_demo_unlock(search_id, full_results, savings)

    client = get_amplitude_client()
    if client:
        client.track(BaseEvent(
            event_type="Demo Search Completed",
            device_id=device_id,
            event_properties={
                "search_id": search_id,
                "product": state.product,
                "category": category,
                "location": area,
                "online_result_count": len(online_results),
                "best_online_price": best_online,
                "savings_percent": savings.savings_percent,
                "flow": "demo-premium",
            },
        ))

    return DemoSearchResponse(
        search_id=search_id,
        query=state.raw_query or state.product,
        product=state.product,
        location=area,
        category=category,
        online_results=online_results,
        locked_preview=locked_preview,
        savings=savings,
        amount_paise=settings.demo_paywall_amount_paise,
        full_results=full_results,
    )


@router.post("/search", response_model=DemoSearchResponse)
async def start_demo_search(payload: DemoSearchRequest) -> DemoSearchResponse:
    return await _demo_dataset(payload.query, payload.location)


@router.post("/create-order", response_model=DemoOrderResponse)
async def create_demo_order(request: Request, payload: DemoOrderRequest) -> DemoOrderResponse:
    amount = settings.demo_paywall_amount_paise
    device_id = request.headers.get("x-device-id", "anonymous")

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        order_id = f"demo_order_{uuid.uuid4().hex[:16]}"
        client = get_amplitude_client()
        if client:
            client.track(BaseEvent(
                event_type="Payment Order Created",
                device_id=device_id,
                event_properties={
                    "search_id": payload.search_id,
                    "amount_paise": amount,
                    "currency": "INR",
                    "demo_mode": True,
                    "flow": "demo-premium",
                },
            ))
        # Meta Conversions API: AddToCart
        await meta_conversions.send_event(
            "AddToCart",
            user_agent=request.headers.get("user-agent"),
            client_ip=request.client.host if request.client else None,
            external_id=device_id,
            currency="INR",
            value=amount / 100,
            content_name=payload.query,
            event_id=f"addtocart-{order_id}",
        )
        return DemoOrderResponse(
            order_id=order_id,
            razorpay_key_id="",
            amount=amount,
            demo_mode=True,
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                auth=(settings.razorpay_key_id, settings.razorpay_key_secret),
                json={
                    "amount": amount,
                    "currency": "INR",
                    "receipt": payload.search_id[:40],
                    "notes": {
                        "search_id": payload.search_id,
                        "query": payload.query[:120],
                        "flow": "demo-premium",
                    },
                },
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not create Razorpay order.") from exc

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Razorpay rejected the order request.")

    order = response.json()
    client = get_amplitude_client()
    if client:
        client.track(BaseEvent(
            event_type="Payment Order Created",
            device_id=device_id,
            event_properties={
                "search_id": payload.search_id,
                "amount_paise": amount,
                "currency": "INR",
                "demo_mode": False,
                "razorpay_order_id": order["id"],
                "flow": "demo-premium",
            },
        ))
    # Meta Conversions API: AddToCart
    await meta_conversions.send_event(
        "AddToCart",
        user_agent=request.headers.get("user-agent"),
        client_ip=request.client.host if request.client else None,
        external_id=device_id,
        currency="INR",
        value=amount / 100,
        content_name=payload.query,
        event_id=f"addtocart-{order['id']}",
    )
    return DemoOrderResponse(
        order_id=order["id"],
        razorpay_key_id=settings.razorpay_key_id,
        amount=amount,
    )


@router.post("/verify-payment", response_model=DemoUnlockResponse)
async def verify_demo_payment(request: Request, payload: DemoVerifyPaymentRequest) -> DemoUnlockResponse:
    payment_verified = False
    device_id = request.headers.get("x-device-id", "anonymous")

    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        payment_verified = payload.demo_mode
    else:
        if payload.demo_mode:
            raise HTTPException(status_code=400, detail="Demo unlock is disabled when Razorpay is configured.")
        if not payload.razorpay_order_id or not payload.razorpay_payment_id or not payload.razorpay_signature:
            raise HTTPException(status_code=400, detail="Missing Razorpay payment verification fields.")

        signed_payload = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode("utf-8")
        expected = hmac.new(
            settings.razorpay_key_secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        payment_verified = hmac.compare_digest(expected, payload.razorpay_signature)

    if not payment_verified:
        client = get_amplitude_client()
        if client:
            client.track(BaseEvent(
                event_type="Payment Verification Failed",
                device_id=device_id,
                event_properties={
                    "search_id": payload.search_id,
                    "razorpay_order_id": payload.razorpay_order_id,
                    "demo_mode": payload.demo_mode,
                    "flow": "demo-premium",
                },
            ))
        raise HTTPException(status_code=402, detail="Payment could not be verified.")

    full_results, savings = await _full_results_for(
        payload.search_id,
        payload.query,
        location=payload.location,
        category=payload.category,
        best_online_price=payload.best_online_price,
        savings_percent=payload.savings_percent,
    )

    client = get_amplitude_client()
    if client:
        client.track(BaseEvent(
            event_type="Payment Verified",
            device_id=device_id,
            event_properties={
                "search_id": payload.search_id,
                "amount_paise": settings.demo_paywall_amount_paise,
                "currency": "INR",
                "razorpay_order_id": payload.razorpay_order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "demo_mode": payload.demo_mode,
                "result_count": len(full_results),
                "savings_percent": savings.savings_percent if savings else None,
                "flow": "demo-premium",
            },
        ))

    # Meta Conversions API: Purchase
    await meta_conversions.send_event(
        "Purchase",
        user_agent=request.headers.get("user-agent"),
        client_ip=request.client.host if request.client else None,
        external_id=device_id,
        currency="INR",
        value=settings.demo_paywall_amount_paise / 100,
        content_name=payload.query,
        event_id=f"purchase-{payload.razorpay_payment_id or payload.search_id}",
    )

    return DemoUnlockResponse(
        search_id=payload.search_id,
        full_results=full_results,
        savings=savings,
        payment_verified=True,
    )
