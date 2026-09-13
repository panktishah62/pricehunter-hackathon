from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.schemas import StructuredQuery, UnifiedResult


STATE_TO_REGION = {
    "andhra pradesh": "south",
    "karnataka": "south",
    "kerala": "south",
    "puducherry": "south",
    "tamil nadu": "south",
    "telangana": "south",
    "goa": "west",
    "gujarat": "west",
    "maharashtra": "west",
    "chandigarh": "north",
    "delhi": "north",
    "haryana": "north",
    "himachal pradesh": "north",
    "jammu and kashmir": "north",
    "ladakh": "north",
    "punjab": "north",
    "rajasthan": "north",
    "uttar pradesh": "north",
    "uttarakhand": "north",
    "bihar": "east",
    "jharkhand": "east",
    "odisha": "east",
    "west bengal": "east",
    "chhattisgarh": "central",
    "madhya pradesh": "central",
    "arunachal pradesh": "north_east",
    "assam": "north_east",
    "manipur": "north_east",
    "meghalaya": "north_east",
    "mizoram": "north_east",
    "nagaland": "north_east",
    "sikkim": "north_east",
    "tripura": "north_east",
}

CITY_TO_STATE = {
    "agra": "uttar pradesh",
    "ahmedabad": "gujarat",
    "aurangabad": "maharashtra",
    "bangalore": "karnataka",
    "bengaluru": "karnataka",
    "bhopal": "madhya pradesh",
    "bhubaneswar": "odisha",
    "chandigarh": "chandigarh",
    "chennai": "tamil nadu",
    "coimbatore": "tamil nadu",
    "delhi": "delhi",
    "faridabad": "haryana",
    "ghaziabad": "uttar pradesh",
    "gurgaon": "haryana",
    "gurugram": "haryana",
    "hyderabad": "telangana",
    "indore": "madhya pradesh",
    "jaipur": "rajasthan",
    "jodhpur": "rajasthan",
    "kanpur": "uttar pradesh",
    "kolkata": "west bengal",
    "lucknow": "uttar pradesh",
    "ludhiana": "punjab",
    "madurai": "tamil nadu",
    "mumbai": "maharashtra",
    "mysore": "karnataka",
    "mysuru": "karnataka",
    "nagpur": "maharashtra",
    "nashik": "maharashtra",
    "noida": "uttar pradesh",
    "pune": "maharashtra",
    "rajkot": "gujarat",
    "raipur": "chhattisgarh",
    "surat": "gujarat",
    "thane": "maharashtra",
    "thrissur": "kerala",
    "vadodara": "gujarat",
    "vapi": "gujarat",
    "visakhapatnam": "andhra pradesh",
}

LOCALITY_TO_CITY = {
    "andheri": "mumbai",
    "bandra": "mumbai",
    "bhandup": "mumbai",
    "borivali": "mumbai",
    "hsr": "bengaluru",
    "hsr layout": "bengaluru",
    "jayanagar": "bengaluru",
    "koramangala": "bengaluru",
    "marathahalli": "bengaluru",
    "munnekolala": "bengaluru",
    "peenya": "bengaluru",
    "whitefield": "bengaluru",
    "zaveri bazaar": "mumbai",
}

CITY_ALIASES = {
    "banglore": "bengaluru",
    "bangluru": "bengaluru",
    "bengaluru": "bengaluru",
    "bangalore": "bengaluru",
    "bombay": "mumbai",
    "calcutta": "kolkata",
    "new delhi": "delhi",
    "gurgaon": "gurugram",
}

# Cities treated as one local market for same-city DB matching.
METRO_CITY_GROUPS: dict[str, set[str]] = {
    "delhi": {"delhi", "new delhi", "gurugram", "gurgaon", "noida", "faridabad", "ghaziabad"},
}

LOCATION_BUCKET_PRIORITY = {
    "same_city": 0,
    "same_state": 1,
    "same_region": 2,
    "pan_india": 3,
    "other_region": 4,
    "unknown": 5,
}


