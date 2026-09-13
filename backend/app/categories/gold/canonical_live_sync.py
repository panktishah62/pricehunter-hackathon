from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from pymongo import UpdateOne

from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID
from app.config import settings
from app.database import (
    current_price_snapshots_collection,
    price_observations_collection,
    supplier_vendors_collection,
    vendor_identity_links_collection,
    vendor_offerings_collection,
    zwig_live_rate_snapshots_collection,
)
from app.services import algolia_search

logger = logging.getLogger(__name__)

SOURCE_SYSTEM = "zwig_vendors"
SOURCE_VENDOR_COLLECTION = "vendors"
GOLD_CATEGORY_ID = "gold"
GOLD_SUBCATEGORY_ID = "gold_bullion"
GOLD_TAXONOMY_PATH = [GOLD_CATEGORY_ID, GOLD_SUBCATEGORY_ID]
GOLD_TAXONOMY_LABELS = ["Gold", "Gold Bullion"]
SNAPSHOT_TTL = timedelta(hours=6)
TOKEN_RE = re.compile(r"[a-z0-9]+")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _slug(value: Any, *, max_length: int = 96) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", _clean(value).lower()).strip("-")
    return (normalized or "unknown")[:max_length].strip("-") or "unknown"


def _tokens(*values: Any) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            text = " ".join(str(item) for item in value if item is not None)
        elif isinstance(value, dict):
            text = " ".join(f"{key} {item}" for key, item in value.items() if item is not None)
        else:
            text = str(value)
        for token in TOKEN_RE.findall(text.lower()):
            if len(token) > 1:
                seen.setdefault(token, None)
    return list(seen.keys())


def _public_url(*values: Any) -> str | None:
    for value in values:
        raw = _clean(value)
        if not raw:
            continue
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        return parsed.geturl()
    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return fallback


def _price(row: dict[str, Any]) -> float | None:
    return _as_number(row.get("sell_rate")) or _as_number(row.get("buy_rate"))


def _unit(row: dict[str, Any]) -> str | None:
    raw = _clean(row.get("unit"))
    if raw:
        return raw
    quantity = _as_number(row.get("quantity_grams"))
    if quantity:
        return f"{quantity:g} GM"
    return None


def _product_id(script_name: str) -> str:
    return f"{GOLD_SUBCATEGORY_ID}:{_slug(script_name)}"


def _offering_id(canonical_vendor_id: str, script_name: str) -> str:
    return f"gold_live:{_slug(canonical_vendor_id)}:{_slug(script_name)}"


def _observation_id(canonical_vendor_id: str, script_name: str, observed_at: datetime) -> str:
    stamp = int(observed_at.timestamp() * 1000)
    return f"gold_live:{_slug(canonical_vendor_id)}:{_slug(script_name)}:{stamp}"


def _service_locations(snapshot: dict[str, Any], vendor: dict[str, Any] | None) -> list[dict[str, Any]]:
    city = _clean(snapshot.get("city")) or _clean((vendor or {}).get("city"))
    if not city:
        return []
    return [{"city": city, "raw": city}]


