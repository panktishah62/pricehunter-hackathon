from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from app.database import (
    zwig_live_rate_snapshots_collection,
    zwig_vendor_channels_collection,
    zwig_vendor_interactions_collection,
    zwig_vendors_collection,
)

CHANNEL_TYPE_WEBSITE = "website"
# Legacy alias kept so older imports keep working while channel_type storage moves to "website".
CHANNEL_TYPE_LIVE_SCRIPT = "live_script"
CHANNEL_TYPE_WHATSAPP = "whatsapp"
CHANNEL_TYPE_CALL = "call"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_website_url(url: str | None) -> str:
    if not url:
        return ""
    raw = url.strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, flags=re.IGNORECASE):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def website_host(url: str | None) -> str:
    normalized = normalize_website_url(url)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    return parsed.netloc.lower().removeprefix("www.")


def build_vendor_id(
    *,
    phone_number: str | None = None,
    website_url: str | None = None,
    vendor_name: str | None = None,
) -> str:
    digits = "".join(char for char in str(phone_number or "") if char.isdigit())
    if digits:
        return f"vendor:phone:{digits}"
    host = website_host(website_url)
    if host:
        return f"vendor:web:{host}"
    slug = re.sub(r"[^a-z0-9]+", "-", (vendor_name or "unknown-vendor").strip().lower()).strip("-")
    return f"vendor:name:{slug or 'unknown-vendor'}"


def build_channel_key(channel_type: str, channel_value: str | None) -> str:
    if is_website_channel_type(channel_type):
        return website_host(channel_value) or normalize_website_url(channel_value)
    if channel_type in {CHANNEL_TYPE_CALL, CHANNEL_TYPE_WHATSAPP}:
        return "".join(char for char in str(channel_value or "") if char.isdigit())
    return (channel_value or "").strip().lower()


def normalize_channel_type(channel_type: str | None) -> str:
    normalized = (channel_type or "").strip().lower()
    if normalized == CHANNEL_TYPE_LIVE_SCRIPT:
        return CHANNEL_TYPE_WEBSITE
    return normalized


def is_website_channel_type(channel_type: str | None) -> bool:
    normalized = normalize_channel_type(channel_type)
    return normalized == CHANNEL_TYPE_WEBSITE


def build_website_channel_metadata(
    *,
    source_name: str | None = None,
    supports_live_rates: bool = True,
    fetch_adapter: str | None = None,
    fetch_config: dict[str, Any] | None = None,
    script_names: list[str] | None = None,
    city_scope: list[str] | None = None,
    additional_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "supports_live_rates": supports_live_rates,
    }
    if source_name:
        metadata["source_name"] = source_name
    if fetch_adapter:
        metadata["fetch_adapter"] = fetch_adapter
    if fetch_config:
        metadata["fetch_config"] = fetch_config
    if script_names:
        metadata["script_names"] = [name for name in script_names if name]
    if city_scope:
        metadata["city_scope"] = [city for city in city_scope if city]
    if additional_metadata:
        metadata.update(additional_metadata)
    return metadata


async def refresh_vendor_channel_summary(vendor_id: str) -> None:
    channels = await zwig_vendor_channels_collection.find(
        {"vendor_id": vendor_id, "is_active": {"$ne": False}}
    ).to_list(length=50)
    channel_types = sorted({channel.get("channel_type") for channel in channels if channel.get("channel_type")})
    website_channels = [channel for channel in channels if is_website_channel_type(channel.get("channel_type"))]
    whatsapp_channels = [channel for channel in channels if channel.get("channel_type") == CHANNEL_TYPE_WHATSAPP]
    call_channels = [channel for channel in channels if channel.get("channel_type") == CHANNEL_TYPE_CALL]

    preferred_contact_channel = None
    if whatsapp_channels:
        preferred_contact_channel = CHANNEL_TYPE_WHATSAPP
    elif call_channels:
        preferred_contact_channel = CHANNEL_TYPE_CALL
    elif website_channels:
        preferred_contact_channel = CHANNEL_TYPE_WEBSITE

    update_fields: dict[str, Any] = {
        "channel_summary": {
            "channel_types": channel_types,
            "website_available": bool(website_channels),
            "website_count": len(website_channels),
            # Legacy summary fields kept for compatibility with existing planners/UI.
            "live_script_available": bool(website_channels),
            "live_script_count": len(website_channels),
            "whatsapp_available": bool(whatsapp_channels),
            "call_available": bool(call_channels),
            "preferred_contact_channel": preferred_contact_channel,
        },
        "updated_at": now_utc(),
    }
    website = next(
        (channel.get("channel_value") for channel in website_channels if channel.get("channel_value")),
        None,
    )
    if website:
        update_fields["website"] = website
    await zwig_vendors_collection.update_one(
        {"vendor_id": vendor_id},
        {"$set": update_fields},
    )


