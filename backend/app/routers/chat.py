from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile

from amplitude import BaseEvent

from app.amplitude import get_amplitude_client
from app.models.schemas import (
    ChatHistoryMessageCreate,
    ChatMessageRequest,
    ChatMessageResponse,
    GoldLiveRatePreviewRequest,
    GoldLiveRatePreviewResponse,
    ProductPreviewRequest,
    ProductPreviewResponse,
    ChatSessionDetail,
    ChatSessionSummary,
    SearchMoreResultsResponse,
    SearchProgressSnapshot,
)
from app.services import chat_session, display_currency, gold_live_rate_preview, gold_requests, product_preview, request_context, search_progress

router = APIRouter()


@router.post("/api/chat/message", response_model=ChatMessageResponse)
async def chat_message(request: Request, payload: ChatMessageRequest) -> ChatMessageResponse:
    metadata = request_context.extract_request_metadata(request)
    result = await chat_session.process_message(
        payload.message,
        payload.session_id,
        payload.location,
        request_metadata=metadata,
        forced_category=payload.category,
    )

    # Amplitude: track when chat session has gathered all info and launches a search
    if result.ready_to_search:
        client = get_amplitude_client()
        if client:
            device_id = (metadata or {}).get("device_id") or "anonymous"
            state = result.state
            client.track(BaseEvent(
                event_type="Chat Search Triggered",
                device_id=device_id,
                event_properties={
                    "session_id": result.session_id,
                    "product": state.product,
                    "category": state.category,
                    "location": state.location,
                    "urgency": state.urgency,
                    "intent": state.intent,
                    "search_strategy": state.search_strategy,
                    "source": (metadata or {}).get("source", "web"),
                },
            ))

    return await display_currency.apply_display_currency_to_chat_response(
        result,
        request.headers.get("X-Display-Currency"),
    )


@router.post("/api/chat/image-intake", response_model=ChatMessageResponse)
async def chat_image_intake(
    request: Request,
    image: UploadFile = File(...),
    message: str = Form(default=""),
    session_id: str | None = Form(default=None),
    location: str | None = Form(default=None),
) -> ChatMessageResponse:
    metadata = request_context.extract_request_metadata(request)
    try:
        image_bytes = await image.read()
        result = await chat_session.process_image_intake(
            image_bytes,
            content_type=image.content_type,
            user_note=message,
            session_id=session_id,
            location=location,
            request_metadata=metadata,
        )
        return await display_currency.apply_display_currency_to_chat_response(
            result,
            request.headers.get("X-Display-Currency"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        await image.close()


@router.get("/api/chat/search/{search_id}", response_model=SearchProgressSnapshot)
async def chat_search_status(request: Request, search_id: str) -> SearchProgressSnapshot:
    snapshot = await search_progress.resolve_snapshot(search_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Search session not found.")
    return await display_currency.apply_display_currency_to_snapshot(
        snapshot,
        request.headers.get("X-Display-Currency"),
    )


@router.post("/api/chat/search/{search_id}/more-results", response_model=SearchMoreResultsResponse)
async def chat_search_more_results(request: Request, search_id: str, offset: int | None = None) -> SearchMoreResultsResponse:
    response = await search_progress.get_more_results(search_id, offset=offset)
    if response is None:
        raise HTTPException(status_code=404, detail="Search session not found.")
    response.results = await display_currency.apply_display_currency_to_results(
        response.results,
        request.headers.get("X-Display-Currency"),
    )
    return response


@router.post("/api/chat/search/{search_id}/expand-other-cities", response_model=SearchProgressSnapshot)
async def chat_search_expand_other_cities(
    request: Request,
    search_id: str,
    accept: bool = True,
) -> SearchProgressSnapshot:
    snapshot = await search_progress.respond_other_city_expand(search_id, accept=accept)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Search session not found.")
    return await display_currency.apply_display_currency_to_snapshot(
        snapshot,
        request.headers.get("X-Display-Currency"),
    )


@router.post("/api/chat/search/{search_id}/expand-brand-fallback", response_model=SearchProgressSnapshot)
async def chat_search_expand_brand_fallback(
    request: Request,
    search_id: str,
    accept: bool = True,
) -> SearchProgressSnapshot:
    snapshot = await search_progress.respond_brand_fallback_expand(search_id, accept=accept)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Search session not found.")
    return await display_currency.apply_display_currency_to_snapshot(
        snapshot,
        request.headers.get("X-Display-Currency"),
    )


@router.post("/api/product-preview", response_model=ProductPreviewResponse)
async def product_preview_details(payload: ProductPreviewRequest) -> ProductPreviewResponse:
    try:
        return await product_preview.fetch_product_preview(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not load product details.") from exc


@router.post("/api/gold-live-rate-preview", response_model=GoldLiveRatePreviewResponse)
async def gold_live_rate_preview_details(payload: GoldLiveRatePreviewRequest) -> GoldLiveRatePreviewResponse:
    try:
        return await gold_live_rate_preview.fetch_gold_live_rate_preview(
            vendor_id=payload.vendor_id,
            source_url=payload.source_url,
            city=payload.city,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not load live-rate details.") from exc


@router.get("/api/chat/sessions", response_model=list[ChatSessionSummary])
async def list_chat_sessions(request: Request) -> list[ChatSessionSummary]:
    metadata = request_context.extract_request_metadata(request)
    return await chat_session.list_sessions(metadata.get("device_id"))


@router.get("/api/chat/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_chat_session(session_id: str) -> ChatSessionDetail:
    detail = await chat_session.get_session_detail(session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Chat session not found.")
    return detail


@router.post("/api/chat/sessions/{session_id}/messages", status_code=204)
async def persist_chat_history_message(session_id: str, payload: ChatHistoryMessageCreate) -> Response:
    await chat_session.persist_history_message(session_id, payload)
    return Response(status_code=204)


@router.get("/api/gold-requests/session/{session_id}/responses")
async def gold_request_responses(session_id: str) -> dict:
    """Supplier quotes posted (by ops on the dashboard) for this session's gold
    request(s). Polled by the gold-locked app to surface quotes back to the user."""
    responses = await gold_requests.list_responses_for_session(session_id)
    return {"responses": responses}
