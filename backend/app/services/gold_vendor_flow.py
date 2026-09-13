from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID
from app.categories.gold.metal import dedupe_rate_snapshots
from app.database import (
    zwig_live_rate_snapshots_collection,
    zwig_vendor_category_links_collection,
    zwig_vendor_channels_collection,
    zwig_vendors_collection,
)
from app.models.schemas import StructuredQuery, UnifiedResult, VendorInfo
from app.services.gold_clusters import resolve_cluster
from app.services.gold_rate_rules import (
    FRESH_SNAPSHOT_HOURS,
    TRUSTED_RATE_SOURCES,
    consensus_10g,
    is_price_outlier,
    national_bullion_consensus,
    is_retail_rate as _is_retail_rate,
    normalize_to_10g as _normalize_to_10g,
)

VENDORS = zwig_vendors_collection
VENDOR_CATEGORIES = zwig_vendor_category_links_collection
VENDOR_CHANNELS = zwig_vendor_channels_collection
LIVE_RATE_SNAPSHOTS = zwig_live_rate_snapshots_collection

logger = logging.getLogger(__name__)

GOLD_CATEGORY_ID = GOLD_BULLION_CATEGORY_ID
PRIMARY_RESULT_LIMIT = 8
LIVE_RESULT_LIMIT = 5
MORE_RESULT_LIMIT = 6
REMOTE_CONFIDENCE_THRESHOLD = 80

# Tier -> human label. 0 = exact city, 1 = same regional cluster, 2 = remote.
_BUCKET_LABELS = {
    0: "Popular local bullion vendor",
    1: "Nearby bullion vendor",
    2: "High-confidence remote bullion vendor",
}


@dataclass
class GoldSelection:
    primary_results: list[UnifiedResult]
    reserve_results: list[UnifiedResult]
    discovered_vendors: list[VendorInfo]
    candidate_vendor_names: list[str]
    shown_vendor_ids: list[str]


def is_gold_query(query: StructuredQuery) -> bool:
    if query.category != "gold":
        return False
    text = f"{query.product} {query.raw_query}".lower()
    return "jewel" not in text and "jewellery" not in text and "jewelry" not in text


def _normalize_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _extract_city(location: str | None) -> str:
    if not location:
        return "unknown"
    base = location.split(",")[0].strip()
    return base or "unknown"


def _location_candidates(location: str | None) -> list[str]:
    if not location:
        return ["unknown"]
    parts = [part.strip() for part in location.split(",") if part.strip()]
    ordered = []
    for part in [parts[0], *parts[1:]] if parts else [location]:
        if part and part not in ordered:
            ordered.append(part)
    return ordered or ["unknown"]


def _city_regex(city: str) -> dict[str, Any]:
    return {"$regex": f"^{re.escape(city)}$", "$options": "i"}


def _pick_price(snapshot: dict[str, Any]) -> float | None:
    for key in ("sell_rate", "buy_rate"):
        value = snapshot.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def _normalized_price(snapshot: dict[str, Any]) -> float | None:
    return _normalize_to_10g(_pick_price(snapshot), snapshot)


def _snapshot_timestamp(snapshot: dict[str, Any]) -> datetime:
    value = snapshot.get("rate_timestamp") or snapshot.get("fetched_at")
    if not isinstance(value, datetime):
        return datetime.min
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


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
        "updated_at": timestamp.isoformat() if timestamp != datetime.min else None,
    }


def _gold_terms_from_snapshot(
    snapshot: dict[str, Any],
    vendor: dict[str, Any],
    related_snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timestamp = _snapshot_timestamp(snapshot)
    website = vendor.get("website")
    return {
        "vendor_name": vendor.get("name"),
        "script_name": snapshot.get("script_name") or "Live Script",
        "buy_rate": snapshot.get("buy_rate"),
        "sell_rate": snapshot.get("sell_rate"),
        "purity": snapshot.get("purity"),
        "quantity_grams": snapshot.get("quantity_grams"),
        "unit": snapshot.get("unit"),
        "source_name": snapshot.get("source_name"),
        "source_url": snapshot.get("source_url") or website,
        "updated_at": timestamp.isoformat() if timestamp != datetime.min else None,
        "rates": [
            _snapshot_rate_row(item)
            for item in dedupe_rate_snapshots(related_snapshots or [snapshot])
        ],
    }


def _fresh_snapshot_filter() -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=FRESH_SNAPSHOT_HOURS)
    return {
        "$or": [
            {"rate_timestamp": {"$gte": cutoff}},
            {"rate_timestamp": {"$exists": False}, "fetched_at": {"$gte": cutoff}},
        ]
    }


