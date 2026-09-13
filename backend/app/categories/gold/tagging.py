from __future__ import annotations

import json
import logging
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.database import zwig_vendor_category_links_collection, zwig_vendors_collection
from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID, now_utc

logger = logging.getLogger(__name__)

_openai_client: AsyncOpenAI | None = None

TAGGING_SYSTEM_PROMPT = """You classify Indian gold bullion vendors.
Return JSON only with this schema:
{
  "vendor_type": "primary_dealer" | "secondary_dealer" | "jewellery_wholesale" | "retail_only" | "scrap_buyer",
  "specialisation": ["bars", "coins", "22K", "24K", "scrap"],
  "serves_b2b": true,
  "serves_b2c": true,
  "fulfillment_type": "local_only" | "city_wide" | "regional" | "pan_india",
  "min_order_grams_estimate": 1 | 10 | 100 | 1000,
  "confidence_in_classification": "high" | "medium" | "low"
}

Use the vendor name, address, source, and description to infer whether this vendor is mainly bullion trade, wholesale jewellery, retail-only, or scrap-related.
Bullion exchange language, refinery terms, bars/coins, and trading wording usually imply B2B.
Retail jewellery wording implies serves_b2c.
"""


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


async def tag_gold_bullion_vendors() -> dict[str, int]:
    cursor = zwig_vendor_category_links_collection.find({"category_id": GOLD_BULLION_CATEGORY_ID})
    category_docs = await cursor.to_list(length=5000)
    processed = 0
    flagged = 0

    client = _get_openai_client()
    for category_doc in category_docs:
        vendor = await zwig_vendors_collection.find_one({"vendor_id": category_doc["vendor_id"]})
        if vendor is None:
            continue

        source_names = [source.get("source_name") for source in vendor.get("sources") or [] if source.get("source_name")]
        user_prompt = {
            "name": vendor.get("name"),
            "address": vendor.get("address"),
            "description": vendor.get("specialisation") or vendor.get("sources", [{}])[-1].get("product_description"),
            "source": source_names,
        }
        try:
            response = await client.chat.completions.create(
                model=settings.gold_tagging_model,
                messages=[
                    {"role": "system", "content": TAGGING_SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
                max_tokens=300,
            )
            payload = json.loads(response.choices[0].message.content.strip())
        except Exception as exc:
            logger.warning("Gold vendor tagging failed for %s: %s", category_doc["vendor_id"], exc)
            continue

        confidence_label = payload.get("confidence_in_classification", "low")
        manual_review = confidence_label == "low"
        if manual_review:
            flagged += 1

        await zwig_vendor_category_links_collection.update_one(
            {"vendor_id": category_doc["vendor_id"], "category_id": GOLD_BULLION_CATEGORY_ID},
            {
                "$set": {
                    "vendor_type": payload.get("vendor_type"),
                    "specialisation": payload.get("specialisation") or [],
                    "serves_b2b": payload.get("serves_b2b"),
                    "serves_b2c": payload.get("serves_b2c"),
                    "fulfillment_type": payload.get("fulfillment_type"),
                    "min_order_grams_estimate": payload.get("min_order_grams_estimate"),
                    "classification_confidence": confidence_label,
                    "manual_review_required": manual_review,
                    "updated_at": now_utc(),
                }
            },
        )
        processed += 1

    return {"processed": processed, "flagged": flagged}
