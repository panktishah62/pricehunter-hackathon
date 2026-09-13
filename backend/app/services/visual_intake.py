"""Phase 4: image-led product identification, confirmation, and search launch."""

from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI

from app.categories.router import enrich_query_route
from app.config import settings
from app.models.schemas import StructuredQuery, VisualProductIntent
from app.services import query_structurer, search_specs

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE_THRESHOLD = 0.75

_CONFIRM_YES = re.compile(
    r"^(yes|yeah|yep|yup|correct|right|ok|okay|sure|go ahead|search|"
    r"yes[,\s]+search|that(?:'s| is) (?:right|correct)|looks good|"
    r"yes[,\s]+that(?:'s| is) (?:it|right|correct))\.?$",
    re.IGNORECASE,
)
_CONFIRM_NO = re.compile(
    r"^(no|nope|wrong|not right|not this|incorrect|"
    r"no[,\s]+that(?:'s| is) wrong|let me correct|different product)\.?$",
    re.IGNORECASE,
)
_SEARCH_FOR_PREFIX = re.compile(r"^search for\s+", re.IGNORECASE)

_openai_client: AsyncOpenAI | None = None


def _client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


async def classify_availability_intent(caption: str | None) -> bool:
    """LLM: is the buyer asking whether something like this product is available?"""
    text = (caption or "").strip()
    if not text:
        return True  # image-only upload implies availability search
    if not settings.openai_api_key:
        lowered = text.lower()
        return any(
            phrase in lowered
            for phrase in (
                "available",
                "availabl",
                "find this",
                "get this",
                "same as",
                "like this",
                "do you have",
                "can i get",
                "looking for",
                "need this",
                "search",
            )
        )

    try:
        response = await _client().chat.completions.create(
            model=settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify whether the buyer wants to find or buy a product like the one "
                        "in an uploaded photo. Return ONLY JSON: "
                        '{"availability_intent": true|false}. '
                        "True for: is this available, find this, same as photo, do you have this, "
                        "need something like this. False for: unrelated chat, returns, complaints, "
                        "only describing without sourcing intent."
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=40,
            response_format={"type": "json_object"},
        )
        raw = json.loads(response.choices[0].message.content or "{}")
        return bool(raw.get("availability_intent"))
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("Availability intent classification failed: %s", exc)
        return True


def intent_to_search_specs(intent: VisualProductIntent) -> list[str]:
    specs: list[str] = []
    seen: set[str] = set()
    for value in intent.visible_attributes.values():
        text = str(value or "").strip()
        if text and text.lower() not in seen:
            seen.add(text.lower())
            specs.append(text)
    for key, value in intent.visible_attributes.items():
        combined = f"{key} {value}".strip()
        if combined.lower() not in seen:
            seen.add(combined.lower())
            specs.append(combined)
    return specs


async def build_structured_query_from_intent(
    intent: VisualProductIntent,
    *,
    location: str | None,
    user_note: str | None = None,
) -> StructuredQuery:
    product = (intent.search_query or intent.product_name or "").strip()
    loc = (location or "unknown").strip() or "unknown"
    structured = StructuredQuery(
        raw_query=(user_note or product).strip() or product,
        product=product,
        category=query_structurer.normalize_category(intent.category),
        location=loc,
        intent="best_value",
        urgency="immediate",
        search_specs=search_specs.enrich_search_specs(
            intent_to_search_specs(intent),
            product_attributes=dict(intent.visible_attributes or {}),
        ),
    )
    try:
        structured = await enrich_query_route(structured)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("enrich_query_route failed for visual intent: %s", exc)
    return structured


def _format_specs(intent: VisualProductIntent) -> str:
    parts = [
        f"{key}: {value}"
        for key, value in (intent.visible_attributes or {}).items()
        if key and value
    ]
    return ", ".join(parts) if parts else ""


def build_location_prompt(intent: VisualProductIntent) -> str:
    specs = _format_specs(intent)
    lines = [
        f"From your photo, this looks like: {intent.product_name}.",
    ]
    if specs:
        lines.append(f"Visible details: {specs}.")
    if intent.description and intent.description.strip().lower() not in intent.product_name.lower():
        lines.append(intent.description.strip())
    lines.append("Which city should I search in?")
    return "\n\n".join(lines)


def build_confirmation_message(intent: VisualProductIntent, *, location: str | None) -> str:
    specs = _format_specs(intent)
    lines = [
        f"From your photo, this looks like: {intent.product_name}.",
    ]
    if specs:
        lines.append(f"Visible details: {specs}.")
    if intent.search_query and intent.search_query.lower() != intent.product_name.lower():
        lines.append(f"I would search for: {intent.search_query}.")
    if location and location.lower() != "unknown":
        lines.append(f"Location: {location}.")
    lines.append("Is this what we are searching for?")
    return "\n\n".join(lines)


def confirmation_suggested_replies(intent: VisualProductIntent) -> list[str]:
    product = intent.search_query or intent.product_name
    return [
        f"Yes, search for {product}",
        "No, that's not right",
    ]


def parse_confirmation_reply(message: str) -> str | None:
    """Return 'yes', 'no', or None if not a confirmation answer."""
    text = (message or "").strip()
    if not text:
        return None
    if _SEARCH_FOR_PREFIX.match(text):
        return "yes"
    if _CONFIRM_YES.match(text.rstrip(".")):
        return "yes"
    if _CONFIRM_NO.match(text.rstrip(".")):
        return "no"
    if text.lower().startswith("yes,") or text.lower().startswith("yes "):
        return "yes"
    if text.lower().startswith("no,") or text.lower().startswith("no "):
        return "no"
    return None


def should_offer_image_nudge(
    *,
    variance_needs_spec: bool,
    image_nudge_sent: bool,
    category: str | None,
) -> bool:
    if image_nudge_sent:
        return False
    return variance_needs_spec


def format_image_nudge_note() -> str:
    return (
        "IMAGE NUDGE — If the buyer has not pinned the exact model/spec yet, you may add ONE sentence: "
        "'If you have a photo of the product or packaging, send it — that helps identify the exact item.' "
        "Do not ask for a photo if they already gave a precise spec or if you already asked for a photo."
    )


def pending_payload(
    intent: VisualProductIntent,
    structured_query: StructuredQuery,
    *,
    awaiting: str,
    availability_intent: bool,
) -> dict:
    return {
        "awaiting": awaiting,
        "availability_intent": availability_intent,
        "intent": intent.model_dump(mode="json"),
        "structured_query": structured_query.model_dump(mode="json"),
    }
