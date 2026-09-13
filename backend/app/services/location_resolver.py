from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.models.schemas import LocationSuggestion

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACE_AUTOCOMPLETE_URL = "https://maps.googleapis.com/maps/api/place/autocomplete/json"


def _pick_location_label(result: dict) -> str:
    components = result.get("address_components", [])
    preferred_types = (
        "sublocality_level_1",
        "sublocality",
        "locality",
        "administrative_area_level_2",
        "administrative_area_level_1",
    )

    for preferred_type in preferred_types:
        for component in components:
            if preferred_type in component.get("types", []):
                return component.get("long_name", "")

    return result.get("formatted_address", "")


async def resolve_location_from_coordinates(latitude: float, longitude: float) -> tuple[str, str | None]:
    if not settings.google_places_api_key:
        raise ValueError("Google Places API key is not configured.")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            GEOCODE_URL,
            params={
                "latlng": f"{latitude},{longitude}",
                "key": settings.google_places_api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

    results = payload.get("results") or []
    if not results:
        status = payload.get("status", "UNKNOWN")
        error_message = payload.get("error_message")
        detail = f"status={status}"
        if error_message:
            detail = f"{detail}, error={error_message}"
        logger.warning("Reverse geocoding returned no results (%s)", detail)
        raise ValueError("Could not determine your current location.")

    top_result = results[0]
    location_label = _pick_location_label(top_result)
    if not location_label:
        raise ValueError("Could not determine your current location.")

    return location_label, top_result.get("formatted_address")


async def suggest_locations(query: str) -> list[LocationSuggestion]:
    cleaned_query = query.strip()
    if len(cleaned_query) < 2 or not settings.google_places_api_key:
        return []

    async with httpx.AsyncClient() as client:
        response = await client.get(
            PLACE_AUTOCOMPLETE_URL,
            params={
                "input": cleaned_query,
                "types": "geocode",
                "components": "country:in",
                "language": "en",
                "key": settings.google_places_api_key,
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

    predictions = payload.get("predictions") or []
    suggestions: list[LocationSuggestion] = []
    for prediction in predictions[:5]:
        description = (prediction.get("description") or "").strip()
        place_id = (prediction.get("place_id") or "").strip()
        if not description or not place_id:
            continue
        suggestions.append(
            LocationSuggestion(
                place_id=place_id,
                description=description,
            )
        )
    return suggestions
