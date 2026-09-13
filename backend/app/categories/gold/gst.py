from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.database import zwig_vendor_category_links_collection, zwig_vendors_collection
from app.categories.gold.ingestion import now_utc

logger = logging.getLogger(__name__)


async def verify_gst_number(gst_number: str) -> dict[str, Any]:
    if not settings.gst_verification_api_url:
        raise RuntimeError("GST_VERIFICATION_API_URL is not configured.")

    headers = {"Content-Type": "application/json"}
    if settings.gst_verification_api_key:
        headers["Authorization"] = f"Bearer {settings.gst_verification_api_key}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            settings.gst_verification_api_url,
            params={"gstin": gst_number},
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


async def run_gst_verification_pass() -> dict[str, int]:
    cursor = zwig_vendors_collection.find({"gst_number": {"$exists": True, "$ne": None, "$ne": ""}})
    vendors = await cursor.to_list(length=5000)
    verified = 0
    deactivated = 0
    skipped = 0

    for vendor in vendors:
        gst_number = vendor.get("gst_number")
        if not gst_number:
            skipped += 1
            continue
        try:
            payload = await verify_gst_number(gst_number)
        except Exception as exc:
            logger.warning("GST verification skipped for %s: %s", vendor.get("vendor_id"), exc)
            skipped += 1
            continue

        active = bool(payload.get("active") or payload.get("status") == "active")
        now = now_utc()
        if active:
            await zwig_vendors_collection.update_one(
                {"vendor_id": vendor["vendor_id"]},
                {
                    "$set": {
                        "verification.gst_verified": True,
                        "verification.gst_verified_at": now,
                        "updated_at": now,
                    },
                    "$inc": {"confidence_score": 10},
                },
            )
            await zwig_vendor_category_links_collection.update_many(
                {"vendor_id": vendor["vendor_id"]},
                {
                    "$inc": {"confidence_score": 10},
                    "$set": {"updated_at": now},
                },
            )
            verified += 1
        else:
            await zwig_vendors_collection.update_one(
                {"vendor_id": vendor["vendor_id"]},
                {
                    "$set": {
                        "is_active": False,
                        "updated_at": now,
                    }
                },
            )
            await zwig_vendor_category_links_collection.update_many(
                {"vendor_id": vendor["vendor_id"]},
                {"$set": {"is_active": False, "updated_at": now}},
            )
            deactivated += 1

    return {"verified": verified, "deactivated": deactivated, "skipped": skipped}
