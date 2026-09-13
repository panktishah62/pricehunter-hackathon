"""Marketing outreach context for WhatsApp template → Get Live Quotes on-ramp.

Stores per-phone campaign context at send time. On CTA tap, injects the stored
product query into the normal WhatsApp bot path (no parallel chatbot).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.database import db

logger = logging.getLogger(__name__)

marketing_contexts_collection = db["marketing_contexts"]
marketing_opt_outs_collection = db["marketing_opt_outs"]

GET_QUOTES_CTAS = frozenset(
    {
        "get live quotes",
        "get live quote",
        "get quotes",
        "get quote",
    }
)
OPT_OUT_CTAS = frozenset(
    {
        "stop updates",
        "stop",
        "unsubscribe",
        "opt out",
        "opt-out",
    }
)

HANDOFF_MESSAGE = (
    "You can now search for any other product or service you like — "
    "just type it here on WhatsApp."
)

# Maps purchase tag / industry → template vars + the query fired on CTA.
# Extend this table as you open new industries; template stays the same.
AUDIENCE_MAP: dict[str, dict[str, str]] = {
    "gold_bullion": {
        "role": "gold jeweller",
        "product_label": "gold bullion",
        "query_template": "gold live rates in {city}",
    },
    "silver_bullion": {
        "role": "silver jeweller",
        "product_label": "silver bullion",
        "query_template": "silver rates in {city}",
    },
    "gold": {
        "role": "gold jeweller",
        "product_label": "gold bullion",
        "query_template": "gold live rates in {city}",
    },
    "pharma": {
        "role": "pharma manufacturer",
        "product_label": "pharma API",
        "query_template": "sultamycin in {city}",
    },
    "electronics": {
        "role": "electronics supplier",
        "product_label": "electronic components",
        "query_template": "electronic components in {city}",
    },
    "industrial": {
        "role": "industrial manufacturer",
        "product_label": "industrial materials",
        "query_template": "industrial raw materials in {city}",
    },
    "home_kitchen": {
        "role": "home & kitchen supplier",
        "product_label": "home and kitchen products",
        "query_template": "kitchenware in {city}",
    },
    "medical_devices": {
        "role": "medical devices supplier",
        "product_label": "medical devices",
        "query_template": "medical devices in {city}",
    },
}

BUSINESS_TYPE_HINTS = (
    (re.compile(r"manufacturer|fabricat|exporter", re.I), "manufacturer"),
    (re.compile(r"retail", re.I), "retailer"),
    (re.compile(r"wholesale|distributor|trader", re.I), "wholesaler"),
)


def normalize_wa_id(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("91") and len(digits) > 10:
        digits = digits[-10:]
    if re.fullmatch(r"[6-9]\d{9}", digits or ""):
        return f"91{digits}"
    return re.sub(r"\D", "", phone or "")


def is_get_quotes_cta(text: str) -> bool:
    return (text or "").strip().lower() in GET_QUOTES_CTAS


def is_opt_out_cta(text: str) -> bool:
    return (text or "").strip().lower() in OPT_OUT_CTAS


def business_type_label(business_type: str | None) -> str:
    raw = (business_type or "").strip()
    if not raw:
        return "business"
    for pattern, label in BUSINESS_TYPE_HINTS:
        if pattern.search(raw):
            return label
    return "business"


def resolve_audience(purchase: list[str] | str | None, industry: str | None) -> dict[str, str] | None:
    tags: list[str] = []
    if isinstance(purchase, list):
        tags.extend(str(t).strip() for t in purchase if t)
    elif purchase:
        tags.append(str(purchase).strip())
    if industry:
        tags.append(str(industry).strip())
    for tag in tags:
        if tag in AUDIENCE_MAP:
            return dict(AUDIENCE_MAP[tag])
    return None


def build_product_query(*, query_template: str, city: str | None, product_override: str | None = None) -> str:
    if product_override and product_override.strip():
        base = product_override.strip()
        city_clean = (city or "").strip()
        if city_clean and city_clean.lower() not in {"your city", "unknown", ""}:
            if city_clean.lower() not in base.lower():
                return f"{base} in {city_clean}"
        return base
    city_clean = (city or "").strip() or "India"
    return query_template.format(city=city_clean)


def template_body_params(vendor: dict[str, Any], audience: dict[str, str]) -> tuple[str, str]:
    """Return ({{1}}, {{2}}) for zwig_marketing_1-style templates."""
    role = audience["role"]
    btype = business_type_label(vendor.get("business_type"))
    # "gold jeweller" / "pharma manufacturer" — prefer mapped role; append type when useful
    if btype != "business" and btype not in role:
        role_text = f"{role} ({btype})"
    else:
        role_text = role
    return role_text, audience["product_label"]


async def is_opted_out(wa_id: str) -> bool:
    doc = await marketing_opt_outs_collection.find_one({"wa_id": normalize_wa_id(wa_id)})
    return doc is not None


async def record_opt_out(wa_id: str, *, reason: str = "stop_cta") -> None:
    now = datetime.now(timezone.utc)
    key = normalize_wa_id(wa_id)
    await marketing_opt_outs_collection.update_one(
        {"wa_id": key},
        {"$set": {"wa_id": key, "reason": reason, "updated_at": now}, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )


async def upsert_context(
    *,
    wa_id: str,
    product_query: str,
    campaign_id: str,
    template_name: str,
    vendor_id: str | None = None,
    industry: str | None = None,
    purchase: list[str] | None = None,
    city: str | None = None,
    template_params: dict[str, str] | None = None,
    ttl_days: int = 30,
) -> None:
    now = datetime.now(timezone.utc)
    key = normalize_wa_id(wa_id)
    await marketing_contexts_collection.update_one(
        {"wa_id": key},
        {
            "$set": {
                "wa_id": key,
                "product_query": product_query,
                "campaign_id": campaign_id,
                "template_name": template_name,
                "vendor_id": vendor_id,
                "industry": industry,
                "purchase": purchase or [],
                "city": city,
                "template_params": template_params or {},
                "status": "pending",
                "updated_at": now,
                "expires_at": now + timedelta(days=ttl_days),
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )


def search_started_copy(product_query: str) -> str:
    """Immediate confirmation after Get Live quotes — sent before vendor lookup."""
    query = " ".join((product_query or "").split()).rstrip(".")
    if not query:
        query = "your request"
    return f"Noted: {query}. Contacting all the suppliers for best prices."


def _pending_context_filter(wa_id: str, now: datetime) -> dict[str, Any]:
    return {
        "wa_id": normalize_wa_id(wa_id),
        "status": "pending",
        "$or": [{"expires_at": {"$exists": False}}, {"expires_at": {"$gt": now}}],
    }


async def peek_pending_context(wa_id: str) -> dict[str, Any] | None:
    """Load pending campaign context without consuming it."""
    now = datetime.now(timezone.utc)
    return await marketing_contexts_collection.find_one(_pending_context_filter(wa_id, now))


async def mark_context_queued(wa_id: str, *, search_id: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    key = normalize_wa_id(wa_id)
    update: dict[str, Any] = {"status": "queued", "queued_at": now, "updated_at": now}
    if search_id:
        update["search_id"] = search_id
    await marketing_contexts_collection.update_one(
        {
            "wa_id": key,
            "status": {"$in": ["pending", "queued"]},
            "$or": [{"expires_at": {"$exists": False}}, {"expires_at": {"$gt": now}}],
        },
        {"$set": update},
    )


async def restore_pending_context(wa_id: str) -> None:
    """If the search never queued, let Get Live quotes retry."""
    now = datetime.now(timezone.utc)
    key = normalize_wa_id(wa_id)
    await marketing_contexts_collection.update_one(
        {"wa_id": key, "status": {"$in": ["queued", "consumed"]}},
        {"$set": {"status": "pending", "updated_at": now}, "$unset": {"queued_at": "", "consumed_at": ""}},
    )


async def consume_pending_context(wa_id: str) -> dict[str, Any] | None:
    """Atomically take a pending, non-expired context for this phone."""
    now = datetime.now(timezone.utc)
    key = normalize_wa_id(wa_id)
    doc = await marketing_contexts_collection.find_one_and_update(
        {
            "wa_id": key,
            "status": "pending",
            "$or": [{"expires_at": {"$exists": False}}, {"expires_at": {"$gt": now}}],
        },
        {"$set": {"status": "consumed", "consumed_at": now, "updated_at": now}},
    )
    return doc


async def ensure_indexes() -> None:
    await marketing_contexts_collection.create_index("wa_id", unique=True)
    await marketing_contexts_collection.create_index([("status", 1), ("expires_at", 1)])
    await marketing_opt_outs_collection.create_index("wa_id", unique=True)
