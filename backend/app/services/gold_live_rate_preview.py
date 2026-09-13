from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID
from app.database import zwig_live_rate_snapshots_collection, zwig_vendors_collection
from app.models.schemas import GoldLiveRatePreviewResponse, GoldLiveRateRow
from app.services.gold_rate_rules import FRESH_SNAPSHOT_HOURS, is_retail_rate

MAX_LIVE_RATE_ROWS = 150


def _fresh_filter() -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(hours=FRESH_SNAPSHOT_HOURS)
    return {
        "$or": [
            {"rate_timestamp": {"$gte": cutoff}},
            {"rate_timestamp": {"$exists": False}, "fetched_at": {"$gte": cutoff}},
        ]
    }


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def _row_from_snapshot(snapshot: dict[str, Any]) -> GoldLiveRateRow:
    raw_payload = snapshot.get("raw_payload") or {}
    timestamp = _timestamp(snapshot.get("rate_timestamp") or snapshot.get("fetched_at"))
    return GoldLiveRateRow(
        script_name=str(snapshot.get("script_name") or "Live Script"),
        buy_rate=snapshot.get("buy_rate"),
        sell_rate=snapshot.get("sell_rate"),
        day_high=raw_payload.get("day_high"),
        day_low=raw_payload.get("day_low"),
        purity=snapshot.get("purity"),
        product_type=snapshot.get("product_type"),
        quantity_grams=snapshot.get("quantity_grams"),
        unit=snapshot.get("unit"),
        source_name=snapshot.get("source_name"),
        source_url=snapshot.get("source_url"),
        updated_at=timestamp.isoformat() if timestamp else None,
    )


async def fetch_gold_live_rate_preview(
    *,
    vendor_id: str | None = None,
    source_url: str | None = None,
    city: str | None = None,
) -> GoldLiveRatePreviewResponse:
    if not vendor_id and not source_url:
        raise ValueError("vendor_id or source_url is required.")

    resolved_vendor_id = vendor_id
    if not resolved_vendor_id and source_url:
        owner_snapshot = await zwig_live_rate_snapshots_collection.find_one(
            {
                "category_id": GOLD_BULLION_CATEGORY_ID,
                "source_url": source_url,
                "is_active": {"$ne": False},
            },
            {"vendor_id": 1},
            sort=[("fetched_at", -1)],
        )
        if owner_snapshot and owner_snapshot.get("vendor_id"):
            resolved_vendor_id = str(owner_snapshot["vendor_id"])

    query: dict[str, Any] = {
        "category_id": GOLD_BULLION_CATEGORY_ID,
        "is_active": {"$ne": False},
        **_fresh_filter(),
    }
    if resolved_vendor_id:
        query["vendor_id"] = resolved_vendor_id
    elif source_url:
        query["source_url"] = source_url
    if city:
        query["city"] = {"$regex": f"^{re.escape(city)}$", "$options": "i"}

    projection = {
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
    }
    snapshots = await zwig_live_rate_snapshots_collection.find(
        query,
        projection,
    ).sort("fetched_at", -1).to_list(length=2000)
    if not snapshots and city:
        query.pop("city", None)
        snapshots = await zwig_live_rate_snapshots_collection.find(
            query,
            projection,
        ).sort("fetched_at", -1).to_list(length=2000)

    latest_by_script: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots:
        # Drop non-retail lines (spot/futures/reference AND bare unit-less "GOLD"
        # lines that carry a USD/spot value) — same gate as the search path.
        if not is_retail_rate(snapshot):
            continue
        key = (str(snapshot.get("script_name") or ""), str(snapshot.get("source_url") or ""))
        if key in latest_by_script:
            continue
        if snapshot.get("buy_rate") is None and snapshot.get("sell_rate") is None:
            continue
        latest_by_script[key] = snapshot

    latest_snapshots = list(latest_by_script.values())
    latest_snapshots.sort(
        key=lambda item: (
            str(item.get("product_type") or ""),
            float(item.get("sell_rate") or item.get("buy_rate") or 10**18),
            str(item.get("script_name") or ""),
        )
    )
    latest_snapshots = latest_snapshots[:MAX_LIVE_RATE_ROWS]

    resolved_vendor_id = resolved_vendor_id or (str(latest_snapshots[0].get("vendor_id")) if latest_snapshots else None)
    vendor = None
    if resolved_vendor_id:
        vendor = await zwig_vendors_collection.find_one(
            {"vendor_id": resolved_vendor_id},
            {"vendor_id": 1, "name": 1, "city": 1, "website": 1},
        )

    latest_timestamp = None
    for snapshot in latest_snapshots:
        candidate = _timestamp(snapshot.get("rate_timestamp") or snapshot.get("fetched_at"))
        if candidate and (latest_timestamp is None or candidate > latest_timestamp):
            latest_timestamp = candidate

    return GoldLiveRatePreviewResponse(
        vendor_id=resolved_vendor_id,
        vendor_name=(vendor or {}).get("name"),
        city=city or (vendor or {}).get("city") or (latest_snapshots[0].get("city") if latest_snapshots else None),
        source_url=(vendor or {}).get("website") or source_url or (latest_snapshots[0].get("source_url") if latest_snapshots else None),
        updated_at=latest_timestamp.isoformat() if latest_timestamp else None,
        rates=[_row_from_snapshot(snapshot) for snapshot in latest_snapshots],
    )