async def upsert_vendor_channel(
    *,
    vendor_id: str,
    category_id: str,
    channel_type: str,
    channel_value: str,
    city: str | None = None,
    priority: int = 100,
    is_active: bool = True,
    metadata: dict[str, Any] | None = None,
) -> None:
    normalized_channel_type = normalize_channel_type(channel_type)
    channel_key = build_channel_key(normalized_channel_type, channel_value)
    if not channel_key:
        return

    now = now_utc()
    existing_channel = await zwig_vendor_channels_collection.find_one(
        {
            "vendor_id": vendor_id,
            "category_id": category_id,
            "channel_type": normalized_channel_type,
            "channel_key": channel_key,
        }
    )
    existing_metadata = (existing_channel or {}).get("metadata") or {}
    merged_metadata = {**existing_metadata, **(metadata or {})}
    await zwig_vendor_channels_collection.update_one(
        {
            "vendor_id": vendor_id,
            "category_id": category_id,
            "channel_type": normalized_channel_type,
            "channel_key": channel_key,
        },
        {
            "$set": {
                "channel_value": channel_value,
                "city": city,
                "priority": priority,
                "is_active": is_active,
                "metadata": merged_metadata,
                "updated_at": now,
            },
            "$setOnInsert": {
                "created_at": now,
            },
        },
        upsert=True,
    )
    await refresh_vendor_channel_summary(vendor_id)


async def record_live_rate_snapshot(
    *,
    vendor_id: str,
    category_id: str,
    city: str,
    script_name: str,
    source_name: str,
    source_url: str | None = None,
    buy_rate: float | None = None,
    sell_rate: float | None = None,
    purity: str | None = None,
    product_type: str | None = None,
    quantity_grams: float | None = None,
    unit: str | None = None,
    rate_timestamp: datetime | None = None,
    is_active: bool = True,
    raw_payload: dict[str, Any] | None = None,
) -> None:
    fetched_at = now_utc()
    await zwig_live_rate_snapshots_collection.insert_one(
        {
            "vendor_id": vendor_id,
            "category_id": category_id,
            "city": city,
            "script_name": script_name,
            "source_name": source_name,
            "source_url": source_url,
            "buy_rate": buy_rate,
            "sell_rate": sell_rate,
            "purity": purity,
            "product_type": product_type,
            "quantity_grams": quantity_grams,
            "unit": unit,
            "rate_timestamp": rate_timestamp or fetched_at,
            "fetched_at": fetched_at,
            "is_active": is_active,
            "raw_payload": raw_payload or {},
        }
    )
    if source_url:
        await upsert_vendor_channel(
            vendor_id=vendor_id,
            category_id=category_id,
            channel_type=CHANNEL_TYPE_WEBSITE,
            channel_value=source_url,
            city=city,
            priority=1,
            is_active=is_active,
            metadata=build_website_channel_metadata(
                source_name=source_name,
                script_names=[script_name],
                additional_metadata={"source_url": source_url},
            ),
        )
    else:
        await refresh_vendor_channel_summary(vendor_id)


async def record_vendor_interaction(
    *,
    vendor_id: str,
    category_id: str,
    interaction_type: str,
    query_id: str | None,
    outcome: str,
    channel_type: str | None = None,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> None:
    timestamp = occurred_at or now_utc()
    await zwig_vendor_interactions_collection.insert_one(
        {
            "vendor_id": vendor_id,
            "category_id": category_id,
            "query_id": query_id,
            "interaction_type": interaction_type,
            "channel_type": channel_type,
            "outcome": outcome,
            "payload": payload or {},
            "occurred_at": timestamp,
            "created_at": timestamp,
        }
    )
