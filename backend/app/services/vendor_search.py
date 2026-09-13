"""
Vendor Search Pipeline — runs after chat_session confirms the requirement.

Orchestrates:
1. LLM query generation (Google Places + IndiaMart keywords)
2. Three parallel tracks: Google Places, IndiaMart (live scraper), Online prices (SerpAPI)
3. LLM relevance filter on offline vendors
4. Rank and deduplicate
5. Progressive delivery of results via WhatsApp
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.categories.router import resolve_query_route
from app.categories.gold.selection import plan_gold_bullion_query
from app.database import db
from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo, VoiceCallResult
from app.services import voice_agent
from app.services.indiamart_live_search import search_indiamart_live

OnIndiaMartVendor = Callable[[dict[str, Any]], Awaitable[None] | None]

logger = logging.getLogger(__name__)

# MongoDB collection for search tracking
searches_collection = db["vendor_searches"]

# Shared OpenAI client
_openai_client: AsyncOpenAI | None = None


async def _send_whatsapp_message(phone: str, text: str, *, session_id: str | None = None) -> None:
    """Lazy-import send_text_message to avoid circular import."""
    from app.services.whatsapp import send_text_message
    await send_text_message(phone, text, session_id=session_id)


async def _send_whatsapp_flow(
    *,
    phone: str,
    flow_key: str,
    search_id: str,
    body: str,
    session_id: str | None = None,
) -> bool:
    from app.whatsapp_flows.service import send_flow_message_for_category

    return await send_flow_message_for_category(
        to=phone,
        flow_key=flow_key,
        search_id=search_id,
        body=body,
        session_id=session_id,
    )


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


# ═══════════════════════════════════════════════
# STEP 1 — LLM QUERY GENERATION
# ═══════════════════════════════════════════════

QUERY_GEN_SYSTEM_PROMPT = """\
You are a search query generator for an Indian sourcing platform.
Given a product and requirement details, generate search queries optimized for two platforms.

Return JSON only. No explanation. No markdown.

Format:
{
  "google_places_queries": ["query 1", "query 2", "query 3"],
  "indiamart_keywords": ["keyword 1", "keyword 2"]
}

Rules:
- google_places_queries: 3-4 queries, each phrased as a local business search. Include the city.
  Vary the business type vocabulary.
  Example for "bearing 6205, mumbai":
  ["bearing dealer mumbai", "industrial bearing supplier mumbai", "FAG SKF NSK bearing shop mumbai", "power transmission parts mumbai"]

- indiamart_keywords: 2-3 short product keywords only.
  No city — IndiaMart filters by city separately.
  Example for "bearing 6205":
  ["deep groove ball bearing 6205", "6205 bearing"]
  When Brand is provided, put the brand+product phrase FIRST, then a generic product keyword:
  Brand=Tupperware Product=water bottles → ["tupperware water bottle", "water bottle"]

- When Brand is provided, preserve that exact buyer brand in google_places_queries and indiamart_keywords.
  Do NOT substitute other manufacturers for a buyer-named brand.
