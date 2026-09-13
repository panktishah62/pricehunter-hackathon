"""End-to-end gold.zwig.in + WhatsApp gold/silver intake (no live LLM/Mongo)."""

from __future__ import annotations

import asyncio
import pytest

from app.models.schemas import SearchProgressSnapshot, StructuredQuery
from app.services import chat_session


def _hostile_structure(text: str) -> StructuredQuery:
    """Mimic the production LLM: 999 ⇒ gold; a weight-only line loses the city."""
    lowered = text.lower()
    location = "unknown"
    if "ahmedabad" in lowered:
        location = "Ahmedabad"
    elif "mumbai" in lowered:
        location = "Mumbai"
    elif "rajkot" in lowered:
        location = "Rajkot"
    return StructuredQuery(
        product="999 gold",
        category="gold",
        location=location,
        intent="cheapest",
        raw_query=text,
    )


async def _identity_enrich(query: StructuredQuery) -> StructuredQuery:
    return query


async def _noop_persist(*_args, **_kwargs):
    return None


async def _no_open_request(*_args, **_kwargs):
    return None


async def _fake_create_request(**kwargs):
    return {"request_id": "GR-TEST"}


async def _fake_start_search(query, **_kwargs):
    return SearchProgressSnapshot(query=query, status="completed")


async def _priya_must_not_run(*_args, **_kwargs):
    raise AssertionError("Priya must not run after gold/silver intake handoff")


@pytest.fixture
def desk_patches(monkeypatch):
    chat_session._SESSIONS.clear()
    async def fake_structure(text: str) -> StructuredQuery:
        return _hostile_structure(text)

    monkeypatch.setattr(chat_session.query_structurer, "structure_query", fake_structure)
    monkeypatch.setattr(chat_session, "enrich_query_route", _identity_enrich)
    monkeypatch.setattr(chat_session, "_persist_session_snapshot", _noop_persist)
    monkeypatch.setattr(chat_session, "_persist_chat_message", _noop_persist)
    monkeypatch.setattr(chat_session.gold_requests, "get_open_request_for_session", _no_open_request)
    monkeypatch.setattr(chat_session.gold_requests, "create_request", _fake_create_request)
    monkeypatch.setattr(chat_session.search_progress, "start_search", _fake_start_search)
    monkeypatch.setattr(chat_session, "_priya_respond", _priya_must_not_run)
    monkeypatch.setattr(chat_session.settings, "gold_use_unified_taxonomy_flow", False)

    async def _no_refresh(_query):
        return None

    async def _no_national(_category_id):
        return None

    monkeypatch.setattr(
        "app.services.gold_rate_rules.national_bullion_consensus",
        _no_national,
        raising=False,
    )
    yield
    chat_session._SESSIONS.clear()


async def _gold_site_turns(*messages: str) -> list:
    session_id = None
    responses = []
    for message in messages:
        response = await chat_session.process_message(
            message,
            session_id=session_id,
            forced_category="gold",
        )
        session_id = response.session_id
        responses.append(response)
    return responses


async def _whatsapp_turns(*messages: str) -> list:
    session_id = None
    responses = []
    for message in messages:
        response = await chat_session.process_message(
            message,
            session_id=session_id,
            request_metadata={"source": "whatsapp", "phone_number": "919999999999"},
        )
        session_id = response.session_id
        responses.append(response)
    return responses


def test_gold_site_silver_stays_silver_through_weight(desk_patches) -> None:
    first, second, third = asyncio.run(
        _gold_site_turns("Silver 999 in Ahmedabad", "Coin", "10 grams")
    )
    assert "form of bullion" in (first.assistant_message or "").lower()
    assert "weight" in (second.assistant_message or "").lower()
    noted = (third.assistant_message or "").lower()
    assert third.ready_to_search is True
    assert noted.startswith("noted:")
    assert "silver" in noted
    assert "coin" in noted
    assert "999 gold" not in noted
    assert "ahmedabad" in noted
    assert "contacting all the suppliers" in noted
    assert "silver" in (third.state.product or "").lower()
    assert (third.state.location or "").lower() == "ahmedabad"


def test_gold_site_gold_stays_gold(desk_patches) -> None:
    first, second, third = asyncio.run(
        _gold_site_turns("Gold 999 in Rajkot", "Bar", "10 grams")
    )
    assert "form of bullion" in (first.assistant_message or "").lower()
    noted = (third.assistant_message or "").lower()
    product = (third.state.product or "").lower()
    assert third.ready_to_search is True
    assert "silver" not in noted
    assert "silver" not in product
    assert "gold" in product
    assert "bar" in noted
    assert (third.state.location or "").lower() == "rajkot"
    assert "rajkot" in noted


def test_whatsapp_silver_coin_does_not_rewrite_to_gold(desk_patches) -> None:
    first, second, third = asyncio.run(
        _whatsapp_turns("Silver 999 in Ahmedabad", "Coin", "10 grams")
    )
    assert "form of bullion" in (first.assistant_message or "").lower()
    assert "weight" in (second.assistant_message or "").lower()
    noted = (third.assistant_message or "").lower()
    assert third.ready_to_search is True
    assert "noted:" in noted
    assert "silver" in noted
    assert "coin" in noted
    assert "999 gold" not in noted
    assert "ahmedabad" in noted


def test_whatsapp_gold_999_handoff(desk_patches) -> None:
    first, second, third = asyncio.run(
        _whatsapp_turns("Gold 999 in Mumbai", "Bar", "10 grams")
    )
    assert "form of bullion" in (first.assistant_message or "").lower()
    noted = (third.assistant_message or "").lower()
    assert third.ready_to_search is True
    assert "noted:" in noted
    assert "gold" in noted
    assert "silver" not in noted
    assert "mumbai" in noted
    assert "bar" in noted


def test_web_and_whatsapp_same_questions_and_noted(desk_patches) -> None:
    script = ("Silver 999 in Ahmedabad", "Coin", "10 grams")
    web = asyncio.run(_gold_site_turns(*script))
    whatsapp = asyncio.run(_whatsapp_turns(*script))
    assert [item.assistant_message for item in web] == [item.assistant_message for item in whatsapp]
    assert web[-1].state.search_strategy == "online"
    assert whatsapp[-1].state.search_strategy == "both"
