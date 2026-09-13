"""Tests for Phase 4 visual intake helpers."""

from __future__ import annotations

from app.models.schemas import VisualProductIntent
from app.services import visual_intake


def test_parse_confirmation_yes_from_suggested_reply() -> None:
    assert visual_intake.parse_confirmation_reply("Yes, search for power bank 10000mAh") == "yes"


def test_parse_confirmation_no() -> None:
    assert visual_intake.parse_confirmation_reply("No, that's not right") == "no"


def test_build_confirmation_message_asks_to_confirm() -> None:
    intent = VisualProductIntent(
        product_name="Anker PowerCore 10000mAh",
        category="electronics",
        search_query="Anker 10000 mAh power bank",
        visible_attributes={"capacity": "10000 mAh"},
        confidence=0.9,
        description="A black power bank",
    )
    message = visual_intake.build_confirmation_message(intent, location="ahmedabad")
    assert "Is this what we are searching for?" in message
    assert "10000" in message


def test_intent_to_search_specs() -> None:
    intent = VisualProductIntent(
        product_name="Paracetamol 500mg",
        category="medicine",
        search_query="Paracetamol 500 mg tablets",
        visible_attributes={"strength": "500 mg"},
        confidence=0.85,
        description="Medicine strip",
    )
    specs = visual_intake.intent_to_search_specs(intent)
    assert any("500" in item for item in specs)
