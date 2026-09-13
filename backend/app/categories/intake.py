from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel

from app.models.schemas import ConversationState, StructuredQuery
from app.services import query_structurer

logger = logging.getLogger(__name__)


class CategoryIntakeResult(BaseModel):
    handled: bool = False
    assistant_message: Optional[str] = None
    handoff_query: Optional[str] = None
    structured_query: Optional[StructuredQuery] = None


async def maybe_handle_category_intake(
    *,
    state: ConversationState,
    combined_query_text: str,
    latest_message: str,
) -> CategoryIntakeResult | None:
    """Run lightweight category-owned intake before Priya starts.

    This layer is intentionally narrow: it only intercepts categories that have
    explicit attribute schemas. Everyone else falls through to the existing
    Priya-driven flow.
    """

    if state.priya_conversation_history:
        return None

    lowered = combined_query_text.lower()
    looks_like_gold = (
        state.category == "gold"
        or state.product_family_id is not None
        or any(token in lowered for token in ("gold", "silver", "bullion", "jewellery", "jewelry", "coin", "bar", "biscuit", "995", "999"))
    )
    structured: StructuredQuery | None = None
    if looks_like_gold:
        structured = await query_structurer.structure_query(combined_query_text)

        if structured.category == "gold":
            from app.categories.gold.intake import handle_gold_intake

            return await handle_gold_intake(
                state=state,
                structured_query=structured,
                combined_query_text=combined_query_text,
                latest_message=latest_message,
            )

        logger.debug("No gold intake handler for category=%s", structured.category)

    structured = structured or await query_structurer.structure_query(combined_query_text)
    from app.services.product_vendor_routing import build_product_intent

    intent = await build_product_intent(structured)
    if intent.missing_attributes and intent.follow_up_questions:
        state.product = structured.product
        state.category = structured.category
        state.product_family_id = intent.product_id or intent.subcategory_id
        state.product_attributes = intent.attributes
        state.product_intake_awaiting_attribute = intent.missing_attributes[0]
        return CategoryIntakeResult(
            handled=True,
            assistant_message=intent.follow_up_questions[0],
            structured_query=structured,
        )

    if state.product_intake_awaiting_attribute or state.product_attributes:
        state.product = structured.product
        state.category = structured.category
        state.product_family_id = intent.product_id or intent.subcategory_id
        state.product_attributes = intent.attributes
        state.product_intake_awaiting_attribute = None
        return CategoryIntakeResult(
            handled=False,
            handoff_query=combined_query_text,
            structured_query=structured,
        )

    return None
