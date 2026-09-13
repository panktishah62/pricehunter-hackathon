"""LLM polish for Pipecat campaign config.

Used to improve the spoken product name and the opening line for
chat-driven Pipecat calls. Falls back to deterministic templates if the
LLM is unavailable, slow, or errors out, so the call path is never
blocked by polish.

Design choices:
- Single OpenAI request per `(product, category)` pair; results cached in
  process for a short TTL so multiple vendors in the same search don't
  trigger more than one LLM call.
- Tight token budget; the result is always a small JSON object.
- Hard timeout so a slow OpenAI never gates the outbound call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60 * 60 * 6  # 6h
_TIMEOUT_SECONDS = 8.0
_MODEL = "gpt-4o-mini"

_cache: dict[str, tuple[float, "PolishedCampaign"]] = {}
_cache_lock = threading.Lock()
_inflight_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Future] = {}


class PolishedCampaign(BaseModel):
    """Subset of campaign fields the LLM may rewrite."""

    product_spoken: str
    opening_line: str
    requested_quantity: str | None = None
    goal: str | None = None


_SYSTEM_PROMPT = (
    "You craft short Hindi-English phone-call snippets for an outbound voice "
    "agent that asks Indian shop vendors for prices and stock. Return ONLY a "
    "small JSON object with keys: product_spoken, opening_line, "
    "requested_quantity, goal. Rules:\n"
    "- product_spoken: the shop-friendly way to say the product over a phone "
    "call. Spell out numbers (e.g. '128' becomes 'one twenty eight'), keep "
    "brand names natural, and translate or transliterate as needed so a "
    "Hindi speaker would understand.\n"
    "- opening_line: one sentence that starts with the English word 'Hello' "
    "then continues mostly in Hindi (Devanagari), under 20 words, asking "
    "whether the product is available; mention quantity if relevant.\n"
    "- requested_quantity: a short Hindi-English phrase like 'एक piece', "
    "'10 gram', 'do dozen'. Pick what fits the product. If the user payload "
    "includes a non-empty 'requested_quantity', preserve that amount and unit "
    "and only naturalize the wording (e.g. '10 pieces' -> '10 pieces', "
    "'1 litre' -> '1 litre'); do NOT replace it with a default. Otherwise "
    "default 'एक piece' for retail items.\n"
    "- goal: 1 short English phrase describing the call goal, e.g. 'gold "
    "price discovery', 'buying price discovery', 'wholesale silk inquiry'.\n"
    "Stay under 60 tokens total. Do not include greetings beyond the "
    "opening_line. Do not include any markdown or commentary."
)


def _cache_key(product: str, category: str | None) -> str:
    return f"{(category or '').strip().lower()}::{product.strip().lower()}"


def _cache_get(key: str) -> PolishedCampaign | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        timestamp, value = entry
        if time.time() - timestamp > _TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return value


def _cache_put(key: str, value: PolishedCampaign) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), value)


async def polish_campaign(
    *,
    product: str,
    category: str | None,
    fallback: PolishedCampaign,
) -> PolishedCampaign:
    """Return a polished campaign, falling back if the LLM is unavailable."""
    if not settings.openai_api_key:
        return fallback

    key = _cache_key(product, category)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # Coalesce concurrent requests for the same product so 8 vendors in
    # parallel only spend one OpenAI call.
    async with _inflight_lock:
        future = _inflight.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            _inflight[key] = future
            owner = True
        else:
            owner = False

    if not owner:
        try:
            return await asyncio.wait_for(future, timeout=_TIMEOUT_SECONDS + 1.0)
        except asyncio.TimeoutError:
            return fallback
        except Exception:
            return fallback

    try:
        polished = await asyncio.wait_for(
            _ask_openai(product=product, category=category, fallback=fallback),
            timeout=_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.info("Campaign polish timed out for %s; using fallback.", product)
        polished = fallback
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("Campaign polish failed for %s: %s", product, exc)
        polished = fallback
    else:
        _cache_put(key, polished)

    async with _inflight_lock:
        _inflight.pop(key, None)
        if not future.done():
            future.set_result(polished)
    return polished


async def _ask_openai(
    *,
    product: str,
    category: str | None,
    fallback: PolishedCampaign,
) -> PolishedCampaign:
    # Lazy import so non-Pipecat deployments don't pay the import cost.
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=_TIMEOUT_SECONDS)
    user_payload: dict[str, Any] = {
        "product": product,
        "category": category or "",
        "fallback": fallback.model_dump(),
    }
    response = await client.chat.completions.create(
        model=_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        max_tokens=200,
        temperature=0.2,
    )
    text = (response.choices[0].message.content or "").strip()
    if not text:
        return fallback
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip("`\n ")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip("`\n ")
        data = json.loads(cleaned)
    polished = PolishedCampaign(**{**fallback.model_dump(), **{k: v for k, v in data.items() if v}})
    return polished
