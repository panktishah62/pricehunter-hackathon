from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import settings
from app.categories.gold.ingestion import city_from_address

logger = logging.getLogger(__name__)

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"

CHICKPET_CLUSTER = {
    "cluster_id": "bangalore_chickpet_t1",
    "cluster_name": "Chickpet",
    "city": "Bangalore",
    "lat": 12.9716,
    "lng": 77.5795,
    "radius_meters": 1000.0,
}

CHICKPET_SEARCH_TERMS = [
    "gold bullion dealer",
    "bullion trader",
    "gold bar dealer",
    "gold wholesale",
    "24K gold dealer",
]


async def _search_places_single_query(client: httpx.AsyncClient, query: str) -> list[dict[str, Any]]:
    request_body = {
        "textQuery": query,
        "maxResultCount": 20,
        "locationBias": {
            "circle": {
                "center": {
                    "latitude": CHICKPET_CLUSTER["lat"],
                    "longitude": CHICKPET_CLUSTER["lng"],
                },
                "radius": CHICKPET_CLUSTER["radius_meters"],
            }
        },
    }
    response = await client.post(
        PLACES_SEARCH_URL,
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": (
                "places.id,places.displayName,places.formattedAddress,places.location,"
                "places.rating,places.userRatingCount,places.types"
            ),
        },
        json=request_body,
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("places", [])


async def _fetch_place_details(client: httpx.AsyncClient, place_id: str) -> dict[str, Any]:
    response = await client.get(
        PLACE_DETAILS_URL.format(place_id=place_id),
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": (
                "nationalPhoneNumber,internationalPhoneNumber,currentOpeningHours,"
                "regularOpeningHours,websiteUri"
            ),
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _opening_hours_list(details: dict[str, Any]) -> list[str]:
    opening = details.get("currentOpeningHours") or details.get("regularOpeningHours") or {}
    return opening.get("weekdayDescriptions") or []


async def scrape_chickpet_google_places() -> list[dict[str, Any]]:
    if not settings.google_places_api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is required for Chickpet Google Places scraping.")

    async with httpx.AsyncClient() as client:
        query_results = await asyncio.gather(
            *[_search_places_single_query(client, query) for query in CHICKPET_SEARCH_TERMS],
            return_exceptions=True,
        )

        deduped_places: dict[str, dict[str, Any]] = {}
        place_query_map: dict[str, list[str]] = {}
        for query, result in zip(CHICKPET_SEARCH_TERMS, query_results, strict=False):
            if isinstance(result, Exception):
                logger.warning("Google Places query failed for '%s': %s", query, result)
                continue
            for place in result:
                place_id = place.get("id")
                if not place_id:
                    continue
                deduped_places[place_id] = place
                place_query_map.setdefault(place_id, []).append(query)

        details_results = await asyncio.gather(
            *[_fetch_place_details(client, place_id) for place_id in deduped_places],
            return_exceptions=True,
        )

    vendors: list[dict[str, Any]] = []
    for place_id, details in zip(deduped_places.keys(), details_results, strict=False):
        if isinstance(details, Exception):
            logger.warning("Google Places details fetch failed for %s: %s", place_id, details)
            continue

        place = deduped_places[place_id]
        phone_number = details.get("nationalPhoneNumber") or details.get("internationalPhoneNumber")
        if not phone_number:
            continue

        address = place.get("formattedAddress", "")
        location = place.get("location") or {}
        vendors.append(
            {
                "name": place.get("displayName", {}).get("text", "Unknown"),
                "phone_number": phone_number,
                "address": address,
                "city": city_from_address(address) or CHICKPET_CLUSTER["city"],
                "lat": location.get("latitude"),
                "lng": location.get("longitude"),
                "rating": place.get("rating"),
                "review_count": place.get("userRatingCount"),
                "types": place.get("types") or [],
                "opening_hours": _opening_hours_list(details),
                "google_place_id": place_id,
                "maps_url": f"https://maps.google.com/?cid={place_id}",
                "source_queries": place_query_map.get(place_id, []),
            }
        )
    logger.info("Google Places Chickpet scraper parsed %s vendor(s)", len(vendors))
    return vendors
