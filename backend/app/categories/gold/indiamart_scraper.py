from __future__ import annotations

import asyncio
import logging
from typing import Any

from apify_client import ApifyClient

from app.config import settings
from app.categories.gold.ingestion import city_from_address

logger = logging.getLogger(__name__)

APIFY_ACTOR_ID = "VIGpnYPrbIJgZp49F"
INDIAMART_LOCATIONS = ("Bangalore", "Mumbai")


def _parse_indiamart_item(item: dict[str, Any], location: str) -> dict[str, Any] | None:
    company_name = item.get("company_name")
    if not company_name:
        return None
    address = item.get("location", "") or item.get("address", "")
    city = item.get("city") or city_from_address(address) or location
    return {
        "name": company_name,
        "phone_number": item.get("phone"),
        "address": address,
        "city": city,
        "product_description": item.get("product_description") or item.get("product_name") or "",
        "listing_url": item.get("product_url") or item.get("catalog_url"),
        "gst_number": item.get("gst_number"),
        "rating": float(item["supplier_rating"]) if item.get("supplier_rating") else None,
        "review_count": int(item["rating_count"]) if item.get("rating_count") else 0,
    }


async def _run_indiamart_actor(location: str) -> list[dict[str, Any]]:
    def _sync_run() -> list[dict[str, Any]]:
        client = ApifyClient(settings.apify_api_token) if settings.apify_api_token else ApifyClient()
        run_input = {
            "queries": ["gold bullion"],
            "location": location,
            "maxResultsPerQuery": 30,
        }
        try:
            run = client.actor(APIFY_ACTOR_ID).call(run_input=run_input)
            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                return []
            items: list[dict[str, Any]] = []
            for item in client.dataset(dataset_id).iterate_items():
                parsed = _parse_indiamart_item(item, location)
                if parsed:
                    items.append(parsed)
            return items
        except Exception as exc:
            logger.warning("IndiaMART gold actor failed for %s: %s", location, exc)
            return []

    return await asyncio.to_thread(_sync_run)


async def scrape_indiamart_gold_bullion() -> list[dict[str, Any]]:
    if not settings.apify_api_token:
        raise RuntimeError("APIFY_API_TOKEN is required for IndiaMART gold scraping.")

    all_results = await asyncio.gather(
        *[_run_indiamart_actor(location) for location in INDIAMART_LOCATIONS],
        return_exceptions=True,
    )

    deduped: dict[tuple[str, str], dict[str, Any]] = {}
    for location, result in zip(INDIAMART_LOCATIONS, all_results, strict=False):
        if isinstance(result, Exception):
            logger.warning("IndiaMART gold scrape failed for %s: %s", location, result)
            continue
        for vendor in result:
            phone = vendor.get("phone_number") or ""
            key = (phone, location)
            deduped[key] = vendor
    vendors = list(deduped.values())
    logger.info("IndiaMART gold scraper parsed %s vendor(s)", len(vendors))
    return vendors
