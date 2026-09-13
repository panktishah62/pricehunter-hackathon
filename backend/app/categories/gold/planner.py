from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import (
    zwig_live_rate_snapshots_collection,
    zwig_vendor_category_links_collection,
    zwig_vendor_channels_collection,
    zwig_vendors_collection,
)
from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo
from app.categories.gold.channels import (
    CHANNEL_TYPE_CALL,
    CHANNEL_TYPE_WEBSITE,
    CHANNEL_TYPE_WHATSAPP,
    is_website_channel_type,
)
from app.categories.gold.classifier import is_gold_bullion_query
from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID, city_from_address
from app.categories.gold.metal import dedupe_rate_snapshots, infer_bullion_metal, snapshot_metal
from app.categories.gold.live_rates import LiveRateRefreshSummary, refresh_website_live_rates_for_query
from app.services.gold_rate_rules import (
    FRESH_SNAPSHOT_HOURS,
    is_price_outlier,
    is_retail_rate,
    national_bullion_consensus,
    snapshot_price_10g,
)

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 60
MAX_LIVE_RESULTS = 20
MAX_DISCOVERED_VENDORS = 20


@dataclass
class GoldQueryPlan:
    city: str
    live_results: list[UnifiedResult] = field(default_factory=list)
    discovered_vendors: list[VendorInfo] = field(default_factory=list)
    outreach_vendors: list[VendorInfo] = field(default_factory=list)
    same_city_live_vendors: list[VendorInfo] = field(default_factory=list)
    high_confidence_live_vendors: list[VendorInfo] = field(default_factory=list)
    remote_confident_vendors: list[VendorInfo] = field(default_factory=list)
    live_refresh_summary: LiveRateRefreshSummary = field(default_factory=LiveRateRefreshSummary)
def extract_quantity_grams(query: StructuredQuery) -> float | None:
    normalized = f"{getattr(query, 'quantity', None) or ''} {query.product} {query.raw_query}".lower()
    kg_match = re.search(r"\b(\d+(?:\.\d+)?)\s?kg\b", normalized)
    if kg_match:
        return float(kg_match.group(1)) * 1000
    g_match = re.search(r"\b(\d+(?:\.\d+)?)\s?(?:g|gm|gram|grams)\b", normalized)
    if g_match:
        return float(g_match.group(1))
    return None


def infer_buyer_type(query: StructuredQuery) -> str:
    normalized = f"{query.product} {query.raw_query}".lower()
    grams = extract_quantity_grams(query)
    if grams is not None and grams >= 500:
        return "b2b"
    if any(token in normalized for token in ("bulk", "wholesale", "dealer", "resale", "kg", "kilo")):
        return "b2b"
    return "b2c"


def _canonical_city(value: str | None) -> str:
    if not value:
        return ""
    return city_from_address(value).strip()


def _extract_purity_hints(query: StructuredQuery) -> set[str]:
    normalized = f"{query.product} {query.raw_query}".lower()
    hints: set[str] = set()
    if "999" in normalized:
        hints.add("999")
    if "24k" in normalized or "24 k" in normalized:
        hints.update({"24k", "999"})
    if "995" in normalized:
        hints.add("995")
    return hints


def _channel_summary(vendor: dict[str, Any]) -> dict[str, Any]:
    return vendor.get("channel_summary") or {}


