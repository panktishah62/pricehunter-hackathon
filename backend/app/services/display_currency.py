from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.models.schemas import ChatMessageResponse, SearchProgressSnapshot, SearchResponse, UnifiedResult

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
RESTCOUNTRIES_ALPHA_URL = "https://restcountries.com/v3.1/alpha/{country_code}"
OPEN_EXCHANGE_URL = "https://open.er-api.com/v6/latest/{base_currency}"

_LOCATION_CACHE_TTL_SECONDS = 60 * 60 * 24 * 7
_FX_CACHE_TTL_SECONDS = 60 * 60 * 6

_location_currency_cache: dict[str, tuple[float, str]] = {}
_fx_cache: dict[str, tuple[float, float, datetime]] = {}
_country_currency_cache: dict[str, tuple[float, str]] = {}

_KNOWN_LOCATION_CURRENCIES: dict[str, str] = {
    "india": "INR",
    "mumbai": "INR",
    "delhi": "INR",
    "bengaluru": "INR",
    "bangalore": "INR",
    "rajkot": "INR",
    "ahmedabad": "INR",
    "chennai": "INR",
    "kolkata": "INR",
    "hyderabad": "INR",
    "pune": "INR",
    "surat": "INR",
    "dubai": "AED",
    "abu dhabi": "AED",
    "sharjah": "AED",
    "uae": "AED",
    "united arab emirates": "AED",
    "london": "GBP",
    "united kingdom": "GBP",
    "uk": "GBP",
    "new york": "USD",
    "san francisco": "USD",
    "usa": "USD",
    "united states": "USD",
    "singapore": "SGD",
    "tokyo": "JPY",
    "japan": "JPY",
    "riyadh": "SAR",
    "saudi arabia": "SAR",
    "doha": "QAR",
    "qatar": "QAR",
    "kuwait": "KWD",
    "oman": "OMR",
    "muscat": "OMR",
    "bahrain": "BHD",
    "manama": "BHD",
    "hong kong": "HKD",
    "sydney": "AUD",
    "australia": "AUD",
    "toronto": "CAD",
    "canada": "CAD",
    "paris": "EUR",
    "france": "EUR",
    "berlin": "EUR",
    "germany": "EUR",
}

_COUNTRY_CURRENCY_FALLBACK: dict[str, str] = {
    "AE": "AED",
    "AU": "AUD",
    "BH": "BHD",
    "CA": "CAD",
    "CH": "CHF",
    "CN": "CNY",
    "DE": "EUR",
    "ES": "EUR",
    "FR": "EUR",
    "GB": "GBP",
    "HK": "HKD",
    "IN": "INR",
    "JP": "JPY",
    "KW": "KWD",
    "OM": "OMR",
    "QA": "QAR",
    "SA": "SAR",
    "SG": "SGD",
    "US": "USD",
}

_FX_RATE_FALLBACK_FROM_INR: dict[str, float] = {
    "AED": 0.044,
    "AUD": 0.018,
    "BHD": 0.0045,
    "CAD": 0.016,
    "CHF": 0.010,
    "CNY": 0.086,
    "EUR": 0.010,
    "GBP": 0.0088,
    "HKD": 0.094,
    "JPY": 1.88,
    "KWD": 0.0037,
    "OMR": 0.0046,
    "QAR": 0.044,
    "SAR": 0.045,
    "SGD": 0.015,
    "USD": 0.012,
}

SUPPORTED_DISPLAY_CURRENCIES = {"INR", "USD", "AED"}


def normalize_display_currency(value: str | None) -> str:
    currency = (value or "INR").strip().upper()
    return currency if currency in SUPPORTED_DISPLAY_CURRENCIES else "INR"


def _cache_get(cache: dict[str, tuple], key: str, ttl_seconds: int) -> Any | None:
    item = cache.get(key)
    if not item:
        return None
    created_at = item[0]
    if time.time() - created_at > ttl_seconds:
        cache.pop(key, None)
        return None
    return item[1:]


def _normalize_location(location: str | None) -> str:
    return " ".join((location or "").strip().lower().split())


