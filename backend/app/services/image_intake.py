from __future__ import annotations

import base64
import io
import json
import logging
from typing import Final

from openai import AsyncOpenAI

from app.config import settings
from app.models.schemas import VisualProductIntent
from app.services import query_structurer

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES: Final[int] = 20 * 1024 * 1024
OPENAI_IMAGE_TYPES: Final[set[str]] = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}
SUPPORTED_IMAGE_TYPES: Final[set[str]] = {
    *OPENAI_IMAGE_TYPES,
    "image/jpg",
    "image/heic",
    "image/heif",
    "application/octet-stream",
}

_openai_client: AsyncOpenAI | None = None

VISION_SYSTEM_PROMPT = """You turn buyer-uploaded product photos into searchable procurement briefs.

Return a JSON object matching the schema. Rules:
- Describe only visible product evidence. Do not invent brand/model unless visible.
- For ambiguous gifting/decor/fashion items, produce multiple searchable aliases.
- category MUST be one of: electronics, medicine, gold, home_decor, gifting, fashion, furniture, industrial, hardware, agricultural, services.
- search_query should be a concise product query usable for supplier search.
- follow_up_questions should ask only what is necessary before searching: usually quantity, location, budget, size, material, or occasion.
- If the image is not a purchasable product, say so in description and set a cautious search_query.
"""


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _data_url(image_bytes: bytes, content_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _normalize_content_type(content_type: str | None) -> str:
    normalized = (content_type or "image/jpeg").split(";", 1)[0].strip().lower()
    return "image/jpeg" if normalized == "image/jpg" else normalized


def _prepare_image_for_openai(image_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    if content_type in OPENAI_IMAGE_TYPES:
        return image_bytes, content_type
    try:
        from PIL import Image
        from pillow_heif import register_heif_opener

        register_heif_opener()
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            image.thumbnail((1600, 1600))
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=90, optimize=True)
            return output.getvalue(), "image/jpeg"
    except Exception as exc:
        logger.warning("Image conversion to JPEG failed for content_type=%s: %s", content_type, exc)
        return image_bytes, content_type


def _fallback_intent(note: str | None = None) -> VisualProductIntent:
    product = (note or "product from uploaded image").strip() or "product from uploaded image"
    return VisualProductIntent(
        product_name=product,
        category=query_structurer.normalize_category(query_structurer.infer_category(product)),
        aliases=[product],
        visible_attributes={},
        confidence=0.2,
        follow_up_questions=["What should I search this as?", "What city should I search in?"],
        search_query=product,
        description="I could not confidently read the image, so I will use your text note as the product description.",
    )


def _coerce_visual_intent_payload(payload: object, note: str | None) -> dict:
    if not isinstance(payload, dict):
        payload = {}

    product_name = (
        payload.get("product_name")
        or payload.get("product")
        or payload.get("name")
        or payload.get("title")
        or payload.get("search_query")
        or payload.get("description")
        or note
        or "product from uploaded image"
    )
    search_query = payload.get("search_query") or payload.get("query") or product_name
    description = payload.get("description") or f"Visible product appears to be {product_name}."
    aliases = payload.get("aliases") or payload.get("similar_names") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    visible_attributes = payload.get("visible_attributes") or payload.get("attributes") or {}
    if not isinstance(visible_attributes, dict):
        visible_attributes = {}
    follow_up_questions = payload.get("follow_up_questions") or payload.get("questions") or []
    if isinstance(follow_up_questions, str):
        follow_up_questions = [follow_up_questions]

    coerced = dict(payload)
    coerced["product_name"] = str(product_name).strip() or "product from uploaded image"
    coerced["search_query"] = str(search_query).strip() or coerced["product_name"]
    coerced["description"] = str(description).strip() or coerced["product_name"]
    coerced["category"] = str(payload.get("category") or query_structurer.infer_category(coerced["search_query"]))
    coerced["aliases"] = [str(alias).strip() for alias in aliases if str(alias).strip()]
    coerced["visible_attributes"] = {
        str(key).strip(): str(value).strip()
        for key, value in visible_attributes.items()
        if str(key).strip() and str(value).strip()
    }
    coerced["follow_up_questions"] = [
        str(question).strip()
        for question in follow_up_questions
        if str(question).strip()
    ][:3]
    try:
        coerced["confidence"] = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        coerced["confidence"] = 0.5
    return coerced


async def analyze_product_image(
    image_bytes: bytes,
    *,
    content_type: str | None,
    user_note: str | None = None,
) -> VisualProductIntent:
    if not image_bytes:
        raise ValueError("Please attach a product image.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ValueError("Image is too large. Please upload an image under 20 MB.")

    normalized_content_type = _normalize_content_type(content_type)
    if normalized_content_type not in SUPPORTED_IMAGE_TYPES and not normalized_content_type.startswith("image/"):
        raise ValueError("Unsupported image type. Please upload JPG, PNG, WEBP, GIF, or HEIC.")
    openai_image_bytes, openai_content_type = _prepare_image_for_openai(image_bytes, normalized_content_type)

    if not settings.openai_api_key:
        logger.info("OpenAI API key missing; using fallback visual product intent.")
        return _fallback_intent(user_note)

    user_text = (
        "Analyze this product image for a sourcing/search assistant."
        + (f"\nBuyer note: {user_note.strip()}" if user_note and user_note.strip() else "")
    )

    try:
        client = _get_openai_client()
        response = await client.chat.completions.create(
            model=settings.openai_visual_product_model or settings.openai_model,
            messages=[
                {"role": "system", "content": f"{VISION_SYSTEM_PROMPT}\nReturn only valid JSON."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": _data_url(openai_image_bytes, openai_content_type)}},
                    ],
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=700,
        )
        raw_content = response.choices[0].message.content or "{}"
        parsed = VisualProductIntent.model_validate(
            _coerce_visual_intent_payload(json.loads(raw_content), user_note)
        )
        parsed.category = query_structurer.normalize_category(parsed.category)
        if not parsed.aliases:
            parsed.aliases = [parsed.product_name, parsed.search_query]
        parsed.search_query = (parsed.search_query or parsed.product_name).strip()
        parsed.product_name = (parsed.product_name or parsed.search_query).strip()
        return parsed
    except Exception as exc:  # pragma: no cover - external API
        logger.warning("Image product analysis failed, using fallback: %s", exc)
        return _fallback_intent(user_note)
