"""Gold/silver intake: keep metal through handoff; do not rewrite silver as gold."""

from __future__ import annotations

import asyncio

from app.categories.gold.intake import handle_gold_intake
from app.categories.gold.metal import infer_bullion_metal, snapshot_metal
from app.models.schemas import ConversationState, StructuredQuery


def test_infer_bullion_metal_prefers_silver() -> None:
    assert infer_bullion_metal("Silver 999 in Ahmedabad") == "silver"
    assert infer_bullion_metal("999 gold coin 10 grams") == "gold"
    assert infer_bullion_metal("xag 999 bar") == "silver"


def test_snapshot_metal_from_product_type() -> None:
    assert snapshot_metal({"product_type": "silver", "script_name": "999"}) == "silver"
    assert snapshot_metal({"product_type": "gold", "script_name": "SLV 999"}) == "gold"
    assert snapshot_metal({"script_name": "SLV 999 1KG"}) == "silver"


async def _run_silver_coin_intake() -> None:
    state = ConversationState()
    query = StructuredQuery(
        product="Silver 999",
        category="gold",
        location="Ahmedabad",
        intent="cheapest",
        raw_query="Silver 999 in Ahmedabad",
    )
    first = await handle_gold_intake(
        state=state,
        structured_query=query,
        combined_query_text="Silver 999 in Ahmedabad",
        latest_message="Silver 999 in Ahmedabad",
    )
    assert first.handled
    assert first.assistant_message
    assert "form of bullion" in (first.assistant_message or "").lower()

    combined = "Silver 999 in Ahmedabad Coin"
    second = await handle_gold_intake(
        state=state,
        structured_query=query,
        combined_query_text=combined,
        latest_message="Coin",
    )
    assert second.handled
    assert second.assistant_message
    assert "weight" in (second.assistant_message or "").lower()

    combined = "Silver 999 in Ahmedabad Coin 10 grams"
    third = await handle_gold_intake(
        state=state,
        structured_query=query,
        combined_query_text=combined,
        latest_message="10 grams",
    )
    assert third.handoff_query
    assert "silver" in third.handoff_query.lower()
    assert "gold coin" not in third.handoff_query.lower()
    assert third.structured_query is not None
    assert third.structured_query.quantity == "10 grams"


def test_silver_999_handoff_stays_silver() -> None:
    asyncio.run(_run_silver_coin_intake())


def test_dedupe_rate_snapshots_collapses_repeats() -> None:
    from app.categories.gold.metal import dedupe_rate_snapshots

    rows = [
        {"script_name": "GOLD 999 T+1", "sell_rate": 154090, "purity": "999"},
        {"script_name": "GOLD 999 T+1", "sell_rate": 154090, "purity": "999"},
        {"script_name": "GOLD 995 T+1", "sell_rate": 153390, "purity": "995"},
    ]
    unique = dedupe_rate_snapshots(rows * 30)
    assert len(unique) == 2
    buy_variants = [
        {"script_name": "GOLD 999 T+1", "sell_rate": 154090, "buy_rate": 153900, "purity": "999"},
        {"script_name": "GOLD 999 T+1", "sell_rate": 154090, "buy_rate": 154000, "purity": "999"},
    ]
    assert len(dedupe_rate_snapshots(buy_variants)) == 1


async def _run_gold_locked_weight_followup() -> None:
    from app.services.chat_session import _gold_locked_collect_bullion_attrs

    state = ConversationState(
        product="999 gold",
        location="Ahmedabad",
        raw_query="Silver 999 in Ahmedabad",
        product_family_id="gold_bullion",
        product_attributes={"form": "bullion", "purity": "999", "metal": "silver"},
        product_intake_awaiting_attribute="bullion_weight",
    )
    query = StructuredQuery(
        product="10 gram",
        category="gold",
        location="unknown",
        intent="cheapest",
        raw_query="10 gram",
    )
    prompt = await _gold_locked_collect_bullion_attrs(state, query, "10 gram")
    assert prompt is None
    assert query.product == "999 silver bullion 10 grams"
    assert query.quantity == "10 grams"


def test_gold_locked_weight_followup_keeps_silver() -> None:
    asyncio.run(_run_gold_locked_weight_followup())
