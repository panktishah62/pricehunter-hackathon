from __future__ import annotations

import httpx

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import LocationSuggestionsResponse, ResolveLocationRequest, ResolveLocationResponse
from app.services.location_resolver import resolve_location_from_coordinates, suggest_locations

router = APIRouter()


@router.post("/api/location/resolve", response_model=ResolveLocationResponse)
async def resolve_location(request: ResolveLocationRequest) -> ResolveLocationResponse:
    try:
        location, formatted_address = await resolve_location_from_coordinates(
            request.latitude,
            request.longitude,
        )
        return ResolveLocationResponse(
            location=location,
            formatted_address=formatted_address,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/location/suggest", response_model=LocationSuggestionsResponse)
async def location_suggestions(q: str = Query("", min_length=0, max_length=120)) -> LocationSuggestionsResponse:
    try:
        suggestions = await suggest_locations(q)
        return LocationSuggestionsResponse(suggestions=suggestions)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Location suggestions are temporarily unavailable.") from exc
