from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi import HTTPException

from amplitude import BaseEvent

from app.amplitude import get_amplitude_client
from app.models.schemas import SearchRequest, SearchResponse
from app.services import display_currency, orchestrator, request_context

router = APIRouter()


@router.post("/api/search", response_model=SearchResponse)
async def search(request: Request, payload: SearchRequest) -> SearchResponse:
    try:
        metadata = request_context.extract_request_metadata(request)
        result = await orchestrator.run_search(payload.query, payload.location, request_metadata=metadata)

        # Amplitude: track search completed
        client = get_amplitude_client()
        if client:
            device_id = (metadata or {}).get("device_id") or "anonymous"
            client.track(BaseEvent(
                event_type="Search Completed",
                device_id=device_id,
                event_properties={
                    "query": payload.query,
                    "location": payload.location or result.query.location,
                    "category": result.query.category,
                    "search_strategy": result.search_strategy,
                    "online_count": result.online_count,
                    "offline_count": result.offline_count,
                    "total_results": len(result.results),
                    "duration_seconds": result.total_time_seconds,
                },
            ))

        return await display_currency.apply_display_currency_to_search_response(
            result,
            request.headers.get("X-Display-Currency"),
        )
    except orchestrator.UnsupportedCategoryError as exc:
        # Amplitude: track unsupported category error
        client = get_amplitude_client()
        if client:
            metadata = request_context.extract_request_metadata(request)
            device_id = (metadata or {}).get("device_id") or "anonymous"
            client.track(BaseEvent(
                event_type="Error Encountered",
                device_id=device_id,
                event_properties={
                    "error_category": "unsupported_category",
                    "error_message": str(exc),
                    "error_context": "direct_search",
                    "query": payload.query,
                },
            ))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