def _build_docs(
    snapshot: dict[str, Any],
    canonical_vendor_id: str,
    vendor: dict[str, Any] | None,
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    script_name = _clean(snapshot.get("script_name")) or "Gold live rate"
    # Query-time freshness is based on when our adapter fetched the dealer board.
    # Some dealer feeds expose their own board timestamp, which can lag even
    # when the latest rate was fetched successfully.
    observed_at = _as_datetime(snapshot.get("fetched_at") or snapshot.get("rate_timestamp"), now)
    price = _price(snapshot)
    offering_id = _offering_id(canonical_vendor_id, script_name)
    product_id = _product_id(script_name)
    source_url = _public_url((vendor or {}).get("website"), snapshot.get("source_url"))
    unit = _unit(snapshot)
    attributes = {
        "metal": "gold",
        "product_type": snapshot.get("product_type") or "gold",
        "script_name": script_name,
        "purity": snapshot.get("purity"),
        "quantity_grams": _as_number(snapshot.get("quantity_grams")),
        "unit": unit,
        "buy_rate": _as_number(snapshot.get("buy_rate")),
        "sell_rate": _as_number(snapshot.get("sell_rate")),
        "rate_timestamp": observed_at,
        "source_rate_timestamp": _as_datetime(snapshot.get("rate_timestamp"), observed_at),
        "legacy_category_id": snapshot.get("category_id"),
        "legacy_vendor_id": snapshot.get("vendor_id"),
        "source_name": snapshot.get("source_name"),
        "raw_source_url": snapshot.get("source_url"),
    }
    attributes = {key: value for key, value in attributes.items() if value not in (None, "", [])}
    common = {
        "vendor_id": canonical_vendor_id,
        "offering_id": offering_id,
        "product_id": product_id,
        "category_id": GOLD_CATEGORY_ID,
        "subcategory_id": GOLD_SUBCATEGORY_ID,
        "canonical_category_id": GOLD_CATEGORY_ID,
        "canonical_subcategory_id": GOLD_SUBCATEGORY_ID,
        "taxonomy_node_id": GOLD_SUBCATEGORY_ID,
        "taxonomy_path": GOLD_TAXONOMY_PATH,
        "taxonomy_path_labels": GOLD_TAXONOMY_LABELS,
    }
    offering_doc = {
        **common,
        "taxonomy_root_id": GOLD_CATEGORY_ID,
        "product_name_raw": script_name,
        "normalized_product_name": script_name.lower(),
        "normalized_tokens": _tokens(
            script_name,
            "gold bullion live rate",
            snapshot.get("purity"),
            unit,
            (vendor or {}).get("name"),
            snapshot.get("city"),
        ),
        "attributes": attributes,
        "source": "gold_live_rate_snapshot",
        "source_url": source_url,
        "evidence_score": 0.92,
        "supply_confidence": 0.92,
        "taxonomy_confidence": 0.98,
        "needs_taxonomy_review": False,
        "service_locations": _service_locations(snapshot, vendor),
        "moq": None,
        "last_seen_at": observed_at,
        "is_active": True,
        "updated_at": now,
        "created_at": now,
    }
    observation_doc = {
        **common,
        "observation_id": _observation_id(canonical_vendor_id, script_name, observed_at),
        "query_id": None,
        "price": price,
        "currency": "INR",
        "unit": unit,
        "availability": True,
        "source_type": "live_api",
        "source_url": source_url,
        "notes": f"Latest Gold live-rate script: {script_name}",
        "confidence": 0.92,
        "observed_at": observed_at,
        "expires_at": observed_at + SNAPSHOT_TTL,
        "created_at": now,
        "buy_rate": _as_number(snapshot.get("buy_rate")),
        "sell_rate": _as_number(snapshot.get("sell_rate")),
    }
    snapshot_doc = {
        **common,
        "latest_price": price,
        "best_recent_price": price,
        "currency": "INR",
        "unit": unit,
        "availability": True,
        "confidence": 0.92,
        "last_observed_at": observed_at,
        "source_type": "live_api",
        "source_url": source_url,
        "updated_at": now,
        "created_at": now,
        "buy_rate": _as_number(snapshot.get("buy_rate")),
        "sell_rate": _as_number(snapshot.get("sell_rate")),
        "rate_timestamp": observed_at,
        "source_rate_timestamp": _as_datetime(snapshot.get("rate_timestamp"), observed_at),
    }
    return offering_doc, observation_doc, snapshot_doc


async def _latest_gold_snapshots(source_vendor_ids: list[str]) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "vendor_id": {"$in": source_vendor_ids},
                "is_active": {"$ne": False},
                "$and": [
                    {
                        "$or": [
                            {"product_type": {"$regex": "^gold$", "$options": "i"}},
                            {"script_name": {"$regex": "gold|bullion", "$options": "i"}},
                            {"category_id": GOLD_BULLION_CATEGORY_ID},
                        ]
                    },
                    {
                        "$nor": [
                            {"product_type": {"$regex": "silver|xag", "$options": "i"}},
                            {"script_name": {"$regex": "silver|xag", "$options": "i"}},
                        ]
                    },
                    {"$or": [{"sell_rate": {"$ne": None}}, {"buy_rate": {"$ne": None}}]},
                ],
            }
        },
        {"$sort": {"fetched_at": -1, "rate_timestamp": -1}},
        {
            "$group": {
                "_id": {"vendor_id": "$vendor_id", "script_name": "$script_name"},
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]
    return await zwig_live_rate_snapshots_collection.aggregate(pipeline, allowDiskUse=True).to_list(length=2000)


async def sync_gold_live_rates_to_canonical(source_vendor_ids: list[str]) -> dict[str, int]:
    unique_source_vendor_ids = sorted({_clean(item) for item in source_vendor_ids if _clean(item)})
    if not unique_source_vendor_ids:
        return {"source_vendors": 0, "snapshots": 0, "offerings": 0, "current_snapshots": 0}

    links_cursor = vendor_identity_links_collection.find(
        {
            "source_system": SOURCE_SYSTEM,
            "source_collection": SOURCE_VENDOR_COLLECTION,
            "source_vendor_id": {"$in": unique_source_vendor_ids},
            "canonical_vendor_id": {"$ne": None},
        },
        {"_id": 0, "source_vendor_id": 1, "canonical_vendor_id": 1},
    )
    links = {
        row.get("source_vendor_id"): row.get("canonical_vendor_id")
        async for row in links_cursor
        if row.get("source_vendor_id") and row.get("canonical_vendor_id")
    }
    if not links:
        return {"source_vendors": len(unique_source_vendor_ids), "snapshots": 0, "offerings": 0, "current_snapshots": 0}

    snapshots = await _latest_gold_snapshots(list(links.keys()))
    canonical_vendor_ids = sorted({links.get(row.get("vendor_id")) for row in snapshots if links.get(row.get("vendor_id"))})
    vendor_docs = {
        row.get("vendor_id"): row
        async for row in supplier_vendors_collection.find({"vendor_id": {"$in": canonical_vendor_ids}}, {"_id": 0})
        if row.get("vendor_id")
    }

    now = _now()
    offering_ops: list[UpdateOne] = []
    observation_ops: list[UpdateOne] = []
    current_snapshot_ops: list[UpdateOne] = []
    algolia_records: list[dict[str, Any]] = []

    for snapshot in snapshots:
        canonical_vendor_id = links.get(snapshot.get("vendor_id"))
        price = _price(snapshot)
        if not canonical_vendor_id or price is None or price <= 0:
            continue
        vendor = vendor_docs.get(canonical_vendor_id)
        offering_doc, observation_doc, current_snapshot_doc = _build_docs(snapshot, canonical_vendor_id, vendor, now)
        algolia_records.append(
            algolia_search.build_offering_record(
                offering_doc,
                vendor=vendor,
                snapshot=current_snapshot_doc,
            )
        )
        offering_ops.append(
            UpdateOne(
                {"offering_id": offering_doc["offering_id"]},
                {
                    "$set": {key: value for key, value in offering_doc.items() if key != "created_at"},
                    "$setOnInsert": {"created_at": offering_doc["created_at"]},
                },
                upsert=True,
            )
        )
        observation_ops.append(
            UpdateOne(
                {"observation_id": observation_doc["observation_id"]},
                {"$setOnInsert": observation_doc},
                upsert=True,
            )
        )
        current_snapshot_ops.append(
            UpdateOne(
                {"vendor_id": current_snapshot_doc["vendor_id"], "offering_id": current_snapshot_doc["offering_id"]},
                {
                    "$set": {key: value for key, value in current_snapshot_doc.items() if key != "created_at"},
                    "$setOnInsert": {"created_at": current_snapshot_doc["created_at"]},
                    "$unset": {"call_id": ""},
                },
                upsert=True,
            )
        )

    if offering_ops:
        await vendor_offerings_collection.bulk_write(offering_ops, ordered=False)
    if observation_ops:
        await price_observations_collection.bulk_write(observation_ops, ordered=False)
    if current_snapshot_ops:
        await current_price_snapshots_collection.bulk_write(current_snapshot_ops, ordered=False)
    if algolia_records and algolia_search.is_enabled(write=True):
        try:
            await algolia_search.save_records(settings.algolia_offerings_index, algolia_records, action="addObject")
        except Exception as exc:  # pragma: no cover - network/index availability
            logger.warning("Gold live Algolia sync failed for %d record(s): %s", len(algolia_records), exc)

    return {
        "source_vendors": len(unique_source_vendor_ids),
        "snapshots": len(snapshots),
        "offerings": len(offering_ops),
        "current_snapshots": len(current_snapshot_ops),
    }
