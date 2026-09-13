from __future__ import annotations

import asyncio
import logging
import re

import httpx

from app.config import settings
from app.categories.router import resolve_query_route
from app.categories.gold.selection import select_gold_bullion_vendors
from app.models.schemas import VendorInfo
from app.models.schemas import StructuredQuery
from app.services.product_registry import classify_product_type, get_places_queries

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


CATEGORY_CONTEXT_TERMS: dict[str, tuple[str, ...]] = {
    "groceries": ("grocery", "supermarket", "mart", "vegetable", "fruit", "dairy", "provision"),
    "electronics": ("electronics", "mobile", "phone", "accessories", "appliance", "digital", "gadget"),
    "clothing": ("clothing", "fashion", "garment", "boutique", "apparel", "wear"),
    "medicine": ("pharmacy", "medical", "chemist", "drug", "hospital"),
    "gold": ("jeweller", "jeweler", "jewellery", "jewelry", "gold", "bullion", "ornament"),
    "hardware": ("hardware", "tool", "electrical", "plumbing", "paint", "sanitary"),
    "services": ("service", "repair", "cleaning", "salon", "clinic", "plumber", "electrician"),
}


def _extract_product_terms(product: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", product.lower())
    stop_words = {"best", "cheap", "cheapest", "for", "near", "me", "in", "the", "and"}
    return [token for token in tokens if len(token) > 2 and token not in stop_words][:4]


def _queries_for_category(product: str, category: str, location: str) -> list[str]:
    spec = classify_product_type(product, category)
    return get_places_queries(spec, product, location)


def _candidate_score(vendor: VendorInfo, product: str, category: str) -> tuple[float, float, str]:
    haystack = f"{vendor.name} {vendor.address}".lower()
    product_terms = _extract_product_terms(product)
    category_terms = CATEGORY_CONTEXT_TERMS.get(category, ())
    product_hits = sum(1 for term in product_terms if term in haystack)
    category_hits = sum(1 for term in category_terms if term in haystack)
    rating = vendor.rating or 0.0
    review_count = vendor.user_rating_count or 0
    return (product_hits * 3 + category_hits * 1.5, rating, review_count, vendor.name)


def _mock_vendors(product: str, category: str, location: str) -> list[VendorInfo]:
    city = location if location and location != "unknown" else "Rajkot"
    if category == "services":
        seeds = [
            ("Urban Assist Services", "+919825001245", f"Yagnik Road, {city}", 4.6),
            ("SwiftFix Professionals", "+919723450981", f"Kalawad Road, {city}", 4.4),
            ("CarePoint Home Services", "+919998112233", f"University Road, {city}", 4.5),
            ("Om Sai Service Hub", "+919879901122", f"Race Course, {city}", 4.3),
            ("Mahavir Local Experts", "+918866554433", f"Amin Marg, {city}", 4.2),
        ]
    else:
        seeds = [
            ("Shree Krishna Traders", "+919825001245", f"Yagnik Road, {city}", 4.6),
            ("Patel Brothers Market", "+919723450981", f"Kalawad Road, {city}", 4.4),
            ("Navkar Sales", "+919998112233", f"University Road, {city}", 4.5),
            ("Om Sai Retail", "+919879901122", f"Race Course, {city}", 4.3),
            ("Mahavir Storefront", "+918866554433", f"Amin Marg, {city}", 4.2),
        ]
    return [
        VendorInfo(
            name=name,
            phone=phone,
            address=address,
            rating=rating,
            user_rating_count=120 - index * 10,
            location={"lat": 22.3039, "lng": 70.8022},
            place_id=f"mock-{index}",
            is_mock=True,
        )
        for index, (name, phone, address, rating) in enumerate(seeds, start=1)
    ]


async def _geocode_location(client: httpx.AsyncClient, location: str) -> tuple[float, float] | None:
    response = await client.get(
        GEOCODE_URL,
        params={"address": location, "key": settings.google_places_api_key},
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
        logger.warning("Geocoding returned no results for '%s' (%s)", location, detail)
        return None

    coordinates = results[0].get("geometry", {}).get("location")
    if not coordinates:
        logger.warning("Geocoding returned a result without coordinates for '%s'", location)
        return None

    return coordinates["lat"], coordinates["lng"]


async def _fetch_place_phone(client: httpx.AsyncClient, place_id: str) -> str | None:
    response = await client.get(
        PLACE_DETAILS_URL.format(place_id=place_id),
        headers={
            "X-Goog-Api-Key": settings.google_places_api_key,
            "X-Goog-FieldMask": "nationalPhoneNumber,internationalPhoneNumber",
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("nationalPhoneNumber") or payload.get("internationalPhoneNumber")


async def _place_to_vendor(client: httpx.AsyncClient, place: dict) -> VendorInfo | None:
    place_id = place.get("id")
    if not place_id:
        return None

    phone = await _fetch_place_phone(client, place_id)
    if not phone:
        logger.info(
            "Skipping place without phone number: %s",
            place.get("displayName", {}).get("text", place_id),
        )
        return None

    return VendorInfo(
        name=place.get("displayName", {}).get("text", "Unknown Vendor"),
        phone=phone,
        address=place.get("formattedAddress", "Address unavailable"),
        location={
            "lat": place.get("location", {}).get("latitude"),
            "lng": place.get("location", {}).get("longitude"),
        },
        place_id=place_id,
        rating=place.get("rating"),
        user_rating_count=place.get("userRatingCount"),
        is_mock=False,
    )


async def discover_vendors(product: str, category: str, location: str) -> list[VendorInfo]:
    logger.info("Starting vendor discovery for %s in %s", product, location)
    structured_query = StructuredQuery(
        product=product,
        category=category,
        location=location,
        intent="cheapest",
        urgency="immediate",
        raw_query=f"{product} in {location}",
    )
    query_route = await resolve_query_route(structured_query)
    if query_route.handler_key == "gold.bullion":
        db_vendors = await select_gold_bullion_vendors(structured_query)
    else:
        db_vendors = []
    if db_vendors:
        logger.info("Using %s DB-backed gold bullion vendors for %s", len(db_vendors), product)
        return db_vendors

    if not settings.google_places_api_key:
        logger.info("Google Places API key missing; skipping external vendor discovery.")
        return []

    try:
        async with httpx.AsyncClient() as client:
            coordinates = await _geocode_location(client, location)
            search_queries = _queries_for_category(product, category, location)

            candidates: list[VendorInfo] = []
            for query in search_queries:
                request_body = {
                    "textQuery": query,
                    "maxResultCount": 10,
                }
                if coordinates:
                    lat, lng = coordinates
                    request_body["locationBias"] = {
                        "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 5000.0}
                    }

                response = await client.post(
                    PLACES_SEARCH_URL,
                    headers={
                        "X-Goog-Api-Key": settings.google_places_api_key,
                        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.rating,places.userRatingCount",
                    },
                    json=request_body,
                    timeout=25,
                )
                response.raise_for_status()
                payload = response.json()
                places = payload.get("places", [])
                logger.info(
                    "Google Places returned %s place candidates for query='%s'",
                    len(places),
                    query,
                )
                resolved = await asyncio.gather(
                    *[_place_to_vendor(client, place) for place in places],
                    return_exceptions=True,
                )
                query_vendors = 0
                for item in resolved:
                    if isinstance(item, Exception):
                        logger.warning("Failed to fetch phone details for a place: %s", item)
                        continue
                    if item is not None:
                        candidates.append(item)
                        query_vendors += 1
                logger.info(
                    "Google Places kept %s callable vendors for query='%s'",
                    query_vendors,
                    query,
                )

            unique: dict[str, VendorInfo] = {}
            for vendor in candidates:
                unique[vendor.place_id or vendor.phone] = vendor
                unique[vendor.phone] = vendor
            deduped_vendors = list({vendor.place_id or vendor.phone: vendor for vendor in unique.values()}.values())
            relevant_vendors = [
                vendor
                for vendor in deduped_vendors
                if _candidate_score(vendor, product, category)[0] > 0
            ]
            pool = relevant_vendors or deduped_vendors
            vendors = sorted(
                pool,
                key=lambda item: (
                    item.rating or 0.0,
                    item.user_rating_count or 0,
                    _candidate_score(item, product, category)[0],
                    item.name,
                ),
                reverse=True,
            )[:10]
            if not vendors:
                raise ValueError(
                    "No callable vendors returned by Google Places. Places may be missing phone numbers."
                )
            logger.info(
                "Vendor discovery completed with %s vendors, sorted by rating and review count",
                len(vendors),
            )
            return vendors
    except Exception as exc:  # pragma: no cover - external integration
        logger.warning("Vendor discovery failed; skipping mock vendor fallback: %s", exc)
        return []
