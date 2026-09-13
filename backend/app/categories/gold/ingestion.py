from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    zwig_scrape_jobs_collection,
    zwig_vendor_calls_collection,
    zwig_vendor_category_links_collection,
    zwig_vendors_collection,
)
from app.categories.gold.channels import (
    CHANNEL_TYPE_CALL,
    CHANNEL_TYPE_WEBSITE,
    CHANNEL_TYPE_WHATSAPP,
    build_website_channel_metadata,
    build_vendor_id,
    normalize_website_url,
    record_vendor_interaction,
    upsert_vendor_channel,
)

logger = logging.getLogger(__name__)

GOLD_BULLION_CATEGORY_ID = "gold_bullion_24k"
SOURCE_CONFIDENCE_SCORES: dict[str, int] = {
    "ibja_verified": 70,
    "justdial": 45,
    "indiamart": 35,
    "google_places": 30,
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_phone_number(phone_number: str | None) -> str:
    if not phone_number:
        return ""
    digits = "".join(char for char in str(phone_number) if char.isdigit())
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) > 10:
        digits = digits[1:]
    return digits


def city_from_address(address: str | None) -> str:
    raw = (address or "").strip()
    if not raw:
        return "unknown"

    city_aliases = {
        "mumbai": "Mumbai",
        "delhi": "Delhi",
        "new delhi": "Delhi",
        "bangalore": "Bangalore",
        "bengaluru": "Bangalore",
        "ahmedabad": "Ahmedabad",
        "chennai": "Chennai",
        "hyderabad": "Hyderabad",
        "rajkot": "Rajkot",
        "pune": "Pune",
    }
    normalized = raw.lower()
    for alias, canonical in city_aliases.items():
        if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", normalized):
            return canonical

    parts = [part.strip() for part in re.split(r"[,|-]", raw) if part.strip()]
    return parts[-1] if parts else "unknown"


def geojson_point(lat: float | None, lng: float | None) -> dict[str, Any] | None:
    if lat is None or lng is None:
        return None
    return {
        "type": "Point",
        "coordinates": [float(lng), float(lat)],
    }


def source_confidence(source_name: str) -> int:
    return SOURCE_CONFIDENCE_SCORES.get(source_name, 25)


def base_vendor_category_doc(
    *,
    vendor_id: str,
    category_id: str,
    confidence_score: int,
) -> dict[str, Any]:
    now = now_utc()
    return {
        "vendor_id": vendor_id,
        "category_id": category_id,
        "confidence_score": confidence_score,
        "is_active": True,
        "blacklisted": False,
        "serves_b2b": None,
        "serves_b2c": None,
        "vendor_type": None,
        "specialisation": [],
        "fulfillment_type": None,
        "min_order_grams_estimate": None,
        "manual_review_required": False,
        "stats": {
            "pickup_rate": None,
            "avg_quote_vs_benchmark": None,
            "negotiation_rate": None,
        },
        "created_at": now,
        "updated_at": now,
    }


async def start_scrape_job(source_name: str, metadata: dict[str, Any] | None = None) -> str:
    started_at = now_utc()
    job_id = f"{source_name}:{int(started_at.timestamp())}"
    await zwig_scrape_jobs_collection.insert_one(
        {
            "job_id": job_id,
            "source_name": source_name,
            "status": "running",
            "metadata": metadata or {},
            "started_at": started_at,
            "updated_at": started_at,
        }
    )
    return job_id