def _known_currency_for_location(location: str | None) -> str | None:
    normalized = _normalize_location(location)
    if not normalized or normalized == "unknown":
        return None
    for token, currency in _KNOWN_LOCATION_CURRENCIES.items():
        if token in normalized:
            return currency
    return None


def _country_code_from_geocode_payload(payload: dict[str, Any]) -> str | None:
    for result in payload.get("results") or []:
        for component in result.get("address_components") or []:
            if "country" in component.get("types", []):
                code = (component.get("short_name") or "").strip().upper()
                if len(code) == 2:
                    return code
    return None


async def _country_code_for_location(location: str) -> str | None:
    if not settings.google_places_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(
                GEOCODE_URL,
                params={"address": location, "key": settings.google_places_api_key, "language": "en"},
            )
            response.raise_for_status()
            return _country_code_from_geocode_payload(response.json())
    except Exception as exc:  # pragma: no cover - external service behavior
        logger.info("Could not geocode location=%r for display currency: %s", location, exc)
        return None


async def _currency_for_country(country_code: str) -> str | None:
    code = country_code.strip().upper()
    if not code:
        return None
    cached = _cache_get(_country_currency_cache, code, _LOCATION_CACHE_TTL_SECONDS)
    if cached:
        return cached[0]
    if code in _COUNTRY_CURRENCY_FALLBACK:
        currency = _COUNTRY_CURRENCY_FALLBACK[code]
        _country_currency_cache[code] = (time.time(), currency)
        return currency
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(RESTCOUNTRIES_ALPHA_URL.format(country_code=code), params={"fields": "currencies"})
            response.raise_for_status()
            currencies = response.json().get("currencies") or {}
        currency = next(iter(currencies.keys()), None)
        if currency:
            currency = str(currency).upper()
            _country_currency_cache[code] = (time.time(), currency)
            return currency
    except Exception as exc:  # pragma: no cover - external service behavior
        logger.info("Could not resolve country currency for %s: %s", code, exc)
    return None


async def currency_for_location(location: str | None) -> str:
    normalized = _normalize_location(location)
    if not normalized or normalized == "unknown":
        return "INR"
    cached = _cache_get(_location_currency_cache, normalized, _LOCATION_CACHE_TTL_SECONDS)
    if cached:
        return cached[0]
    currency = _known_currency_for_location(normalized)
    if not currency:
        country_code = await _country_code_for_location(normalized)
        if country_code:
            currency = await _currency_for_country(country_code)
    currency = (currency or "INR").upper()
    _location_currency_cache[normalized] = (time.time(), currency)
    return currency


async def _fx_rate(base_currency: str, target_currency: str) -> tuple[float | None, datetime | None]:
    base = (base_currency or "INR").upper()
    target = (target_currency or "INR").upper()
    if base == target:
        return 1.0, datetime.now(timezone.utc)
    cache_key = f"{base}:{target}"
    cached = _cache_get(_fx_cache, cache_key, _FX_CACHE_TTL_SECONDS)
    if cached:
        return cached[0], cached[1]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(OPEN_EXCHANGE_URL.format(base_currency=base))
            response.raise_for_status()
            payload = response.json()
        rate = payload.get("rates", {}).get(target)
        if rate:
            as_of = datetime.now(timezone.utc)
            rate_float = float(rate)
            _fx_cache[cache_key] = (time.time(), rate_float, as_of)
            return rate_float, as_of
    except Exception as exc:  # pragma: no cover - external service behavior
        logger.info("Could not fetch FX rate %s -> %s: %s", base, target, exc)
    if base == "INR" and target in _FX_RATE_FALLBACK_FROM_INR:
        return _FX_RATE_FALLBACK_FROM_INR[target], datetime.now(timezone.utc)
    return None, None


