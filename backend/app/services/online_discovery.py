from __future__ import annotations

import logging

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings
from app.models.schemas import StructuredQuery

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an e-commerce platform selection assistant for India.
Given a structured shopping query, pick the most relevant Indian e-commerce platforms and return ONLY valid JSON.

Supported platforms include:
- amazon_in
- flipkart
- jiomart
- bigbasket
- blinkit
- swiggy_instamart
- zepto
- croma
- reliance_digital
- meesho
- urban_company
- justdial
- indiamart

Pick up to 3 platforms based on the product category. Groceries should favor quick-commerce and grocery platforms. Electronics should favor Amazon India, Flipkart, Croma, and Reliance Digital. Clothing should favor Amazon India, Flipkart, and Meesho. Gold should favor trusted jewellery/bullion discovery through IndiaMART and Justdial. Services should favor Urban Company, Justdial, and IndiaMART style discovery.

Return JSON with this exact shape:
{
  "platforms": [
    {
      "platform_name": "Amazon India",
      "platform_id": "amazon_in",
      "search_query": "iPhone 15 Pro Max",
      "expected_category": "electronics"
    }
  ]
}
""".strip()


class PlatformStrategy(BaseModel):
    platform_name: str
    platform_id: str
    search_query: str
    expected_category: str


class DiscoveryResponse(BaseModel):
    platforms: list[PlatformStrategy]


def _fallback_platforms(query: StructuredQuery) -> DiscoveryResponse:
    category_map: dict[str, list[tuple[str, str]]] = {
        "groceries": [
            ("Blinkit", "blinkit"),
            ("Swiggy Instamart", "swiggy_instamart"),
            ("BigBasket", "bigbasket"),
            ("JioMart", "jiomart"),
            ("Amazon India", "amazon_in"),
        ],
        "electronics": [
            ("Amazon India", "amazon_in"),
            ("Flipkart", "flipkart"),
            ("Croma", "croma"),
            ("Reliance Digital", "reliance_digital"),
        ],
        "clothing": [
            ("Flipkart", "flipkart"),
            ("Amazon India", "amazon_in"),
            ("Meesho", "meesho"),
        ],
        "medicine": [
            ("Blinkit", "blinkit"),
            ("Swiggy Instamart", "swiggy_instamart"),
            ("Amazon India", "amazon_in"),
        ],
        "gold": [
            ("IndiaMART", "indiamart"),
            ("Justdial", "justdial"),
            ("Amazon India", "amazon_in"),
        ],
        "hardware": [
            ("Amazon India", "amazon_in"),
            ("Flipkart", "flipkart"),
            ("JioMart", "jiomart"),
        ],
        "services": [
            ("Urban Company", "urban_company"),
            ("Justdial", "justdial"),
            ("IndiaMART", "indiamart"),
        ],
    }
    selected = category_map.get(query.category, category_map["electronics"])[:3]
    return DiscoveryResponse(
        platforms=[
            PlatformStrategy(
                platform_name=name,
                platform_id=platform_id,
                search_query=query.product,
                expected_category=query.category,
            )
            for name, platform_id in selected
        ]
    )


async def discover_platforms(query: StructuredQuery) -> list[PlatformStrategy]:
    logger.info("Discovering platforms for category=%s product=%s", query.category, query.product)
    if not settings.openai_api_key:
        logger.info("OpenAI API key missing; using fallback platform selection.")
        return _fallback_platforms(query).platforms

    client = AsyncOpenAI(api_key=settings.openai_api_key)

    try:
        response = await client.responses.parse(
            model=settings.openai_model,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": query.model_dump_json()},
            ],
            text_format=DiscoveryResponse,
            temperature=0,
        )
        parsed = response.output_parsed
        if parsed is None:
            raise ValueError("OpenAI returned no parsed platform strategy payload.")
        if not parsed.platforms:
            raise ValueError("No platforms returned by LLM.")
        deduped: list[PlatformStrategy] = []
        seen_ids: set[str] = set()
        for strategy in parsed.platforms:
            if strategy.platform_id in seen_ids:
                continue
            deduped.append(strategy)
            seen_ids.add(strategy.platform_id)
            if len(deduped) == 3:
                break
        if not deduped:
            raise ValueError("No usable platforms returned by LLM.")
        return deduped
    except Exception as exc:  # pragma: no cover - depends on external API
        logger.warning("Online discovery failed, using fallback platforms: %s", exc)
        return _fallback_platforms(query).platforms