def _vendor_info_from_docs(
    vendor: dict[str, Any],
    category_doc: dict[str, Any] | None,
    channel_docs: list[dict[str, Any]],
    *,
    bucket: str,
) -> VendorInfo:
    location = vendor.get("location") or {}
    coordinates = location.get("coordinates") or [None, None]
    channel_types = sorted(
        {
            *(channel.get("channel_type") for channel in channel_docs if channel.get("channel_type")),
            *(_channel_summary(vendor).get("channel_types") or []),
        }
    )
    if vendor.get("phone_primary") and CHANNEL_TYPE_CALL not in channel_types:
        channel_types.append(CHANNEL_TYPE_CALL)
    website_channel_count = len([channel for channel in channel_docs if is_website_channel_type(channel.get("channel_type"))])
    live_script_count = max(
        website_channel_count,
        int(_channel_summary(vendor).get("live_script_count") or 0),
        int(_channel_summary(vendor).get("website_count") or 0),
    )
    whatsapp_available = bool(
        any(channel.get("channel_type") == CHANNEL_TYPE_WHATSAPP for channel in channel_docs)
        or _channel_summary(vendor).get("whatsapp_available")
    )
    call_available = bool(vendor.get("phone_primary")) or bool(
        any(channel.get("channel_type") == CHANNEL_TYPE_CALL for channel in channel_docs)
        or _channel_summary(vendor).get("call_available")
    )
    live_script_available = bool(live_script_count)
    preferred_contact_channel = _channel_summary(vendor).get("preferred_contact_channel")
    if not preferred_contact_channel:
        if whatsapp_available:
            preferred_contact_channel = CHANNEL_TYPE_WHATSAPP
        elif call_available:
            preferred_contact_channel = CHANNEL_TYPE_CALL
        elif live_script_available:
            preferred_contact_channel = CHANNEL_TYPE_WEBSITE

    website = vendor.get("website") or next(
        (
            channel.get("channel_value")
            for channel in channel_docs
            if is_website_channel_type(channel.get("channel_type")) and channel.get("channel_value")
        ),
        None,
    )
    return VendorInfo(
        vendor_id=vendor.get("vendor_id"),
        name=vendor.get("name", "Unknown Vendor"),
        phone=vendor.get("phone_primary", ""),
        address=vendor.get("address", ""),
        city=vendor.get("city"),
        location={
            "lat": coordinates[1] if len(coordinates) > 1 else None,
            "lng": coordinates[0] if coordinates else None,
        },
        place_id=vendor.get("google_place_id"),
        rating=vendor.get("rating"),
        user_rating_count=vendor.get("review_count"),
        confidence_score=(category_doc or {}).get("confidence_score", vendor.get("confidence_score")),
        channel_types=channel_types,
        live_script_available=live_script_available,
        live_script_count=live_script_count,
        whatsapp_available=whatsapp_available,
        call_available=call_available,
        preferred_contact_channel=preferred_contact_channel,
        website=website,
        bucket=bucket,
        is_mock=False,
    )


def _best_live_price(snapshot: dict[str, Any]) -> float | None:
    """The snapshot's usable rate, NORMALIZED to a single ₹/10g basis.

    Indian bullion boards mix per-gram and per-10g quotes (a vendor may even
    publish both, e.g. "GOLD 999" per-gram and "GOLD 999 10GM"). Displaying /
    sorting on the raw sell_rate makes a per-gram quote (~₹14,500) look 10x
    cheaper than a per-10g one (~₹145,000) and wrongly headline as the "best"
    rate. Normalizing to ₹/10g (same basis the outlier guard uses) keeps the
    board consistent and the cheapest-rate ordering correct."""
    return snapshot_price_10g(snapshot)


def _snapshot_timestamp(snapshot: dict[str, Any]) -> datetime | None:
    timestamp = snapshot.get("rate_timestamp") or snapshot.get("fetched_at")
    if not isinstance(timestamp, datetime):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp


def _snapshot_rate_row(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw_payload = snapshot.get("raw_payload") or {}
    timestamp = _snapshot_timestamp(snapshot)
    return {
        "script_name": snapshot.get("script_name") or "Live Script",
        "buy_rate": snapshot.get("buy_rate"),
        "sell_rate": snapshot.get("sell_rate"),
        "day_high": raw_payload.get("day_high"),
        "day_low": raw_payload.get("day_low"),
        "purity": snapshot.get("purity"),
        "product_type": snapshot.get("product_type"),
        "quantity_grams": snapshot.get("quantity_grams"),
        "unit": snapshot.get("unit"),
        "source_name": snapshot.get("source_name"),
        "source_url": snapshot.get("source_url"),
        "updated_at": timestamp.isoformat() if timestamp else None,
    }


def _gold_terms_from_snapshot(
    snapshot: dict[str, Any],
    vendor: dict[str, Any],
    related_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timestamp = _snapshot_timestamp(snapshot)
    rate_rows = [
        _snapshot_rate_row(item)
        for item in dedupe_rate_snapshots(related_snapshots or [snapshot])
    ]
    return {
        "vendor_name": vendor.get("name"),
        "script_name": snapshot.get("script_name") or "Live Script",
        "buy_rate": snapshot.get("buy_rate"),
        "sell_rate": snapshot.get("sell_rate"),
        "purity": snapshot.get("purity"),
        "quantity_grams": snapshot.get("quantity_grams"),
        "unit": snapshot.get("unit"),
        "source_name": snapshot.get("source_name"),
        "source_url": snapshot.get("source_url") or vendor.get("website"),
        "updated_at": timestamp.isoformat() if timestamp else None,
        "rates": rate_rows,
    }


def _freshness_penalty(snapshot: dict[str, Any]) -> float:
    timestamp = _snapshot_timestamp(snapshot)
    if timestamp is None:
        return 0.0
    age_seconds = max((datetime.now(timezone.utc) - timestamp).total_seconds(), 0.0)
    return min(age_seconds / 3600.0, 24.0)


def _live_snapshot_match_score(snapshot: dict[str, Any], query: StructuredQuery) -> tuple[float, float, float]:
    score = 0.0
    script_name = (snapshot.get("script_name") or "").lower()
    purity = str(snapshot.get("purity") or "").lower()
    wanted_metal = infer_bullion_metal(f"{query.product} {query.raw_query}")
    if snapshot_metal(snapshot) == wanted_metal:
        score += 8.0
    for hint in _extract_purity_hints(query):
        if hint in purity or hint in script_name:
            score += 5.0

    quantity_grams = extract_quantity_grams(query)
    snapshot_quantity = snapshot.get("quantity_grams")
    if quantity_grams and isinstance(snapshot_quantity, (int, float)):
        if abs(float(snapshot_quantity) - quantity_grams) < 0.001:
            score += 4.0
        elif float(snapshot_quantity) <= quantity_grams:
            score += 2.0
    elif quantity_grams:
        quantity_tokens = {str(int(quantity_grams)), f"{int(quantity_grams)}gm", f"{int(quantity_grams)}g"}
        if any(token in script_name for token in quantity_tokens):
            score += 3.0

    if snapshot.get("city") and _canonical_city(snapshot.get("city")).lower() == _canonical_city(query.location).lower():
        score += 4.0
    price = _best_live_price(snapshot)
    price_sort = price if price is not None else float("inf")
    freshness_sort = _freshness_penalty(snapshot)
    return (-score, price_sort, freshness_sort)


def _build_live_result(
    snapshot: dict[str, Any],
    vendor: dict[str, Any],
    category_doc: dict[str, Any] | None,
    related_snapshots: list[dict[str, Any]] | None = None,
) -> UnifiedResult:
    best_price = _best_live_price(snapshot)
    rate_timestamp = _snapshot_timestamp(snapshot)
    timestamp_note = rate_timestamp.isoformat() if isinstance(rate_timestamp, datetime) else "unknown"
    return UnifiedResult(
        source_type="online",
        result_type="live_rate",
        vendor_id=vendor.get("vendor_id"),
        name=f"{vendor.get('name', 'Unknown Vendor')} | {snapshot.get('script_name', 'Live Script')}",
        price=best_price,
        confidence=min(1.0, ((category_doc or {}).get("confidence_score", 50)) / 100.0),
        city=snapshot.get("city") or vendor.get("city"),
        channel_type=CHANNEL_TYPE_WEBSITE,
        url=snapshot.get("source_url") or vendor.get("website"),
        phone=vendor.get("phone_primary"),
        address=vendor.get("address"),
        delivery_time="Real-time website quote",
        notes=(
            f"Live dealer rate from {snapshot.get('source_name', 'website')} | "
            f"buy={snapshot.get('buy_rate')} sell={snapshot.get('sell_rate')} | "
            f"updated={timestamp_note}"
        ),
        gold_terms=_gold_terms_from_snapshot(snapshot, vendor, related_snapshots),
        timestamp=rate_timestamp if isinstance(rate_timestamp, datetime) else datetime.now(timezone.utc),
    )


async def _load_category_docs(query: StructuredQuery) -> list[dict[str, Any]]:
    buyer_type = infer_buyer_type(query)
    base_filter: dict[str, Any] = {
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "is_active": {"$ne": False},
        "blacklisted": {"$ne": True},
    }
    if buyer_type == "b2b":
        base_filter["serves_b2b"] = {"$ne": False}
    else:
        base_filter["serves_b2c"] = {"$ne": False}
    # Load ALL linked vendors, not just the top-N by confidence. The list is
    # confidence-ranked (so discovery/outreach still favour the strongest
    # vendors), but a low cap ranked them NATIONALLY — which starved local,
    # low-confidence vendors that DO publish fresh live rates out of the live
    # board entirely (e.g. a Rajkot vendor at confidence 25 crowded out by 200
    # higher-confidence Mumbai/Delhi vendors, so its ₹/10g never surfaced for a
    # Rajkot query). The city+freshness filters on the snapshot query below are
    # what actually scope the board, so include every linked vendor here.
    # length=None fetches all matches so this can't silently truncate as the
    # gold catalogue grows.
    return await zwig_vendor_category_links_collection.find(base_filter).sort("confidence_score", -1).to_list(length=None)


async def build_gold_query_plan(query: StructuredQuery, *, refresh: bool = True) -> GoldQueryPlan:
    city = _canonical_city(query.location)
    # `refresh` does a synchronous live HTTP fetch of up to LIVE_RATE_MAX_REFRESH_VENDORS
    # vendors, which can take many seconds. Callers with a hard response deadline
    # (the WhatsApp Flow data_exchange, which times out and shows "could not load
    # content") pass refresh=False and rely on the 6-min background poller to keep
    # snapshots fresh — the read path below is fast (indexed aggregation).
    live_refresh_summary = (
        await refresh_website_live_rates_for_query(query) if refresh else LiveRateRefreshSummary()
    )
    category_docs = await _load_category_docs(query)
    category_doc_map = {doc["vendor_id"]: doc for doc in category_docs}
    vendor_ids = list(category_doc_map)
    if not vendor_ids:
        return GoldQueryPlan(city=city, live_refresh_summary=live_refresh_summary)

    # These lookups are all keyed by vendor_id IN vendor_ids; their caps must
    # cover the full vendor set, otherwise a vendor is silently dropped from
    # vendor_map/channels and can never appear on the board — even with fresh
    # rates (this is what hid Rajkot's local vendor once the category cap was
    # raised: the 300-vendor cap here excluded it).
    vendor_fetch_limit = max(len(vendor_ids), 1)
    vendors = await zwig_vendors_collection.find(
        {"vendor_id": {"$in": vendor_ids}, "is_active": {"$ne": False}}
    ).to_list(length=vendor_fetch_limit)
    vendor_map = {vendor["vendor_id"]: vendor for vendor in vendors}

    channel_docs = await zwig_vendor_channels_collection.find(
        {"vendor_id": {"$in": vendor_ids}, "category_id": GOLD_BULLION_CATEGORY_ID, "is_active": {"$ne": False}}
    ).to_list(length=None)
    channels_by_vendor: dict[str, list[dict[str, Any]]] = {}
    for channel_doc in channel_docs:
        channels_by_vendor.setdefault(channel_doc["vendor_id"], []).append(channel_doc)

    snapshot_filter: dict[str, Any] = {
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "vendor_id": {"$in": vendor_ids},
        "is_active": {"$ne": False},
    }
    if city:
        # City is stored inconsistently cased across ingest sources (e.g.
        # "Rajkot" / "RAJKOT" / "rajkot" — the latter two held ~2/3 of Rajkot's
        # snapshots). An exact-case match hid most of a city's dealers from the
        # board. Match all casing variants (index-friendly $in). The refresh path
        # already compares city case-insensitively; this aligns the read path.
        snapshot_filter["city"] = {"$in": list({city, city.upper(), city.lower(), city.title()})}
    # Fetch the LATEST snapshot per (vendor, script) via aggregation rather than
    # "500 newest rows, then de-dupe in Python". The snapshots collection is
    # append-only, so a chatty streaming vendor (e.g. a socket feed writing a new
    # row every tick) accumulates thousands of rows and its recent stream fills
    # the whole flat 500-row window — starving every other vendor out of the
    # board. Grouping to one latest doc per (vendor, script) makes the board
    # robust to that regardless of how noisy a single feed is.
    pipeline: list[dict[str, Any]] = [
        {"$match": snapshot_filter},
        {"$sort": {"fetched_at": -1}},
        {"$group": {"_id": {"vendor_id": "$vendor_id", "script_name": "$script_name"}, "doc": {"$first": "$$ROOT"}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
        {"$sort": {"fetched_at": -1}},
        {"$limit": 1000},
    ]
    snapshot_docs = await zwig_live_rate_snapshots_collection.aggregate(
        pipeline, allowDiskUse=True
    ).to_list(length=1000)

    latest_snapshots: list[dict[str, Any]] = []
    # A "live" board must not surface multi-day-old snapshots as current rates
    # (e.g. a vendor whose feed died weeks ago showing "Updated 530 hr ago").
    # The aggregation already collapsed to one latest doc per (vendor, script);
    # here we just drop anything older than FRESH_SNAPSHOT_HOURS.
    fresh_cutoff = datetime.now(timezone.utc) - timedelta(hours=FRESH_SNAPSHOT_HOURS)
    stale_or_undated = 0
    for snapshot in snapshot_docs:
        ts = _snapshot_timestamp(snapshot)
        # ts is None when rate_timestamp/fetched_at is missing or not a datetime
        # (e.g. an ISO string) — treat as stale rather than let it headline as
        # fresh. Counted + logged below so a timestamp-format regression that
        # silently empties the board is diagnosable.
        if ts is None or ts < fresh_cutoff:
            stale_or_undated += 1
            continue
        latest_snapshots.append(snapshot)
    if stale_or_undated:
        logger.info(
            "gold board: dropped %d stale/undated snapshots (>%dh) for city=%s; %d fresh kept",
            stale_or_undated, FRESH_SNAPSHOT_HOURS, city or "all", len(latest_snapshots),
        )

    # Guardrails (same as the web board): drop non-retail rows (spot/futures,
    # USD, sub-bullion jewellery purities) and price outliers vs the national
    # bullion consensus, so a mis-parsed rate (e.g. an auto-adapter ₹82,652)
    # can't surface as the "best" rate here the way it can't on the web path.
    latest_snapshots = [s for s in latest_snapshots if is_retail_rate(s)]
    wanted_metal = infer_bullion_metal(f"{query.product} {query.raw_query}")
    latest_snapshots = [s for s in latest_snapshots if snapshot_metal(s) == wanted_metal]
    consensus = await national_bullion_consensus(GOLD_BULLION_CATEGORY_ID)
    # Always run the outlier filter: is_price_outlier applies the absolute
    # plausibility band even when `consensus` is None (trusted feeds stale), so
    # a wrong rate (e.g. an auto-adapter ₹82,652) can't leak here either.
    # Silver is a different price band — never judge it against gold ₹/10g.
    kept_snaps = [
        s for s in latest_snapshots
        if snapshot_metal(s) == "silver"
        or not is_price_outlier(snapshot_price_10g(s), consensus)
    ]
    # Never blank the board: if suppression would drop everything, keep the
    # unfiltered set (a thin/odd board is better than an empty one).
    if kept_snaps:
        latest_snapshots = kept_snaps

    latest_snapshots_by_vendor: dict[str, list[dict[str, Any]]] = {}
    for snapshot in latest_snapshots:
        vendor_id = snapshot.get("vendor_id")
        if vendor_id:
            latest_snapshots_by_vendor.setdefault(vendor_id, []).append(snapshot)

    # One row per DEALER, and that row is the dealer's FRESHEST rate — this is a
    # LIVE board, so a vendor's current tick must win over an older, slightly
    # cheaper script of theirs (otherwise a dealer can headline at "Rs X, updated
    # 37h ago" while a fresher rate of theirs is ignored). Tie-break by lower
    # normalized ₹/10g. Snapshots with NO usable rate are skipped outright — a
    # board row must carry a real price, so a fresh-but-priceless tick can never
    # displace a priced one. Other scripts stay in related_snapshots for the
    # detail view. Then order the dealers cheapest/best-first for the board.
    freshest_by_vendor: dict[str, dict[str, Any]] = {}
    freshest_scores: dict[str, tuple[float, float]] = {}
    for snapshot in latest_snapshots:
        vendor_id = snapshot.get("vendor_id")
        if not vendor_id:
            continue
        price = snapshot_price_10g(snapshot)
        if price is None:
            continue
        ts = _snapshot_timestamp(snapshot)
        score = (ts.timestamp() if ts else 0.0, -price)  # freshest, then cheapest
        if vendor_id not in freshest_by_vendor or score > freshest_scores[vendor_id]:
            freshest_by_vendor[vendor_id] = snapshot
            freshest_scores[vendor_id] = score

    dealer_snapshots = sorted(
        freshest_by_vendor.values(),
        key=lambda item: _live_snapshot_match_score(item, query),
    )

    live_results: list[UnifiedResult] = []
    same_city_live_vendors: list[VendorInfo] = []
    for snapshot in dealer_snapshots:
        vendor = vendor_map.get(snapshot.get("vendor_id"))
        if vendor is None:
            continue
        vendor_id = vendor["vendor_id"]
        category_doc = category_doc_map.get(vendor_id)
        live_results.append(
            _build_live_result(
                snapshot,
                vendor,
                category_doc,
                latest_snapshots_by_vendor.get(vendor_id),
            )
        )
        same_city_live_vendors.append(
            _vendor_info_from_docs(
                vendor,
                category_doc,
                channels_by_vendor.get(vendor_id, []),
                bucket="same_city_live",
            )
        )
        if len(live_results) >= MAX_LIVE_RESULTS:
            break

    high_confidence_live_vendors: list[VendorInfo] = []
    remote_confident_vendors: list[VendorInfo] = []
    seen_vendor_ids = {vendor.vendor_id for vendor in same_city_live_vendors if vendor.vendor_id}

    for category_doc in category_docs:
        vendor = vendor_map.get(category_doc["vendor_id"])
        if vendor is None or vendor["vendor_id"] in seen_vendor_ids:
            continue
        confidence_score = category_doc.get("confidence_score", 0)
        vendor_city = _canonical_city(vendor.get("city"))
        vendor_channels = channels_by_vendor.get(vendor["vendor_id"], [])
        has_live_channel = any(
            is_website_channel_type(channel.get("channel_type")) for channel in vendor_channels
        ) or bool(
            _channel_summary(vendor).get("live_script_available")
            or _channel_summary(vendor).get("website_available")
        )

        if has_live_channel and confidence_score >= HIGH_CONFIDENCE_THRESHOLD:
            high_confidence_live_vendors.append(
                _vendor_info_from_docs(vendor, category_doc, vendor_channels, bucket="high_confidence_live")
            )
            seen_vendor_ids.add(vendor["vendor_id"])
            continue

        if vendor_city and city and vendor_city.lower() == city.lower():
            continue
        if confidence_score >= HIGH_CONFIDENCE_THRESHOLD:
            remote_confident_vendors.append(
                _vendor_info_from_docs(vendor, category_doc, vendor_channels, bucket="remote_confident")
            )
            seen_vendor_ids.add(vendor["vendor_id"])

    # Float phone-reachable vendors to the front of each bucket before the cap.
    # Buckets can be much larger than MAX_DISCOVERED_VENDORS (e.g. ~120
    # high-confidence website vendors), and if callable vendors sit behind the
    # website-only ones the [:MAX_DISCOVERED_VENDORS] slice drops every vendor
    # the outreach/voice flows can actually call. sorted() is stable, so this
    # preserves the existing confidence/city ordering within each group.
    def _callable_first(vendors: list[VendorInfo]) -> list[VendorInfo]:
        return sorted(vendors, key=lambda vendor: 0 if vendor.call_available else 1)

    discovered_vendors = [
        *_callable_first(same_city_live_vendors),
        *_callable_first(high_confidence_live_vendors),
        *_callable_first(remote_confident_vendors),
    ][:MAX_DISCOVERED_VENDORS]

    outreach_vendors = [
        vendor
        for vendor in discovered_vendors
        if vendor.call_available
    ]

    return GoldQueryPlan(
        city=city,
        live_results=live_results,
        discovered_vendors=discovered_vendors,
        outreach_vendors=outreach_vendors,
        same_city_live_vendors=same_city_live_vendors,
        high_confidence_live_vendors=high_confidence_live_vendors,
        remote_confident_vendors=remote_confident_vendors,
        live_refresh_summary=live_refresh_summary,
    )