- When Brand is empty, for industrial/B2B products you may include common manufacturer names in google_places_queries
- For consumer products keep queries simple and local
"""


async def generate_search_queries(product: str, collected: dict) -> dict:
    """Generate optimized search queries for Google Places and IndiaMart."""

    # Check cache first
    cache_key = (
        f"{product}|{collected.get('spec', '')}|{collected.get('location', '')}|"
        f"{collected.get('brand', '')}|{collected.get('search_specs', '')}"
    )
    cached = await searches_collection.find_one({"cache_key": cache_key, "type": "query_cache"})
    if cached and "queries" in cached:
        logger.info("Using cached search queries for %s", product)
        return cached["queries"]

    user_content = (
        f"Product: {product}\n"
        f"Brand: {collected.get('brand', '')}\n"
        f"Spec: {collected.get('spec', '')}\n"
        f"Search specs: {collected.get('search_specs', '')}\n"
        f"Location: {collected.get('location', '')}\n"
        f"Use case: {collected.get('use_case', '')}"
    )

    client = _get_openai_client()
    try:
        t0 = time.monotonic()
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": QUERY_GEN_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=300,
        )
        elapsed = time.monotonic() - t0
        raw = response.choices[0].message.content.strip()
        queries = json.loads(raw)
        logger.info("Generated search queries in %.2fs: %s", elapsed, queries)

        # Cache in MongoDB
        await searches_collection.update_one(
            {"cache_key": cache_key, "type": "query_cache"},
            {"$set": {"queries": queries, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return queries
    except Exception as exc:
        logger.error("LLM query generation failed: %s", exc)
        # Fallback: basic queries
        location = collected.get("location", "")
        brand = (collected.get("brand") or "").strip()
        branded = f"{brand} {product}".strip() if brand else product
        keywords = [branded, product] if brand and branded.lower() != product.lower() else [product]
        if collected.get("spec"):
            keywords.append(f"{product} {collected.get('spec', '')}".strip())
        return {
            "google_places_queries": [
                f"{branded} dealer {location}",
                f"{product} supplier {location}",
                f"{branded} shop {location}" if brand else f"{product} shop {location}",
            ],
            "indiamart_keywords": keywords[:3],
        }


# ═══════════════════════════════════════════════
# STEP 2 — THREE PARALLEL TRACKS
# ═══════════════════════════════════════════════

# ──────────────────────────────────────────────
# TRACK A — Google Places
# ──────────────────────────────────────────────

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"


async def _geocode_city(client: httpx.AsyncClient, location: str) -> tuple[float, float] | None:
    """Get lat/lng for a city name."""
    response = await client.get(
        GEOCODE_URL,
        params={"address": location, "key": settings.google_places_api_key},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") or []
    if not results:
        return None
    coords = results[0].get("geometry", {}).get("location")
    if not coords:
        return None
    return coords["lat"], coords["lng"]


async def _fetch_phone(client: httpx.AsyncClient, place_id: str) -> str | None:
    """Fetch phone number for a place via Place Details."""
    try:
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
    except Exception:
        return None


async def _search_places_single_query(
    client: httpx.AsyncClient,
    query: str,
    coordinates: tuple[float, float] | None,
) -> list[dict]:
    """Run a single Google Places text search query."""
    request_body: dict[str, Any] = {
        "textQuery": query,
        "maxResultCount": 10,
    }
    if coordinates:
        lat, lng = coordinates
        request_body["locationBias"] = {
            "circle": {"center": {"latitude": lat, "longitude": lng}, "radius": 20000.0}
        }

    try:
        response = await client.post(
            PLACES_SEARCH_URL,
            headers={
                "X-Goog-Api-Key": settings.google_places_api_key,
                "X-Goog-FieldMask": (
                    "places.id,places.displayName,places.formattedAddress,"
                    "places.location,places.rating,places.userRatingCount"
                ),
            },
            json=request_body,
            timeout=25,
        )
        response.raise_for_status()
        return response.json().get("places", [])
    except Exception as exc:
        logger.warning("Google Places query failed for '%s': %s", query, exc)
        return []


async def search_google_places(queries: list[str], location: str) -> list[dict]:
    """Run all Google Places queries in parallel, deduplicate, filter."""
    if not settings.google_places_api_key:
        logger.info("Google Places API key missing; skipping.")
        return []

    async with httpx.AsyncClient() as client:
        coordinates = await _geocode_city(client, location)

        # Run all queries in parallel
        all_places_lists = await asyncio.gather(
            *[_search_places_single_query(client, q, coordinates) for q in queries],
            return_exceptions=True,
        )

        # Flatten and deduplicate by place_id
        seen_ids: set[str] = set()
        unique_places: list[dict] = []
        for result in all_places_lists:
            if isinstance(result, Exception):
                continue
            for place in result:
                pid = place.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    unique_places.append(place)

        # Filter: rating >= 3.5 OR user_ratings_total >= 50
        filtered: list[dict] = []
        for place in unique_places:
            rating = place.get("rating", 0) or 0
            rating_count = place.get("userRatingCount", 0) or 0
            if rating >= 3.5 or rating_count >= 50:
                filtered.append(place)

        # Fetch phone numbers in parallel and build results
        async def _resolve_place(place: dict) -> dict | None:
            pid = place.get("id")
            if not pid:
                return None
            phone = await _fetch_phone(client, pid)
            if not phone:
                return None
            return {
                "source": "google_places",
                "place_id": pid,
                "vendor_key": f"offline:place:{pid}",
                "company_name": place.get("displayName", {}).get("text", "Unknown"),
                "phone": phone,
                "address": place.get("formattedAddress", ""),
                "rating": place.get("rating"),
                "rating_count": place.get("userRatingCount", 0),
                "maps_url": f"https://maps.google.com/?cid={pid}",
                "product_name": None,
                "price": None,
                "product_url": None,
            }

        resolved = await asyncio.gather(
            *[_resolve_place(p) for p in filtered[:30]],
            return_exceptions=True,
        )

        results = [r for r in resolved if isinstance(r, dict)]
        logger.info("Google Places: %d vendors found with phone numbers", len(results))
        return results[:30]


# ──────────────────────────────────────────────
# TRACK B — IndiaMart via live directory scrape
# ──────────────────────────────────────────────

INDIAMART_LIVE_MAX_RESULTS = 10


def _indiamart_vendor_key(
    *,
    company_name: str,
    product_url: str | None,
    catalog_url: str | None,
    phone: str | None,
    gst_number: str | None,
) -> str | None:
    """Stable vendor_key for reliability + cache."""
    url = product_url or catalog_url or ""
    docid_match = re.search(r"([0-9]+pxx[^/?]+)", url) or re.search(r"([0-9]+p[0-9]+-[^/?]+)", url)
    if docid_match:
        return f"online:indiamart:docid:{docid_match.group(1)}"

    # Live product URLs are usually /proddetail/<slug>-<digits>.html
    prod_id = re.search(r"/proddetail/[^/]*?(\d{8,})\.html", url, flags=re.I)
    if prod_id:
        return f"online:indiamart:product:{prod_id.group(1)}"

    if gst_number:
        return f"online:indiamart:gst:{gst_number}"
    if phone:
        digits = re.sub(r"\D+", "", str(phone))
        if digits:
            return f"online:indiamart:phone:{digits[-10:]}"
    slug = re.sub(r"[^a-z0-9]+", "-", (company_name or "").lower()).strip("-")
    return f"online:indiamart:name:{slug}" if slug else None


def _vendor_from_live_card(card: dict[str, Any]) -> dict | None:
    """Map a live IndiaMART card into the standard vendor_search format."""
    company_name = (card.get("supplier_name") or "").strip()
    product_name = (card.get("product_name") or "").strip()
    if not company_name and not product_name:
        return None
    if not company_name:
        company_name = product_name

    phones = card.get("phones") or []
    phone = phones[0] if phones else None
    price = card.get("price")
    if isinstance(price, str):
        price = price.replace("&#8377;", "\u20b9")

    raw_specs = card.get("attributes") or {}
    if not isinstance(raw_specs, dict):
        raw_specs = {}

    product_url = card.get("product_url")
    catalog_url = card.get("supplier_url")
    gst_number = card.get("gst_number")

    return {
        "source": "indiamart",
        "vendor_key": _indiamart_vendor_key(
            company_name=company_name,
            product_url=product_url,
            catalog_url=catalog_url,
            phone=phone,
            gst_number=gst_number,
        ),
        "company_name": company_name,
        "phone": phone,
        "address": card.get("address") or "",
        "city": card.get("city") or "",
        "state": card.get("state") or "",
        "rating": float(card["rating"]) if card.get("rating") is not None else None,
        "rating_count": int(card["rating_count"]) if card.get("rating_count") is not None else 0,
        "member_since": card.get("years_in_business"),
        "product_name": product_name or None,
        "price": price,
        "moq": card.get("moq"),
        "product_url": product_url,
        "catalog_url": catalog_url,
        "image_url": card.get("image_url"),
        "description": card.get("listing_text") or None,
        "specs": raw_specs,
        "gst_number": gst_number,
        "badges": card.get("badges") or [],
        "response_rate": card.get("response_rate"),
    }


def _indiamart_dedupe_key(vendor: dict[str, Any]) -> str | None:
    gst = vendor.get("gst_number")
    phone_digits = re.sub(r"\D+", "", str(vendor.get("phone") or ""))[-10:]
    catalog = (vendor.get("catalog_url") or "").rstrip("/").lower()
    product = (vendor.get("product_url") or "").rstrip("/").lower()
    if gst:
        return f"gst:{gst}"
    if phone_digits:
        return f"phone:{phone_digits}"
    if catalog:
        return f"catalog:{catalog}"
    if product:
        return f"product:{product}"
    return None


async def _run_indiamart_live(
    keyword: str,
    location: str,
    *,
    on_vendor: OnIndiaMartVendor,
) -> None:
    """Scrape IndiaMART for one keyword; invoke on_vendor only after phone enrichment."""

    async def _on_card(card: dict[str, Any]) -> None:
        vendor = _vendor_from_live_card(card)
        if not vendor or not vendor.get("phone"):
            return
        maybe = on_vendor(vendor)
        if maybe is not None:
            await maybe

    try:
        payload = await search_indiamart_live(
            product=keyword,
            city=location,
            max_results=INDIAMART_LIVE_MAX_RESULTS,
            fetch_mode="auto",
            include_phones=True,
            phone_limit=INDIAMART_LIVE_MAX_RESULTS,
            on_result=_on_card,
            emit_only_with_phone=True,
        )
    except Exception as exc:
        logger.warning("IndiaMart live search failed for keyword '%s' @ %s: %s", keyword, location, exc)
        return

    logger.info(
        "IndiaMart live: keyword=%r city=%r slug=%s phone_resolved=%s",
        keyword,
        location,
        payload.get("product_slug_used"),
        payload.get("phone_enrich_resolved"),
    )


async def search_indiamart(
    keywords: list[str],
    location: str,
    *,
    on_vendor: OnIndiaMartVendor | None = None,
) -> list[dict]:
    """Run live IndiaMART searches; emit each phone-ready vendor via on_vendor as it lands."""
    if not keywords:
        return []

    location = (location or "").strip()
    if not location or location.lower() == "unknown":
        logger.warning("IndiaMart live search skipped: missing location (keywords=%s)", keywords[:2])
        return []

    collected: list[dict] = []
    seen_keys: set[str] = set()
    seen_lock = asyncio.Lock()

    async def _emit(vendor: dict[str, Any]) -> None:
        key = _indiamart_dedupe_key(vendor)
        async with seen_lock:
            if key and key in seen_keys:
                return
            if key:
                seen_keys.add(key)
            if len(collected) >= 25:
                return
            collected.append(vendor)
        if on_vendor:
            maybe = on_vendor(vendor)
            if maybe is not None:
                await maybe

    results = await asyncio.gather(
        *[_run_indiamart_live(kw, location, on_vendor=_emit) for kw in keywords[:2]],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            logger.warning("IndiaMart live keyword task failed: %s", result)

    logger.info("IndiaMart: %d unique phone-ready vendors streamed", len(collected))
    return collected


# ──────────────────────────────────────────────
# TRACK C — Online prices via SerpAPI
# ──────────────────────────────────────────────

SERPAPI_URL = "https://serpapi.com/search.json"


def _online_search_url(query: str, *, engine: str = "google_shopping") -> str:
    encoded = query.replace(" ", "+")
    if engine == "amazon":
        return f"https://www.amazon.in/s?k={encoded}"
    return f"https://www.google.com/search?tbm=shop&q={encoded}"


def _item_url(item: dict, query: str, *, engine: str = "google_shopping") -> str:
    for key in ("link", "product_link", "serpapi_link"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _online_search_url(query, engine=engine)


async def _search_google_shopping(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Search Google Shopping via SerpAPI."""
    try:
        response = await client.get(
            SERPAPI_URL,
            params={
                "engine": "google_shopping",
                "q": query,
                "gl": "in",
                "hl": "en",
                "api_key": settings.serpapi_api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("shopping_results", [])[:5]:
            results.append({
                "source": "online",
                "platform": "flipkart" if "flipkart" in (item.get("link") or "").lower() else "google_shopping",
                "product_name": item.get("title", ""),
                "price": item.get("extracted_price") or item.get("price", ""),
                "rating": item.get("rating"),
                "rating_count": item.get("reviews"),
                "product_url": _item_url(item, query),
                "delivery": item.get("delivery"),
            })
        return results
    except Exception as exc:
        logger.warning("Google Shopping SerpAPI failed: %s", exc)
        return []


async def _search_amazon(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Search Amazon India via SerpAPI."""
    try:
        response = await client.get(
            SERPAPI_URL,
            params={
                "engine": "amazon",
                "k": query,
                "amazon_domain": "amazon.in",
                "api_key": settings.serpapi_api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("organic_results", [])[:5]:
            price_info = item.get("price", {})
            price = price_info.get("raw") if isinstance(price_info, dict) else price_info
            results.append({
                "source": "online",
                "platform": "amazon",
                "product_name": item.get("title", ""),
                "price": price or "",
                "rating": item.get("rating"),
                "rating_count": item.get("reviews", {}).get("total_reviews") if isinstance(item.get("reviews"), dict) else item.get("reviews"),
                "product_url": _item_url(item, query, engine="amazon"),
                "delivery": item.get("delivery", {}).get("tagline") if isinstance(item.get("delivery"), dict) else None,
            })
        return results
    except Exception as exc:
        logger.warning("Amazon SerpAPI failed: %s", exc)
        return []


async def search_online_prices(product: str, collected: dict) -> list[dict]:
    """Run Google Shopping and Amazon searches in parallel."""
    if not settings.serpapi_api_key:
        logger.info("SerpAPI key missing; skipping online prices.")
        return []

    query = f"{product} {collected.get('spec', '')}".strip()

    async with httpx.AsyncClient() as client:
        shopping_results, amazon_results = await asyncio.gather(
            _search_google_shopping(client, query),
            _search_amazon(client, query),
            return_exceptions=True,
        )

    if isinstance(shopping_results, Exception):
        shopping_results = []
    if isinstance(amazon_results, Exception):
        amazon_results = []

    # Keep top 3 per platform sorted by price ascending
    def _sort_key(r: dict) -> float:
        p = r.get("price")
        if isinstance(p, (int, float)):
            return p
        if isinstance(p, str):
            # Extract numeric value
            import re
            nums = re.findall(r"[\d,]+\.?\d*", p.replace(",", ""))
            if nums:
                try:
                    return float(nums[0])
                except ValueError:
                    pass
        return float("inf")

    shopping_results.sort(key=_sort_key)
    amazon_results.sort(key=_sort_key)

    combined = shopping_results[:3] + amazon_results[:3]
    logger.info("Online prices: %d results found", len(combined))
    return combined


# ═══════════════════════════════════════════════
# STEP 3 — LLM RELEVANCE FILTER
# ═══════════════════════════════════════════════

RELEVANCE_FILTER_SYSTEM_PROMPT = """\
You are a vendor relevance checker for an Indian sourcing platform.

For each vendor, determine if they would likely stock the requested product.

Return JSON only:
{"results": [{"company_name": "...", "relevant": true/false, "confidence": 0.0-1.0}]}

Be strict. Reject vendors where:
- Business type clearly does not match product
  (e.g. electronics shop for industrial bearings)
- Product listing is tangentially related but not the actual product needed
- Generic traders with no product specialization

Be lenient for:
- Industrial suppliers with broad catalogs
- Distributors and wholesalers
- Long-standing members (member_since before 2018)
"""


async def filter_relevant_vendors(
    vendors: list[dict],
    product: str,
    spec: str,
) -> list[dict]:
    """Filter vendors by LLM relevance check. Batch into groups of 10."""
    if not vendors:
        return []

    client = _get_openai_client()
    relevant_vendors: list[dict] = []

    # Process in batches of 10
    for i in range(0, len(vendors), 10):
        batch = vendors[i : i + 10]
        vendor_summaries = []
        for v in batch:
            summary = {
                "company_name": v.get("company_name", ""),
                "product_name": v.get("product_name", ""),
                "address": v.get("address", ""),
            }
            if v.get("member_since"):
                summary["member_since"] = v["member_since"]
            vendor_summaries.append(summary)

        user_content = (
            f"Required product: {product}\n"
            f"Spec: {spec}\n"
            f"Vendors to check:\n{json.dumps(vendor_summaries, ensure_ascii=False)}"
        )

        try:
            response = await client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": RELEVANCE_FILTER_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=500,
            )
            raw = response.choices[0].message.content.strip()
            data = json.loads(raw)
            results_list = data.get("results", [])

            # Map back to vendors
            name_to_vendor = {v["company_name"]: v for v in batch}
            for r in results_list:
                name = r.get("company_name", "")
                if r.get("relevant") and r.get("confidence", 0) >= 0.6:
                    vendor = name_to_vendor.get(name)
                    if vendor:
                        vendor["relevance_confidence"] = r["confidence"]
                        relevant_vendors.append(vendor)
        except Exception as exc:
            logger.warning("Relevance filter LLM call failed for batch: %s", exc)
            # On failure, include all vendors from this batch
            relevant_vendors.extend(batch)

    # Sort by confidence descending
    relevant_vendors.sort(key=lambda v: v.get("relevance_confidence", 0.5), reverse=True)
    logger.info("Relevance filter: %d/%d vendors passed", len(relevant_vendors[:15]), len(vendors))
    return relevant_vendors[:15]


# ═══════════════════════════════════════════════
# STEP 4 — THOMPSON VENDOR SELECTION
# ═══════════════════════════════════════════════

async def select_vendors_for_calling(
    vendors: list[dict],
    *,
    category: str | None,
    limit: int = 10,
) -> tuple[list[dict], list[dict]]:
    """Select callable vendors using Thompson sampling only.

    The LLM relevance filter determines the eligible pool. After that, vendor
    outreach selection is reliability/exploration driven instead of the old
    rating/member-since/has-price rule score.
    """
    callable_vendors = [vendor for vendor in vendors if vendor.get("phone")]
    if not callable_vendors or limit <= 0:
        return [], []

    try:
        from app.services.vendor_reliability import (
            CandidateVendor,
            is_vendor_eligible_to_call,
            select_vendors_to_call,
        )

        candidates = [
            CandidateVendor(
                vendor_key=vendor.get("vendor_key") or f"phone:{vendor['phone']}",
                handle=vendor,
                source=vendor.get("source"),
                phone=vendor.get("phone"),
                place_id=vendor.get("place_id"),
            )
            for vendor in callable_vendors
        ]

        eligible: list[CandidateVendor] = []
        for candidate in candidates:
            ok, _reason = await is_vendor_eligible_to_call(candidate.vendor_key)
            if ok:
                eligible.append(candidate)

        if not eligible:
            return [], []

        picked = await select_vendors_to_call(
            candidates=eligible,
            category=category or "default",
            n=min(limit, len(eligible)),
        )
        picked_keys = {candidate.vendor_key for candidate in picked}
        top_vendors = [candidate.handle for candidate in picked]
        reserve_vendors = [
            candidate.handle
            for candidate in eligible
            if candidate.vendor_key not in picked_keys
        ]
        logger.info(
            "Thompson selection: %d top vendors, %d reserve vendors",
            len(top_vendors),
            len(reserve_vendors),
        )
        return top_vendors, reserve_vendors
    except Exception as exc:  # pragma: no cover - reliability should not block search
        logger.warning("Thompson vendor selection skipped: %s", exc)
        return callable_vendors[:limit], callable_vendors[limit:]


# ═══════════════════════════════════════════════
# STEP 5 — PROGRESSIVE DELIVERY SEQUENCE
# ═══════════════════════════════════════════════

def format_online_results(results: list[dict], product: str) -> str:
    """Format online price results for WhatsApp."""
    lines = [f"Online prices for {product}:\n"]
    for i, r in enumerate(results, 1):
        platform = (r.get("platform") or "online").title()
        price = r.get("price") or "Price N/A"
        rating = r.get("rating") or "N/A"
        rating_count = r.get("rating_count") or 0
        url = r.get("product_url") or ""
        lines.append(
            f"{i}. *{platform}* \u2014 {price}\n"
            f"   \u2b50 {rating} ({rating_count} reviews)\n"
            f"   {url}\n"
        )
    return "\n".join(lines)


def format_vendor_result(vendor: dict, call_result: VoiceCallResult) -> str:
    """Format a single vendor call result for WhatsApp."""
    extracted = call_result.extracted_data or {}
    price = extracted.get("price", "Not quoted")
    availability = extracted.get("availability", "Unknown")
    delivery = extracted.get("delivery", "Not specified")

    parts = [
        f"*{vendor['company_name']}*",
        f"Price: {price}",
        f"Availability: {availability}",
        f"Delivery: {delivery}",
        f"Contact: {vendor.get('phone', 'N/A')}",
    ]

    rating = vendor.get("rating")
    rating_count = vendor.get("rating_count", 0)
    if rating:
        parts.append(f"Rating: \u2b50 {rating} ({rating_count} reviews)")

    if vendor.get("maps_url"):
        parts.append(f"Maps: {vendor['maps_url']}")
    if vendor.get("product_url"):
        parts.append(f"Product: {vendor['product_url']}")

    return "\n".join(parts)


def format_gold_live_results(results: list[UnifiedResult], product: str, city: str) -> str:
    lines = [f"*Live bullion rates for {product} in {city}*"]
    for result in results[:8]:
        price_display = f"₹{result.price:,.0f}" if result.price is not None else "Price unavailable"
        lines.append(f"{result.name}")
        lines.append(f"   {price_display}")
        if result.url:
            lines.append(f"   {result.url}")
    return "\n".join(lines)


def format_gold_vendor_shortlist(vendors: list[VendorInfo], city: str) -> str:
    lines = [f"*Trusted bullion dealers after {city} live scripts*"]
    for vendor in vendors[:10]:
        channel_labels = []
        if vendor.live_script_available:
            channel_labels.append("Live Script")
        if vendor.whatsapp_available:
            channel_labels.append("WhatsApp")
        if vendor.call_available:
            channel_labels.append("Call")
        suffix = f" ({', '.join(channel_labels)})" if channel_labels else ""
        confidence = f" | score {vendor.confidence_score:.0f}" if vendor.confidence_score is not None else ""
        lines.append(f"- {vendor.name}{suffix}{confidence}")
    return "\n".join(lines)


async def run_search_and_deliver(
    phone: str,
    product: str,
    collected: dict,
    *,
    session_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Main pipeline: search vendors and deliver results progressively via WhatsApp."""

    search_id = str(uuid.uuid4())
    location = collected.get("location", "unknown")

    # Initialize search record in MongoDB
    search_doc = {
        "search_id": search_id,
        "phone": phone,
        "product": product,
        "collected": collected,
        "status": "searching",
        "online_results": [],
        "vendors_found": 0,
        "vendors_called": 0,
        "quotes_received": 0,
        "results": [],
        "created_at": datetime.now(timezone.utc),
        "completed_at": None,
    }
    await searches_collection.insert_one(search_doc)

    # T+0: start message
    await _send_whatsapp_message(
        phone,
        f"Search started for {product} in {location}. "
        f"Checking online + local vendors now.",
        session_id=session_id,
    )

    structured_query = StructuredQuery(
        product=product,
        category=collected.get("category", ""),
        location=location,
        intent=collected.get("intent", "cheapest"),
        urgency=collected.get("urgency", "immediate"),
        raw_query=collected.get("raw_query") or f"{product} in {location}",
    )

    if (await resolve_query_route(structured_query)).handler_key == "gold.bullion":
        plan = await plan_gold_bullion_query(structured_query)
        if plan.live_results:
            flow_intro = (
                f"I found {len(plan.live_results)} live bullion rates in {plan.city or location}. "
                "Open the live rates board below."
            )
            flow_sent = await _send_whatsapp_flow(
                phone=phone,
                flow_key="gold_live_rates",
                search_id=search_id,
                body=flow_intro,
                session_id=session_id,
            )
            if not flow_sent:
                await _send_whatsapp_message(
                    phone,
                    format_gold_live_results(plan.live_results, product, plan.city or location),
                    session_id=session_id,
                )
        else:
            refresh_note = ""
            if plan.live_refresh_summary.attempted:
                refresh_note = (
                    f" I checked {plan.live_refresh_summary.attempted} live website feed"
                    f"{'s' if plan.live_refresh_summary.attempted != 1 else ''}"
                    f" and refreshed {plan.live_refresh_summary.refreshed}."
                )
            await _send_whatsapp_message(
                phone,
                f"No live bullion website rates are available in {location} yet."
                f"{refresh_note} I’ll continue with the most trusted dealers next.",
                session_id=session_id,
            )

        if plan.discovered_vendors:
            await _send_whatsapp_message(
                phone,
                format_gold_vendor_shortlist(plan.discovered_vendors, plan.city or location),
                session_id=session_id,
            )

        await searches_collection.update_one(
            {"search_id": search_id},
            {
                "$set": {
                    "online_results": [result.model_dump(mode="json") for result in plan.live_results],
                    "vendors_found": len(plan.discovered_vendors),
                }
            },
        )

        if not plan.outreach_vendors:
            await _send_whatsapp_message(
                phone,
                "Search completed. Live bullion scripts are ready above. "
                "Send more dealers whenever you want me to expand the network.",
                session_id=session_id,
            )
            await searches_collection.update_one(
                {"search_id": search_id},
                {
                    "$set": {
                        "status": "complete",
                        "completed_at": datetime.now(timezone.utc),
                    }
                },
            )
            return

        await _send_whatsapp_message(
            phone,
            f"Found {len(plan.outreach_vendors)} trusted bullion dealers with phone support. Calling them now.",
            session_id=session_id,
        )

        quotes_received = 0
        all_results: list[dict] = []

        async def call_gold_vendor_and_deliver(vendor_info: VendorInfo) -> None:
            nonlocal quotes_received
            try:
                result = await voice_agent.call_vendor(vendor_info, product, query=structured_query)
                result = await voice_agent.poll_call_result(result)
                if result.status == "completed" and result.extracted_data:
                    extracted = result.extracted_data
                    if extracted.get("price") or extracted.get("availability"):
                        quotes_received += 1
                        all_results.append({"vendor": vendor_info.model_dump(mode="json"), "result": extracted})
                        await _send_whatsapp_message(
                            phone,
                            format_vendor_result(
                                {
                                    "company_name": vendor_info.name,
                                    "phone": vendor_info.phone,
                                    "address": vendor_info.address,
                                    "rating": vendor_info.rating,
                                    "rating_count": vendor_info.user_rating_count,
                                    "product_url": vendor_info.website,
                                },
                                result,
                            ),
                            session_id=session_id,
                        )
            except Exception as exc:
                logger.warning("Gold call failed for vendor %s: %s", vendor_info.name, exc)

        await asyncio.gather(
            *[call_gold_vendor_and_deliver(vendor) for vendor in plan.outreach_vendors],
            return_exceptions=True,
        )

        await searches_collection.update_one(
            {"search_id": search_id},
            {
                "$set": {
                    "vendors_called": len(plan.outreach_vendors),
                    "quotes_received": quotes_received,
                    "results": all_results,
                    "status": "complete",
                    "completed_at": datetime.now(timezone.utc),
                }
            },
        )
        await _send_whatsapp_message(
            phone,
            "Gold search completed. You can send another bullion query or expand this city shortlist whenever you want.",
            session_id=session_id,
        )
        return

    # Step 1: Generate search queries
    queries = await generate_search_queries(product, collected)
    google_queries = queries.get("google_places_queries", [])
    indiamart_keywords = queries.get("indiamart_keywords", [])

    # Step 2: Run tracks in parallel. IndiaMART messages stream as phones enrich.
    whatsapp_indiamart_sent = 0
    whatsapp_indiamart_lock = asyncio.Lock()

    async def _send_indiamart_whatsapp_card(r: dict[str, Any]) -> None:
        nonlocal whatsapp_indiamart_sent
        if not r.get("phone"):
            return
        async with whatsapp_indiamart_lock:
            if whatsapp_indiamart_sent >= 8:
                return
            whatsapp_indiamart_sent += 1
        price = (r.get("price") or "").replace("\u20b9", "₹")
        company = r.get("company_name", "")
        prod_name = r.get("product_name", "")
        moq = r.get("moq", "")
        vendor_phone = r.get("phone", "")
        product_url = r.get("product_url", "")

        parts = [f"*{company}*", prod_name]
        if price:
            parts.append(f"Price: {price}")
        if moq:
            parts.append(f"MOQ: {moq}")
        parts.append(f"📞 {vendor_phone}")
        if product_url:
            parts.append(product_url)

        await _send_whatsapp_message(
            phone,
            "\n".join(part for part in parts if part),
            session_id=session_id,
        )

    online_results, places_results, indiamart_results = await asyncio.gather(
        search_online_prices(product, collected),
        search_google_places(google_queries, location),
        search_indiamart(
            indiamart_keywords,
            location,
            on_vendor=_send_indiamart_whatsapp_card,
        ),
        return_exceptions=True,
    )

    # Handle exceptions
    if isinstance(online_results, Exception):
        logger.warning("Online prices track failed: %s", online_results)
        online_results = []
    if isinstance(places_results, Exception):
        logger.warning("Google Places track failed: %s", places_results)
        places_results = []
    if isinstance(indiamart_results, Exception):
        logger.warning("IndiaMart track failed: %s", indiamart_results)
        indiamart_results = []

    # T+2: Send online results immediately
    if online_results:
        await _send_whatsapp_message(
            phone,
            format_online_results(online_results, product),
            session_id=session_id,
        )
        await searches_collection.update_one(
            {"search_id": search_id},
            {"$set": {"online_results": online_results}},
        )

    # Filter offline vendors, then select call targets via Thompson sampling.
    db_vendor_dicts: list[dict] = []
    try:
        from app.services.product_vendor_routing import find_vendor_offerings

        route = await find_vendor_offerings(structured_query)
        db_vendor_dicts = [
            {
                "source": "vendor_offerings",
                "vendor_key": vendor.vendor_id or f"phone:{vendor.phone}",
                "company_name": vendor.name,
                "phone": vendor.phone,
                "address": vendor.address,
                "rating": vendor.rating,
                "rating_count": vendor.user_rating_count,
                "place_id": vendor.place_id,
                "offering_id": vendor.offering_id,
                "product_id": vendor.product_id,
                "category_id": vendor.category_id,
            }
            for vendor in route.to_vendors()
        ]
    except Exception as exc:  # pragma: no cover - DB routing should not block live search
        logger.warning("DB product-vendor routing skipped: %s", exc)

    combined = db_vendor_dicts + places_results + indiamart_results
    if combined:
        filtered = await filter_relevant_vendors(combined, product, collected.get("spec", ""))
        top_vendors, reserve_vendors = await select_vendors_for_calling(
            filtered,
            category=collected.get("category"),
            limit=10,
        )
    else:
        top_vendors, reserve_vendors = [], []

    await searches_collection.update_one(
        {"search_id": search_id},
        {"$set": {"vendors_found": len(top_vendors) + len(reserve_vendors)}},
    )

    if not top_vendors:
        await _send_whatsapp_message(
            phone,
            "Could not find local vendors for this product in your area. "
            "Try a different location or product specification.",
            session_id=session_id,
        )
        await searches_collection.update_one(
            {"search_id": search_id},
            {"$set": {"status": "complete", "completed_at": datetime.now(timezone.utc)}},
        )
        return

    # T+3: Notify how many vendors being called
    await _send_whatsapp_message(
        phone,
        f"Found {len(top_vendors)} local vendors. Calling them now for live prices.",
        session_id=session_id,
    )

    # T+3 onwards: Call vendors and send results as each completes
    quotes_received = 0
    all_results: list[dict] = []

    async def call_and_deliver(vendor: dict) -> None:
        nonlocal quotes_received
        # Build VendorInfo for voice_agent
        vendor_info = VendorInfo(
            vendor_id=vendor.get("vendor_key"),
            name=vendor["company_name"],
            phone=vendor.get("phone", ""),
            address=vendor.get("address", ""),
            rating=vendor.get("rating"),
            user_rating_count=vendor.get("rating_count"),
            offering_id=vendor.get("offering_id"),
            product_id=vendor.get("product_id"),
            category_id=vendor.get("category_id"),
            is_mock=False,
        )

        try:
            result = await voice_agent.call_vendor(vendor_info, product, query=structured_query)
            # Poll for result if needed
            result = await voice_agent.poll_call_result(result)

            if result.status == "completed" and result.extracted_data:
                extracted = result.extracted_data
                if extracted.get("price") or extracted.get("availability"):
                    quotes_received += 1
                    all_results.append({"vendor": vendor, "result": extracted})
                    if vendor_info.vendor_id and vendor_info.offering_id and vendor_info.category_id:
                        try:
                            from app.services.product_vendor_routing import record_price_observation

                            raw_price = extracted.get("price")
                            price = None
                            if isinstance(raw_price, (int, float)):
                                price = float(raw_price)
                            elif isinstance(raw_price, str):
                                nums = re.findall(r"[\d,]+\.?\d*", raw_price.replace(",", ""))
                                price = float(nums[0]) if nums else None
                            await record_price_observation(
                                vendor_id=vendor_info.vendor_id,
                                offering_id=vendor_info.offering_id,
                                product_id=vendor_info.product_id,
                                category_id=vendor_info.category_id,
                                query_id=search_id,
                                price=price,
                                availability=bool(extracted.get("availability", True)),
                                source_type="call",
                                call_id=result.call_id,
                                notes=json.dumps(extracted, ensure_ascii=False),
                                confidence=float(extracted.get("confidence") or 0.7),
                            )
                        except Exception as exc:  # pragma: no cover - persistence should not block delivery
                            logger.debug("vendor offering price observation skipped: %s", exc)
                    await _send_whatsapp_message(
                        phone,
                        format_vendor_result(vendor, result),
                        session_id=session_id,
                    )
        except Exception as exc:
            logger.warning("Call failed for vendor %s: %s", vendor["company_name"], exc)

    # Call all top vendors in parallel
    await asyncio.gather(
        *[call_and_deliver(v) for v in top_vendors],
        return_exceptions=True,
    )

    await searches_collection.update_one(
        {"search_id": search_id},
        {"$set": {"vendors_called": len(top_vendors), "quotes_received": quotes_received}},
    )

    # Keep the demo flow aggressive: attempt at least 10 offline negotiations
    # whenever enough callable vendors are available, even if online listings
    # already produced prices.
    attempted_calls = len(top_vendors)
    if attempted_calls < 10 and reserve_vendors:
        reserve_to_call = reserve_vendors[: 10 - attempted_calls]
        await asyncio.gather(
            *[call_and_deliver(v) for v in reserve_to_call],
            return_exceptions=True,
        )
        await searches_collection.update_one(
            {"search_id": search_id},
            {
                "$set": {
                    "vendors_called": len(top_vendors) + len(reserve_to_call),
                    "quotes_received": quotes_received,
                }
            },
        )

    # Final summary
    await _send_whatsapp_message(
        phone,
        "Search completed. Let me know if you want to buy any other product or need more options for the same product.",
        session_id=session_id,
    )

    # Update search status
    await searches_collection.update_one(
        {"search_id": search_id},
        {
            "$set": {
                "status": "complete",
                "results": all_results,
                "completed_at": datetime.now(timezone.utc),
            }
        },
    )

    # Backfill the ops dashboard request (dashboard.zwig.in) with the results
    # snapshot now that the async pipeline has finished. The request was opened
    # empty at search kickoff (chat_session). Best-effort; lazy import avoids a
    # gold_requests <-> vendor_search import cycle.
    if request_id:
        try:
            from app.services import gold_requests

            summary_rows: list[dict[str, Any]] = []
            for entry in all_results:
                vendor = entry.get("vendor") or {}
                extracted = entry.get("result") or {}
                summary_rows.append(
                    {
                        "vendor_name": vendor.get("company_name"),
                        "city": vendor.get("city") or "",
                        "label": vendor.get("product_name") or product,
                        "sell_rate": extracted.get("price") or vendor.get("price"),
                        "url": vendor.get("product_url") or vendor.get("maps_url"),
                        "phone": vendor.get("phone"),
                        "result_type": "vendor_quote",
                    }
                )
            for r in (online_results or []):
                summary_rows.append(
                    {
                        "vendor_name": (r.get("platform") or "online").title(),
                        "city": "",
                        "label": r.get("product_name") or product,
                        "sell_rate": r.get("price"),
                        "url": r.get("product_url"),
                        "result_type": "online",
                    }
                )
            await gold_requests.update_request_results(request_id, results=summary_rows)
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to backfill dashboard request %s results: %s", request_id, exc)

    logger.info(
        "Search pipeline complete: search_id=%s vendors_called=%d quotes=%d",
        search_id,
        len(top_vendors),
        quotes_received,
    )