async def apply_display_currency_to_results(
    results: list[UnifiedResult],
    display_currency: str | None = None,
) -> list[UnifiedResult]:
    display_currency = normalize_display_currency(display_currency)
    converted: list[UnifiedResult] = []
    rate_cache: dict[str, tuple[float | None, datetime | None]] = {}
    for result in results:
        next_result = result.model_copy(deep=True)
        source_currency = (next_result.currency or "INR").upper()
        if next_result.price is None:
            next_result.display_currency = display_currency
            key = f"{source_currency}:{display_currency}"
            if key not in rate_cache:
                rate_cache[key] = await _fx_rate(source_currency, display_currency)
            rate, as_of = rate_cache[key]
            _apply_gold_terms_display(next_result, rate, display_currency, as_of)
            converted.append(next_result)
            continue
        if source_currency == display_currency:
            next_result.display_price = next_result.price
            next_result.display_currency = display_currency
            next_result.fx_rate = 1.0
            next_result.fx_as_of = datetime.now(timezone.utc)
            _apply_gold_terms_display(next_result, 1.0, display_currency, next_result.fx_as_of)
            converted.append(next_result)
            continue
        key = f"{source_currency}:{display_currency}"
        if key not in rate_cache:
            rate_cache[key] = await _fx_rate(source_currency, display_currency)
        rate, as_of = rate_cache[key]
        if rate is None:
            next_result.display_price = next_result.price
            next_result.display_currency = source_currency
        else:
            next_result.display_price = round(float(next_result.price) * rate, 2)
            next_result.display_currency = display_currency
            next_result.fx_rate = rate
            next_result.fx_as_of = as_of
            _apply_gold_terms_display(next_result, rate, display_currency, as_of)
        converted.append(next_result)
    return converted


def _convert_number(value: Any, rate: float) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value) * rate, 2)
    except (TypeError, ValueError):
        return None


def _apply_rate_row_display(row: dict[str, Any], rate: float, currency: str, as_of: datetime | None) -> None:
    row["display_currency"] = currency
    if as_of:
        row["fx_as_of"] = as_of.isoformat()
    for key in ("buy_rate", "sell_rate", "day_high", "day_low"):
        converted = _convert_number(row.get(key), rate)
        if converted is not None:
            row[f"display_{key}"] = converted


def _apply_gold_terms_display(
    result: UnifiedResult,
    rate: float | None,
    currency: str,
    as_of: datetime | None,
) -> None:
    if rate is None or not isinstance(result.gold_terms, dict):
        return
    terms = dict(result.gold_terms)
    terms["display_currency"] = currency
    if as_of:
        terms["fx_as_of"] = as_of.isoformat()
    for key in ("buy_rate", "sell_rate", "day_high", "day_low"):
        converted = _convert_number(terms.get(key), rate)
        if converted is not None:
            terms[f"display_{key}"] = converted
    if isinstance(terms.get("rates"), list):
        display_rows = []
        for item in terms["rates"]:
            if not isinstance(item, dict):
                display_rows.append(item)
                continue
            row = dict(item)
            _apply_rate_row_display(row, rate, currency, as_of)
            display_rows.append(row)
        terms["rates"] = display_rows
    result.gold_terms = terms


async def apply_display_currency_to_search_response(
    response: SearchResponse,
    display_currency: str | None = None,
) -> SearchResponse:
    next_response = response.model_copy(deep=True)
    next_response.results = await apply_display_currency_to_results(
        next_response.results,
        display_currency,
    )
    return next_response


async def apply_display_currency_to_snapshot(
    snapshot: SearchProgressSnapshot,
    display_currency: str | None = None,
) -> SearchProgressSnapshot:
    next_snapshot = snapshot.model_copy(deep=True)
    next_snapshot.partial_results = await apply_display_currency_to_results(next_snapshot.partial_results, display_currency)
    if next_snapshot.final_results:
        next_snapshot.final_results = await apply_display_currency_to_search_response(
            next_snapshot.final_results,
            display_currency,
        )
    return next_snapshot


async def apply_display_currency_to_chat_response(
    response: ChatMessageResponse,
    display_currency: str | None = None,
) -> ChatMessageResponse:
    next_response = response.model_copy(deep=True)
    if next_response.results:
        next_response.results = await apply_display_currency_to_search_response(next_response.results, display_currency)
    if next_response.search_progress:
        next_response.search_progress = await apply_display_currency_to_snapshot(
            next_response.search_progress,
            display_currency,
        )
    return next_response