def _best_snapshot_per_vendor(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for snapshot in snapshots:
        vendor_id = str(snapshot.get("vendor_id") or "")
        if not vendor_id:
            continue
        if _pick_price(snapshot) is None:
            continue
        # Only consider genuine INR retail rates — drop spot/futures/bare lines.
        if not _is_retail_rate(snapshot):
            continue
        grouped.setdefault(vendor_id, []).append(snapshot)

    best_by_vendor: dict[str, dict[str, Any]] = {}
    for vendor_id, vendor_snaps in grouped.items():
        vendor_snaps.sort(
            key=lambda item: (
                _snapshot_timestamp(item),
                -(_normalized_price(item) or float("inf")),
            ),
            reverse=True,
        )
        freshest_time = _snapshot_timestamp(vendor_snaps[0])
        freshest_candidates = [
            snap for snap in vendor_snaps if _snapshot_timestamp(snap) == freshest_time
        ] or vendor_snaps[:1]
        freshest_candidates.sort(key=lambda item: _normalized_price(item) or float("inf"))
        best_by_vendor[vendor_id] = freshest_candidates[0]
    return best_by_vendor


def _compute_popularity(
    vendor: dict[str, Any],
    category_doc: dict[str, Any] | None,
    channels: list[dict[str, Any]],
    live_script_count: int,
) -> float:
    vendor_conf = float(vendor.get("confidence_score") or 0)
    category_conf = float((category_doc or {}).get("confidence_score") or 0)
    has_call = any(channel.get("channel_type") == "call" for channel in channels)
    has_website = any(channel.get("channel_type") == "website" for channel in channels)
    supports_live = any(
        (channel.get("metadata") or {}).get("supports_live_rates") for channel in channels
    )

    popularity = vendor_conf * 0.45 + category_conf * 0.35
    if has_call:
        popularity += 8
    if has_website:
        popularity += 6
    if supports_live:
        popularity += 8
    popularity += min(live_script_count, 10) * 1.2
    return max(0.0, min(popularity, 99.0))


def _build_vendor_result(
    *,
    vendor: dict[str, Any],
    channels: list[dict[str, Any]],
    popularity: float,
    live_snapshot: dict[str, Any] | None = None,
    related_snapshots: list[dict[str, Any]] | None = None,
    bucket_label: str = "Popular local bullion vendor",
) -> UnifiedResult:
    website = next((channel for channel in channels if channel.get("channel_type") == "website"), None)
    call = next((channel for channel in channels if channel.get("channel_type") == "call"), None)
    notes_parts: list[str] = []
    price = None

    if live_snapshot is not None:
        price = _normalize_to_10g(_pick_price(live_snapshot), live_snapshot)
        script_name = live_snapshot.get("script_name")
        if script_name:
            notes_parts.append(f"Live script: {script_name}")
        if price is not None:
            notes_parts.append(f"Rate ₹{price:,.0f}/10g")
        snapshot_city = live_snapshot.get("city")
        if snapshot_city:
            notes_parts.append(f"City: {snapshot_city}")
    else:
        notes_parts.append(bucket_label)

    notes_parts.append(f"Popularity score: {popularity:.0f}/99")

    return UnifiedResult(
        source_type="offline",
        result_type="live_rate" if live_snapshot is not None else "gold_vendor",
        vendor_id=str(vendor.get("vendor_id") or "") or None,
        name=str(vendor.get("name") or "Unknown vendor"),
        price=price,
        confidence=round(popularity / 100.0, 2),
        city=str(vendor.get("city") or "") or None,
        url=website.get("channel_value") if website else None,
        phone=call.get("channel_value") if call else vendor.get("phone_primary"),
        address=str(vendor.get("city") or vendor.get("address") or ""),
        notes=" | ".join(notes_parts),
        gold_terms=_gold_terms_from_snapshot(live_snapshot, vendor, related_snapshots) if live_snapshot else None,
    )


def _call_phone(vendor: dict[str, Any], channels: list[dict[str, Any]]) -> str:
    return next(
        (
            str(channel.get("channel_value") or "")
            for channel in channels
            if channel.get("channel_type") == "call" and channel.get("channel_value")
        ),
        str(vendor.get("phone_primary") or ""),
    )


def _is_actionable_result(result: UnifiedResult) -> bool:
    return result.price is not None or bool(result.phone)


def _suppress_price_outliers(
    best_by_vendor: dict[str, dict[str, Any]],
    consensus: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Drop gold rates that deviate from the consensus by more than the
    tolerance.

    `consensus` is normally the national bullion consensus (robust, well
    sampled). If not provided, it falls back to the median of CURATED bespoke
    rates within this candidate set — the generic "auto" adapter is the
    unreliable one we're guarding, so it can't drag the anchor. Even with NO
    trustworthy consensus we still run the loop: the absolute plausibility band
    in `is_price_outlier` catches clearly-broken rates on its own, so a wrong
    rate can't leak while the trusted feeds are stale. We never blank the board
    (fall back to the unfiltered set). Silver rows are left untouched (they're
    not comparable to a gold consensus)."""
    if consensus is None:
        trusted_prices = [
            _normalized_price(snap)
            for snap in best_by_vendor.values()
            if snap.get("source_name") in TRUSTED_RATE_SOURCES
        ]
        consensus = consensus_10g(trusted_prices)
    kept: dict[str, dict[str, Any]] = {}
    for vendor_id, snap in best_by_vendor.items():
        if snap.get("product_type") == "silver":
            kept[vendor_id] = snap  # don't judge silver against a gold consensus
            continue
        norm = _normalized_price(snap)
        if is_price_outlier(norm, consensus):
            logger.info(
                "gold board: suppressing price outlier vendor=%s rate=%s vs consensus=%s",
                vendor_id, norm, consensus,
            )
            continue
        kept[vendor_id] = snap
    return kept or best_by_vendor


async def _national_bullion_consensus() -> float | None:
    """Delegates to the shared consensus (single source of truth + cache)."""
    return await national_bullion_consensus(GOLD_CATEGORY_ID)


async def select_gold_vendors(
    query: StructuredQuery,
    *,
    exclude_vendor_ids: set[str] | None = None,
    primary_limit: int = PRIMARY_RESULT_LIMIT,
    live_limit: int = LIVE_RESULT_LIMIT,
    reserve_limit: int = MORE_RESULT_LIMIT,
) -> GoldSelection:
    category_docs = await VENDOR_CATEGORIES.find(
        {"category_id": GOLD_CATEGORY_ID},
        {"vendor_id": 1, "confidence_score": 1, "vendor_type": 1, "serves_b2b": 1},
    ).to_list(length=5000)
    category_by_vendor = {str(doc["vendor_id"]): doc for doc in category_docs if doc.get("vendor_id")}
    vendor_ids = list(category_by_vendor)
    if not vendor_ids:
        return GoldSelection([], [], [], [], [])

    exclude_vendor_ids = exclude_vendor_ids or set()
    selected_city = _extract_city(query.location)
    vendors: list[dict[str, Any]] = []
    for candidate_city in _location_candidates(query.location):
        city_filter = _city_regex(candidate_city)
        candidate_vendors = await VENDORS.find(
            {"vendor_id": {"$in": vendor_ids}, "city": city_filter, "is_active": {"$ne": False}},
            {"vendor_id": 1, "name": 1, "city": 1, "confidence_score": 1, "phone_primary": 1},
        ).to_list(length=1000)
        if candidate_vendors:
            selected_city = candidate_city
            vendors = candidate_vendors
            break

    local_vendors_by_id = {str(vendor["vendor_id"]): vendor for vendor in vendors if vendor.get("vendor_id")}
    local_vendor_ids = [vendor_id for vendor_id in local_vendors_by_id if vendor_id not in exclude_vendor_ids]
    local_vendor_id_set = set(local_vendor_ids)

    # Tier 1 (vicinity): vendors in the same regional cluster as the searcher.
    # A buyer in Jamnagar (few local suppliers) sees the Rajkot/Saurashtra
    # cluster; a buyer in Thrissur sees Coimbatore, and so on. Resolution is
    # table-first with a Google Places geocode + nearest-cluster fallback.
    resolved_cluster = await resolve_cluster(query.location)
    vicinity_vendors_by_id: dict[str, dict[str, Any]] = {}
    if resolved_cluster:
        vicinity_vendors = await VENDORS.find(
            {
                "vendor_id": {"$in": vendor_ids},
                "cluster_id": resolved_cluster,
                "is_active": {"$ne": False},
            },
            {"vendor_id": 1, "name": 1, "city": 1, "confidence_score": 1, "phone_primary": 1},
        ).to_list(length=1000)
        vicinity_vendors_by_id = {
            str(vendor["vendor_id"]): vendor
            for vendor in vicinity_vendors
            if vendor.get("vendor_id")
            and str(vendor["vendor_id"]) not in local_vendor_id_set
            and str(vendor["vendor_id"]) not in exclude_vendor_ids
        }
    vicinity_vendor_ids = list(vicinity_vendors_by_id)
    vicinity_vendor_id_set = set(vicinity_vendor_ids)

    # Tier 2 (remote): high-confidence vendors anywhere else.
    remote_category_vendor_ids = [
        vendor_id
        for vendor_id, category_doc in category_by_vendor.items()
        if vendor_id not in local_vendor_id_set
        and vendor_id not in vicinity_vendor_id_set
        and vendor_id not in exclude_vendor_ids
        and float(category_doc.get("confidence_score") or 0) >= REMOTE_CONFIDENCE_THRESHOLD
    ]
    remote_vendors = await VENDORS.find(
        {
            "vendor_id": {"$in": remote_category_vendor_ids},
            "is_active": {"$ne": False},
        },
        {"vendor_id": 1, "name": 1, "city": 1, "confidence_score": 1, "phone_primary": 1},
    ).to_list(length=1000)
    remote_vendors_by_id = {str(vendor["vendor_id"]): vendor for vendor in remote_vendors if vendor.get("vendor_id")}
    remote_vendor_ids = list(remote_vendors_by_id)

    vendors_by_id = {**local_vendors_by_id, **vicinity_vendors_by_id, **remote_vendors_by_id}
    candidate_vendor_ids = [*local_vendor_ids, *vicinity_vendor_ids, *remote_vendor_ids]
    if not candidate_vendor_ids:
        return GoldSelection([], [], [], [], [])

    # Tier per vendor: 0 = exact city, 1 = vicinity cluster, 2 = remote.
    tier_by_vendor: dict[str, int] = {}
    for vendor_id in local_vendor_ids:
        tier_by_vendor[vendor_id] = 0
    for vendor_id in vicinity_vendor_ids:
        tier_by_vendor[vendor_id] = 1
    for vendor_id in remote_vendor_ids:
        tier_by_vendor[vendor_id] = 2

    channels = await VENDOR_CHANNELS.find(
        {"vendor_id": {"$in": candidate_vendor_ids}, "is_active": {"$ne": False}},
        {"vendor_id": 1, "channel_type": 1, "channel_value": 1, "metadata": 1, "priority": 1},
    ).to_list(length=5000)
    channels_by_vendor: dict[str, list[dict[str, Any]]] = {}
    for channel in channels:
        vendor_id = str(channel.get("vendor_id") or "")
        if vendor_id:
            channels_by_vendor.setdefault(vendor_id, []).append(channel)

    snapshots = await LIVE_RATE_SNAPSHOTS.find(
        {
            "vendor_id": {"$in": candidate_vendor_ids},
            "category_id": GOLD_CATEGORY_ID,
            **_fresh_snapshot_filter(),
        },
        {
            "vendor_id": 1,
            "city": 1,
            "script_name": 1,
            "buy_rate": 1,
            "sell_rate": 1,
            "source_name": 1,
            "source_url": 1,
            "purity": 1,
            "product_type": 1,
            "quantity_grams": 1,
            "unit": 1,
            "raw_payload": 1,
            "rate_timestamp": 1,
            "fetched_at": 1,
        },
    ).to_list(length=10000)

    snapshots_by_vendor: dict[str, list[dict[str, Any]]] = {}
    live_count_by_vendor: dict[str, int] = {}
    for snapshot in snapshots:
        vendor_id = str(snapshot.get("vendor_id") or "")
        if vendor_id:
            snapshots_by_vendor.setdefault(vendor_id, []).append(snapshot)
            live_count_by_vendor[vendor_id] = live_count_by_vendor.get(vendor_id, 0) + 1
    for vendor_snapshots in snapshots_by_vendor.values():
        vendor_snapshots.sort(key=_snapshot_timestamp, reverse=True)
    best_snapshot_by_vendor = _best_snapshot_per_vendor(snapshots)
    best_snapshot_by_vendor = _suppress_price_outliers(
        best_snapshot_by_vendor, consensus=await _national_bullion_consensus()
    )

    popularity_by_vendor: dict[str, float] = {}
    for vendor_id in candidate_vendor_ids:
        popularity_by_vendor[vendor_id] = _compute_popularity(
            vendors_by_id[vendor_id],
            category_by_vendor.get(vendor_id),
            channels_by_vendor.get(vendor_id, []),
            live_count_by_vendor.get(vendor_id, 0),
        )

    live_vendor_ids = [vendor_id for vendor_id in candidate_vendor_ids if vendor_id in best_snapshot_by_vendor]
    live_vendor_ids.sort(
        key=lambda vendor_id: (
            tier_by_vendor.get(vendor_id, 2),
            _normalized_price(best_snapshot_by_vendor[vendor_id]) or float("inf"),
            -(popularity_by_vendor.get(vendor_id, 0.0)),
        )
    )
    popular_vendor_ids = [
        vendor_id
        for vendor_id in candidate_vendor_ids
        if vendor_id not in best_snapshot_by_vendor
        and _call_phone(vendors_by_id[vendor_id], channels_by_vendor.get(vendor_id, []))
    ]
    popular_vendor_ids.sort(
        key=lambda vendor_id: (
            tier_by_vendor.get(vendor_id, 2),
            -(popularity_by_vendor.get(vendor_id, 0.0)),
        )
    )

    def _result_for(vendor_id: str) -> UnifiedResult:
        return _build_vendor_result(
            vendor=vendors_by_id[vendor_id],
            channels=channels_by_vendor.get(vendor_id, []),
            popularity=popularity_by_vendor.get(vendor_id, 0.0),
            live_snapshot=best_snapshot_by_vendor.get(vendor_id),
            related_snapshots=snapshots_by_vendor.get(vendor_id),
            bucket_label=_BUCKET_LABELS.get(tier_by_vendor.get(vendor_id, 2), _BUCKET_LABELS[2]),
        )

    primary_vendor_ids: list[str] = []
    for vendor_id in live_vendor_ids[:live_limit]:
        result = _result_for(vendor_id)
        if _is_actionable_result(result):
            primary_vendor_ids.append(vendor_id)
    for vendor_id in popular_vendor_ids:
        if len(primary_vendor_ids) >= primary_limit:
            break
        result = _result_for(vendor_id)
        if _is_actionable_result(result):
            primary_vendor_ids.append(vendor_id)

    reserve_vendor_ids = [
        vendor_id
        for vendor_id in (live_vendor_ids[live_limit:] + popular_vendor_ids)
        if vendor_id not in primary_vendor_ids and _is_actionable_result(_result_for(vendor_id))
    ][:reserve_limit]

    primary_results = [_result_for(vendor_id) for vendor_id in primary_vendor_ids]
    reserve_results = [_result_for(vendor_id) for vendor_id in reserve_vendor_ids]
    shown_vendor_ids = [*primary_vendor_ids, *reserve_vendor_ids]
    discovered_vendors = [
        VendorInfo(
            vendor_id=vendor_id,
            name=vendors_by_id[vendor_id].get("name", ""),
            phone=_call_phone(vendors_by_id[vendor_id], channels_by_vendor.get(vendor_id, [])),
            address=str(vendors_by_id[vendor_id].get("city") or ""),
            city=str(vendors_by_id[vendor_id].get("city") or "") or None,
            is_mock=False,
        )
        for vendor_id in shown_vendor_ids
        if _call_phone(vendors_by_id[vendor_id], channels_by_vendor.get(vendor_id, []))
    ]
    candidate_vendor_names = [vendors_by_id[vendor_id].get("name", "") for vendor_id in candidate_vendor_ids]
    return GoldSelection(
        primary_results=primary_results,
        reserve_results=reserve_results,
        discovered_vendors=discovered_vendors,
        candidate_vendor_names=[name for name in candidate_vendor_names if name],
        shown_vendor_ids=primary_vendor_ids,
    )


async def find_vendor_price(query: StructuredQuery, vendor_hint: str) -> UnifiedResult | None:
    normalized_hint = _normalize_text(vendor_hint)
    if not normalized_hint:
        return None

    category_docs = await VENDOR_CATEGORIES.find(
        {"category_id": GOLD_CATEGORY_ID, "is_active": {"$ne": False}},
        {"vendor_id": 1, "confidence_score": 1, "vendor_type": 1, "serves_b2b": 1},
    ).to_list(length=5000)
    category_by_vendor = {str(doc["vendor_id"]): doc for doc in category_docs if doc.get("vendor_id")}
    vendor_ids = list(category_by_vendor)
    if not vendor_ids:
        return None

    vendors: list[dict[str, Any]] = []
    selected_city = _extract_city(query.location)
    for candidate_city in _location_candidates(query.location):
        candidate_vendors = await VENDORS.find(
            {
                "vendor_id": {"$in": vendor_ids},
                "city": _city_regex(candidate_city),
                "is_active": {"$ne": False},
            },
            {"vendor_id": 1, "name": 1, "city": 1, "confidence_score": 1, "phone_primary": 1},
        ).to_list(length=1000)
        if candidate_vendors:
            vendors = candidate_vendors
            selected_city = candidate_city
            break

    matches: list[dict[str, Any]] = []
    hint_tokens = set(normalized_hint.split())
    for vendor in vendors:
        vendor_name = str(vendor.get("name") or "")
        normalized_name = _normalize_text(vendor_name)
        if not normalized_name:
            continue
        name_tokens = set(normalized_name.split())
        overlap = len(hint_tokens & name_tokens) / max(len(name_tokens), 1)
        if normalized_hint in normalized_name or overlap >= 0.5:
            matches.append(vendor)

    if not matches:
        return None

    vendor = sorted(matches, key=lambda item: float(item.get("confidence_score") or 0), reverse=True)[0]
    vendor_id = str(vendor.get("vendor_id") or "")
    channels = await VENDOR_CHANNELS.find(
        {
            "vendor_id": vendor_id,
            "category_id": GOLD_CATEGORY_ID,
            "is_active": {"$ne": False},
        },
        {"channel_type": 1, "channel_value": 1, "metadata": 1},
    ).to_list(length=20)
    snapshots = await LIVE_RATE_SNAPSHOTS.find(
        {
            "vendor_id": vendor_id,
            "category_id": GOLD_CATEGORY_ID,
            "city": _city_regex(selected_city),
            **_fresh_snapshot_filter(),
        },
        {
            "vendor_id": 1,
            "city": 1,
            "script_name": 1,
            "buy_rate": 1,
            "sell_rate": 1,
            "source_name": 1,
            "source_url": 1,
            "purity": 1,
            "product_type": 1,
            "quantity_grams": 1,
            "unit": 1,
            "raw_payload": 1,
            "rate_timestamp": 1,
            "fetched_at": 1,
        },
    ).sort("rate_timestamp", -1).to_list(length=20)
    best_snapshot_by_vendor = _best_snapshot_per_vendor(snapshots)
    snapshot = best_snapshot_by_vendor.get(vendor_id)
    popularity = _compute_popularity(vendor, category_by_vendor.get(vendor_id), channels, len(snapshots))
    return _build_vendor_result(
        vendor=vendor,
        channels=channels,
        popularity=popularity,
        live_snapshot=snapshot,
        related_snapshots=snapshots,
    )