async def finish_scrape_job(
    job_id: str,
    *,
    status: str,
    summary: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    finished_at = now_utc()
    update: dict[str, Any] = {
        "status": status,
        "updated_at": finished_at,
        "finished_at": finished_at,
    }
    if summary is not None:
        update["summary"] = summary
    if error is not None:
        update["error"] = error
    await zwig_scrape_jobs_collection.update_one(
        {"job_id": job_id},
        {"$set": update},
    )


def _merge_source_docs(
    existing_sources: list[dict[str, Any]],
    source_name: str,
    source_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    merged = [source for source in existing_sources if source.get("source_name") != source_name]
    merged.append({"source_name": source_name, **source_payload})
    return merged


async def upsert_gold_vendor(
    raw_vendor: dict[str, Any],
    *,
    source_name: str,
    category_id: str = GOLD_BULLION_CATEGORY_ID,
    initial_confidence_score: int | None = None,
) -> str:
    normalized_phone = normalize_phone_number(raw_vendor.get("phone_number") or raw_vendor.get("phone"))
    whatsapp_number = normalize_phone_number(raw_vendor.get("whatsapp_number") or raw_vendor.get("whatsapp"))
    google_place_id = raw_vendor.get("google_place_id") or raw_vendor.get("place_id")
    website_url = normalize_website_url(raw_vendor.get("website_url") or raw_vendor.get("website"))
    incoming_confidence = initial_confidence_score or source_confidence(source_name)

    existing_vendor = None
    if normalized_phone:
        existing_vendor = await zwig_vendors_collection.find_one({"phone_primary": normalized_phone})
    if existing_vendor is None and website_url:
        existing_vendor = await zwig_vendors_collection.find_one(
            {
                "$or": [
                    {"vendor_id": build_vendor_id(website_url=website_url, vendor_name=raw_vendor.get("name"))},
                    {"website": website_url},
                ]
            }
        )
    if existing_vendor is None and google_place_id:
        existing_vendor = await zwig_vendors_collection.find_one({"google_place_id": google_place_id})

    now = now_utc()
    address = raw_vendor.get("address") or ""
    city = raw_vendor.get("city") or city_from_address(address)
    lat = raw_vendor.get("lat")
    lng = raw_vendor.get("lng")
    location = geojson_point(lat, lng)
    vendor_name = raw_vendor.get("name") or raw_vendor.get("company_name") or "Unknown Vendor"
    specialisation = raw_vendor.get("specialisation") or raw_vendor.get("product_description") or ""
    source_payload = {
        key: value
        for key, value in {
            "listing_url": raw_vendor.get("listing_url") or raw_vendor.get("product_url"),
            "certificate_url": raw_vendor.get("certificate_url"),
            "maps_url": raw_vendor.get("maps_url"),
            "last_seen_at": now,
            "rating": raw_vendor.get("rating"),
            "review_count": raw_vendor.get("review_count") or raw_vendor.get("rating_count"),
            "category_tags": raw_vendor.get("category_tags") or raw_vendor.get("types"),
            "opening_hours": raw_vendor.get("opening_hours"),
            "product_description": raw_vendor.get("product_description"),
            "gst_number": raw_vendor.get("gst_number"),
            "google_place_id": google_place_id,
        }.items()
        if value not in (None, "", [])
    }

    if existing_vendor is not None:
        update_fields: dict[str, Any] = {
            "sources": _merge_source_docs(existing_vendor.get("sources") or [], source_name, source_payload),
            "updated_at": now,
            "confidence_score": max(existing_vendor.get("confidence_score", 0), incoming_confidence),
        }
        if not existing_vendor.get("address") and address:
            update_fields["address"] = address
        if not existing_vendor.get("city") and city:
            update_fields["city"] = city
        if not existing_vendor.get("google_place_id") and google_place_id:
            update_fields["google_place_id"] = google_place_id
        if not existing_vendor.get("website") and website_url:
            update_fields["website"] = website_url
        if existing_vendor.get("location") in (None, {}) and location is not None:
            update_fields["location"] = location
        if not existing_vendor.get("rating") and raw_vendor.get("rating") is not None:
            update_fields["rating"] = raw_vendor.get("rating")
        if not existing_vendor.get("review_count") and raw_vendor.get("review_count") is not None:
            update_fields["review_count"] = raw_vendor.get("review_count")
        if not existing_vendor.get("gst_number") and raw_vendor.get("gst_number"):
            update_fields["gst_number"] = raw_vendor.get("gst_number")
        if not existing_vendor.get("specialisation") and specialisation:
            update_fields["specialisation"] = specialisation

        existing_tags = existing_vendor.get("category_tags") or []
        incoming_tags = raw_vendor.get("category_tags") or raw_vendor.get("types") or []
        merged_tags = list(dict.fromkeys([*existing_tags, *incoming_tags]))
        if merged_tags:
            update_fields["category_tags"] = merged_tags

        if source_name == "ibja_verified":
            update_fields["verification.ibja_member"] = True
            update_fields["verification.ibja_verified_at"] = now

        insert_doc = base_vendor_category_doc(
            vendor_id=existing_vendor["vendor_id"],
            category_id=category_id,
            confidence_score=incoming_confidence,
        )
        insert_doc.pop("confidence_score", None)
        insert_doc.pop("updated_at", None)
        await zwig_vendors_collection.update_one(
            {"_id": existing_vendor["_id"]},
            {"$set": update_fields},
        )
        await zwig_vendor_category_links_collection.update_one(
            {"vendor_id": existing_vendor["vendor_id"], "category_id": category_id},
            {
                "$setOnInsert": insert_doc,
                "$max": {"confidence_score": incoming_confidence},
                "$set": {"updated_at": now},
            },
            upsert=True,
        )
        if normalized_phone:
            await upsert_vendor_channel(
                vendor_id=existing_vendor["vendor_id"],
                category_id=category_id,
                channel_type=CHANNEL_TYPE_CALL,
                channel_value=normalized_phone,
                city=city,
                priority=20,
                metadata={"source_name": source_name},
            )
        if whatsapp_number:
            await upsert_vendor_channel(
                vendor_id=existing_vendor["vendor_id"],
                category_id=category_id,
                channel_type=CHANNEL_TYPE_WHATSAPP,
                channel_value=whatsapp_number,
                city=city,
                priority=10,
                metadata={"source_name": source_name},
            )
        if website_url:
            await upsert_vendor_channel(
                vendor_id=existing_vendor["vendor_id"],
                category_id=category_id,
                channel_type=CHANNEL_TYPE_WEBSITE,
                channel_value=website_url,
                city=city,
                priority=1,
                metadata=build_website_channel_metadata(source_name=source_name),
            )
        logger.info("vendor enriched source=%s vendor_id=%s", source_name, existing_vendor["vendor_id"])
        return "vendor enriched"

    if not normalized_phone and not website_url:
        logger.info("Skipping vendor without usable phone or website for source=%s name=%s", source_name, vendor_name)
        return "vendor skipped"

    vendor_id = build_vendor_id(
        phone_number=normalized_phone or None,
        website_url=website_url or None,
        vendor_name=vendor_name,
    )
    vendor_doc = {
        "vendor_id": vendor_id,
        "name": vendor_name,
        "address": address,
        "city": city,
        "location": location,
        "rating": raw_vendor.get("rating"),
        "review_count": raw_vendor.get("review_count") or raw_vendor.get("rating_count"),
        "category_tags": raw_vendor.get("category_tags") or raw_vendor.get("types") or [],
        "specialisation": specialisation,
        "sources": [{"source_name": source_name, **source_payload}],
        "verification": {
            "ibja_member": source_name == "ibja_verified",
            "ibja_verified_at": now if source_name == "ibja_verified" else None,
            "gst_verified": False,
        },
        "gst_number": raw_vendor.get("gst_number"),
        "confidence_score": incoming_confidence,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    if normalized_phone:
        vendor_doc["phone_primary"] = normalized_phone
    if google_place_id:
        vendor_doc["google_place_id"] = google_place_id
    if website_url:
        vendor_doc["website"] = website_url
    if location is None:
        vendor_doc.pop("location", None)
    await zwig_vendors_collection.insert_one(vendor_doc)
    await zwig_vendor_category_links_collection.update_one(
        {"vendor_id": vendor_id, "category_id": category_id},
        {"$setOnInsert": base_vendor_category_doc(vendor_id=vendor_id, category_id=category_id, confidence_score=incoming_confidence)},
        upsert=True,
    )
    if normalized_phone:
        await upsert_vendor_channel(
            vendor_id=vendor_id,
            category_id=category_id,
            channel_type=CHANNEL_TYPE_CALL,
            channel_value=normalized_phone,
            city=city,
            priority=20,
            metadata={"source_name": source_name},
        )
    if whatsapp_number:
        await upsert_vendor_channel(
            vendor_id=vendor_id,
            category_id=category_id,
            channel_type=CHANNEL_TYPE_WHATSAPP,
            channel_value=whatsapp_number,
            city=city,
            priority=10,
            metadata={"source_name": source_name},
        )
    if website_url:
        await upsert_vendor_channel(
            vendor_id=vendor_id,
            category_id=category_id,
            channel_type=CHANNEL_TYPE_WEBSITE,
            channel_value=website_url,
            city=city,
            priority=1,
            metadata=build_website_channel_metadata(source_name=source_name),
        )
    logger.info("vendor created source=%s vendor_id=%s", source_name, vendor_id)
    return "vendor created"


def infer_gold_category_id(product: str, category: str, product_type_id: str) -> str | None:
    if category != "gold":
        return None
    if product_type_id == "gold_bullion":
        return GOLD_BULLION_CATEGORY_ID
    return None


async def record_vendor_call_outcome(
    *,
    vendor_id: str,
    query_id: str,
    category_id: str,
    outcome: str,
    duration: int | None,
    quote_received: bool,
    quoted_price: float | None,
    benchmark_rate: float | None,
    negotiated: bool,
    final_price: float | None,
    deal_closed: bool = False,
) -> None:
    now = now_utc()
    await zwig_vendor_calls_collection.insert_one(
        {
            "vendor_id": vendor_id,
            "query_id": query_id,
            "category_id": category_id,
            "outcome": outcome,
            "called_at": now,
            "duration": duration,
            "quote_received": quote_received,
            "quoted_price": quoted_price,
            "benchmark_rate": benchmark_rate,
            "negotiated": negotiated,
            "final_price": final_price,
            "deal_closed": deal_closed,
            "created_at": now,
        }
    )
    await record_vendor_interaction(
        vendor_id=vendor_id,
        category_id=category_id,
        query_id=query_id,
        interaction_type="call",
        channel_type=CHANNEL_TYPE_CALL,
        outcome=outcome,
        payload={
            "duration": duration,
            "quote_received": quote_received,
            "quoted_price": quoted_price,
            "benchmark_rate": benchmark_rate,
            "negotiated": negotiated,
            "final_price": final_price,
            "deal_closed": deal_closed,
        },
        occurred_at=now,
    )
    await recalculate_vendor_category_stats(vendor_id=vendor_id, category_id=category_id)


async def recalculate_vendor_category_stats(*, vendor_id: str, category_id: str) -> None:
    since = now_utc() - timedelta(days=30)
    cursor = zwig_vendor_calls_collection.find(
        {
            "vendor_id": vendor_id,
            "category_id": category_id,
            "called_at": {"$gte": since},
        }
    )
    calls = await cursor.to_list(length=500)
    total_calls = len(calls)
    if total_calls == 0:
        return

    quote_calls = [call for call in calls if call.get("quote_received")]
    pickup_rate = len(quote_calls) / total_calls
    negotiation_rate = (
        len([call for call in quote_calls if call.get("negotiated")]) / len(quote_calls)
        if quote_calls else None
    )
    deltas = []
    for call in quote_calls:
        benchmark_rate = call.get("benchmark_rate")
        quoted_price = call.get("quoted_price")
        if benchmark_rate and quoted_price:
            deltas.append((quoted_price - benchmark_rate) / benchmark_rate)
    avg_quote_vs_benchmark = sum(deltas) / len(deltas) if deltas else None

    category_doc = await zwig_vendor_category_links_collection.find_one({"vendor_id": vendor_id, "category_id": category_id})
    current_confidence = (category_doc or {}).get("confidence_score", 0)
    source_strength = current_confidence
    bonus = int(round(pickup_rate * 10))
    if negotiation_rate is not None:
        bonus += int(round(negotiation_rate * 5))
    if avg_quote_vs_benchmark is not None and avg_quote_vs_benchmark <= 0:
        bonus += 5
    recalculated_confidence = max(current_confidence, min(100, source_strength + bonus))

    await zwig_vendor_category_links_collection.update_one(
        {"vendor_id": vendor_id, "category_id": category_id},
        {
            "$set": {
                "stats.pickup_rate": pickup_rate,
                "stats.avg_quote_vs_benchmark": avg_quote_vs_benchmark,
                "stats.negotiation_rate": negotiation_rate,
                "confidence_score": recalculated_confidence,
                "updated_at": now_utc(),
            }
        },
    )