@dataclass(frozen=True)
class NormalizedLocation:
    city: str | None = None
    state: str | None = None
    region: str | None = None
    pan_india: bool = False


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _canonical_city(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if not normalized:
        return None
    return CITY_ALIASES.get(normalized, normalized)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return bool(re.search(rf"(^|\s){re.escape(phrase)}(\s|$)", haystack))


def normalize_location(*parts: Any) -> NormalizedLocation:
    text = _normalize_text(" ".join(str(part or "") for part in parts if part))
    if not text:
        return NormalizedLocation()

    pan_india = any(token in text for token in ("pan india", "all india", "across india", "india wide", "nationwide"))

    city: str | None = None
    for locality, locality_city in LOCALITY_TO_CITY.items():
        if _contains_phrase(text, locality):
            city = locality_city
            break
    if city is None:
        for candidate in sorted({*CITY_TO_STATE.keys(), *CITY_ALIASES.keys()}, key=len, reverse=True):
            if _contains_phrase(text, candidate):
                city = _canonical_city(candidate)
                break

    state: str | None = CITY_TO_STATE.get(city or "")
    for candidate_state in sorted(STATE_TO_REGION.keys(), key=len, reverse=True):
        if _contains_phrase(text, candidate_state):
            state = candidate_state
            break

    region = STATE_TO_REGION.get(state or "")
    return NormalizedLocation(city=city, state=state, region=region, pan_india=pan_india)


def buyer_location(query: StructuredQuery) -> NormalizedLocation:
    return normalize_location(query.location, query.raw_query)


def result_location(result: UnifiedResult) -> NormalizedLocation:
    preview = (result.attributes or {}).get("product_preview") if result.attributes else {}
    preview = preview if isinstance(preview, dict) else {}
    return normalize_location(
        preview.get("supplier_city"),
        preview.get("supplier_state"),
        preview.get("supplier_location"),
        preview.get("supplier_address"),
        result.city,
        result.address,
        result.notes,
    )


def _metro_group(city: str | None) -> str | None:
    canonical = _canonical_city(city)
    if not canonical:
        return None
    for group_name, members in METRO_CITY_GROUPS.items():
        normalized_members = {_canonical_city(member) or member for member in members}
        if canonical in normalized_members:
            return group_name
    return None


def cities_in_same_market(buyer_city: str | None, supplier_city: str | None) -> bool:
    """True when cities match exactly, via alias, or via metro group (e.g. Delhi NCR)."""
    left = _canonical_city(buyer_city)
    right = _canonical_city(supplier_city)
    if not left or not right:
        return False
    if left == right:
        return True
    left_metro = _metro_group(left)
    right_metro = _metro_group(right)
    return bool(left_metro and right_metro and left_metro == right_metro)


def is_same_city_result(query: StructuredQuery, result: UnifiedResult) -> bool:
    buyer = buyer_location(query)
    supplier = result_location(result)
    if supplier.pan_india:
        return False
    return cities_in_same_market(buyer.city, supplier.city)


def location_bucket(query: StructuredQuery, result: UnifiedResult) -> str:
    buyer = buyer_location(query)
    supplier = result_location(result)
    if supplier.pan_india:
        return "pan_india"
    if cities_in_same_market(buyer.city, supplier.city):
        return "same_city"
    if buyer.state and supplier.state and buyer.state == supplier.state:
        return "same_state"
    if buyer.region and supplier.region and buyer.region == supplier.region:
        return "same_region"
    if buyer.region and supplier.region:
        return "other_region"
    return "unknown"


def rank_results_by_location(query: StructuredQuery, results: list[UnifiedResult]) -> list[UnifiedResult]:
    return sorted(
        results,
        key=lambda result: LOCATION_BUCKET_PRIORITY.get(location_bucket(query, result), 99),
    )


def split_results_by_city_market(
    query: StructuredQuery,
    results: list[UnifiedResult],
) -> tuple[list[UnifiedResult], list[UnifiedResult]]:
    same_city: list[UnifiedResult] = []
    other_city: list[UnifiedResult] = []
    for result in results:
        if is_same_city_result(query, result):
            same_city.append(result)
        else:
            other_city.append(result)
    return same_city, other_city


def top_other_cities(
    other_city_results: list[UnifiedResult],
    *,
    limit: int = 3,
) -> list[str]:
    counts: dict[str, int] = {}
    labels: dict[str, str] = {}
    for result in other_city_results:
        supplier = result_location(result)
        if not supplier.city:
            continue
        key = _canonical_city(supplier.city) or supplier.city
        counts[key] = counts.get(key, 0) + 1
        raw = supplier.city
        labels.setdefault(key, raw.title() if raw == raw.lower() else raw)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [labels[key] for key, _count in ranked[: max(0, limit)]]


def build_other_city_prompt_message(cities: list[str], *, other_count: int | None = None) -> str:
    # other_count kept for callers / telemetry; omit from user-facing copy so we
    # don't advertise huge dumps (e.g. "249 listings").
    _ = other_count
    if not cities:
        city_phrase = "other cities"
    elif len(cities) == 1:
        city_phrase = cities[0]
    elif len(cities) == 2:
        city_phrase = f"{cities[0]} and {cities[1]}"
    else:
        city_phrase = f"{', '.join(cities[:-1])}, and {cities[-1]}"
    return (
        f"We also have suppliers from other cities who supply this product "
        f"(including {city_phrase}). "
        f"Would you like to see a few of those quotes?"
    )

