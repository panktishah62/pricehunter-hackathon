"""Smoke test: Get Live quotes queues a slow search, parks a later message, then delivers.

No Graph API / WhatsApp sends. Vendor lookup is mocked; outbound is captured in-memory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.models.schemas import (
    ChatMessageResponse,
    ConversationState,
    SearchProgressSnapshot,
    SearchResponse,
    StructuredQuery,
    UnifiedResult,
)
from app.services import marketing_outreach, persistence, whatsapp
from app.services.search_wait_copy import SEARCH_WAIT_START
from app.services.whatsapp import IncomingWhatsAppMessage


WA_ID = "919978447854"
GOLD_QUERY = "gold live rates in Mumbai"
PHARMA_QUERY = "sultamycin in Delhi"
GOLD_SEARCH_ID = "search-gold-cta"
PHARMA_SEARCH_ID = "search-pharma"


class _MemoryStore:
    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}
        self.message_ids: set[str] = set()
        self.contexts: dict[str, dict] = {}

    async def whatsapp_message_exists(self, message_id: str) -> bool:
        return message_id in self.message_ids

    async def record_whatsapp_message(self, *, message_id: str, **_kwargs) -> None:
        self.message_ids.add(message_id)

    async def get_whatsapp_session(self, wa_id: str) -> dict | None:
        doc = self.sessions.get(wa_id)
        return dict(doc) if doc is not None else None

    async def upsert_whatsapp_session(
        self,
        *,
        wa_id: str,
        phone_number: str,
        profile_name: str | None,
        session_id: str,
        last_message: str | None = None,
        last_search_id: str | None = None,
    ) -> None:
        doc = self.sessions.setdefault(
            wa_id,
            {"wa_id": wa_id, "pending_inbounds": [], "turn_status": "idle"},
        )
        doc["phone_number"] = phone_number
        doc["profile_name"] = profile_name
        doc["session_id"] = session_id
        if last_message is not None:
            doc["last_message"] = last_message
        if last_search_id is not None:
            doc["last_search_id"] = last_search_id

    async def try_acquire_whatsapp_turn(self, wa_id: str) -> bool:
        doc = self.sessions.setdefault(
            wa_id,
            {"wa_id": wa_id, "pending_inbounds": [], "turn_status": "idle"},
        )
        if doc.get("turn_status") in (None, "idle"):
            doc["turn_status"] = "busy"
            return True
        return False

    async def enqueue_whatsapp_inbound(self, wa_id: str, inbound: dict) -> None:
        doc = self.sessions.setdefault(
            wa_id,
            {"wa_id": wa_id, "pending_inbounds": [], "turn_status": "busy"},
        )
        pending = list(doc.get("pending_inbounds") or [])
        pending.append(inbound)
        doc["pending_inbounds"] = pending

    async def pop_whatsapp_pending_inbound(self, wa_id: str) -> dict | None:
        doc = self.sessions.get(wa_id) or {}
        pending = list(doc.get("pending_inbounds") or [])
        if pending:
            first = pending.pop(0)
            doc["pending_inbounds"] = pending
            doc["turn_status"] = "busy"
            return first
        doc["turn_status"] = "idle"
        doc.pop("active_search_id", None)
        return None

    async def set_whatsapp_active_search(self, wa_id: str, search_id: str | None) -> None:
        doc = self.sessions.setdefault(wa_id, {"wa_id": wa_id, "pending_inbounds": []})
        if search_id:
            doc["active_search_id"] = search_id
        else:
            doc.pop("active_search_id", None)

    async def peek_pending_context(self, wa_id: str) -> dict | None:
        doc = self.contexts.get(marketing_outreach.normalize_wa_id(wa_id))
        if doc and doc.get("status") == "pending":
            return dict(doc)
        return None

    async def mark_context_queued(self, wa_id: str, *, search_id: str | None = None) -> None:
        doc = self.contexts.get(marketing_outreach.normalize_wa_id(wa_id))
        if not doc:
            return
        doc["status"] = "queued"
        if search_id:
            doc["search_id"] = search_id

    async def restore_pending_context(self, wa_id: str) -> None:
        doc = self.contexts.get(marketing_outreach.normalize_wa_id(wa_id))
        if doc:
            doc["status"] = "pending"


def _msg(message_id: str, text: str) -> IncomingWhatsAppMessage:
    return IncomingWhatsAppMessage(
        message_id=message_id,
        wa_id=WA_ID,
        phone_number=WA_ID,
        profile_name="Pankti",
        message_type="text",
        text=text,
        payload={},
    )


def _query(product: str, location: str) -> StructuredQuery:
    return StructuredQuery(
        product=product,
        category="gold" if "gold" in product.lower() else "pharma",
        location=location,
        intent="cheapest",
        raw_query=f"{product} in {location}",
    )


def _snapshot(search_id: str, query: StructuredQuery, vendor: str, price: float) -> SearchProgressSnapshot:
    result = UnifiedResult(
        source_type="offline",
        result_type="live_rate",
        name=vendor,
        price=price,
        city=query.location,
    )
    return SearchProgressSnapshot(
        search_id=search_id,
        query=query,
        status="completed",
        final_results=SearchResponse(
            query=query,
            results=[result],
            online_count=0,
            offline_count=1,
            total_time_seconds=0.2,
            search_strategy="both",
        ),
    )


def test_cta_sends_noted_immediately_parks_followup_then_delivers(monkeypatch) -> None:
    asyncio.run(_run_cta_turn_smoke(monkeypatch))


async def _run_cta_turn_smoke(monkeypatch) -> None:
    store = _MemoryStore()
    store.contexts[WA_ID] = {
        "wa_id": WA_ID,
        "product_query": GOLD_QUERY,
        "campaign_id": "zwig_marketing_1",
        "status": "pending",
        "expires_at": datetime.now(timezone.utc),
    }
    outbound: list[str] = []
    process_calls: list[str] = []
    gold_started = asyncio.Event()
    gold_gate = asyncio.Event()

    gold_snapshot = _snapshot(GOLD_SEARCH_ID, _query("gold live rates", "Mumbai"), "Mumbai Gold Desk", 72500)
    pharma_snapshot = _snapshot(PHARMA_SEARCH_ID, _query("sultamycin", "Delhi"), "Delhi Pharma", 120)

    async def fake_send_message(to, payload, *, session_id, text, search_id=None):
        outbound.append(text)

    async def noop_typing(*_args, **_kwargs):
        return None

    async def fake_process_message(user_message, session_id, **kwargs):
        process_calls.append(user_message)
        if "gold" in user_message.lower():
            gold_started.set()
            await gold_gate.wait()
            snapshot = gold_snapshot
        else:
            snapshot = pharma_snapshot
        return ChatMessageResponse(
            session_id=session_id,
            assistant_message=f"Noted: {user_message}. Contacting all the suppliers for best prices.",
            state=ConversationState(session_id=session_id),
            ready_to_search=True,
            search_progress=snapshot,
        )

    async def fake_send_search_results_when_ready(
        *, to, wa_id, session_id, search_id, inbound_message_id
    ):
        await whatsapp.send_text_message(
            to, f"RESULTS:{search_id}", session_id=session_id, search_id=search_id
        )
        if search_id == GOLD_SEARCH_ID:
            await whatsapp.send_text_message(
                to,
                marketing_outreach.HANDOFF_MESSAGE,
                session_id=session_id,
                search_id=search_id,
            )

    monkeypatch.setattr(whatsapp, "_send_message", fake_send_message)
    monkeypatch.setattr(whatsapp, "maybe_show_typing_while_processing", noop_typing)
    monkeypatch.setattr(whatsapp.chat_session, "process_message", fake_process_message)
    monkeypatch.setattr(whatsapp, "send_search_results_when_ready", fake_send_search_results_when_ready)

    monkeypatch.setattr(persistence, "whatsapp_message_exists", store.whatsapp_message_exists)
    monkeypatch.setattr(persistence, "record_whatsapp_message", store.record_whatsapp_message)
    monkeypatch.setattr(persistence, "get_whatsapp_session", store.get_whatsapp_session)
    monkeypatch.setattr(persistence, "upsert_whatsapp_session", store.upsert_whatsapp_session)
    monkeypatch.setattr(persistence, "try_acquire_whatsapp_turn", store.try_acquire_whatsapp_turn)
    monkeypatch.setattr(persistence, "enqueue_whatsapp_inbound", store.enqueue_whatsapp_inbound)
    monkeypatch.setattr(persistence, "pop_whatsapp_pending_inbound", store.pop_whatsapp_pending_inbound)
    monkeypatch.setattr(persistence, "set_whatsapp_active_search", store.set_whatsapp_active_search)

    monkeypatch.setattr(marketing_outreach, "peek_pending_context", store.peek_pending_context)
    monkeypatch.setattr(marketing_outreach, "mark_context_queued", store.mark_context_queued)
    monkeypatch.setattr(marketing_outreach, "restore_pending_context", store.restore_pending_context)

    cta_task = asyncio.create_task(
        whatsapp.handle_incoming_message(_msg("wamid-cta", "Get Live quotes"))
    )
    await asyncio.wait_for(gold_started.wait(), timeout=2)

    noted = marketing_outreach.search_started_copy(GOLD_QUERY)
    assert outbound[:2] == [noted, SEARCH_WAIT_START]
    assert process_calls == [GOLD_QUERY]
    assert "RESULTS:" not in "".join(outbound)

    await whatsapp.handle_incoming_message(_msg("wamid-pharma", PHARMA_QUERY))
    assert process_calls == [GOLD_QUERY], "follow-up must wait until gold search finishes"
    pending = (await store.get_whatsapp_session(WA_ID) or {}).get("pending_inbounds") or []
    assert len(pending) == 1
    assert pending[0]["text"] == PHARMA_QUERY

    gold_gate.set()
    await asyncio.wait_for(cta_task, timeout=2)

    assert process_calls == [GOLD_QUERY, PHARMA_QUERY]
    assert outbound[0] == noted
    assert outbound[1] == SEARCH_WAIT_START
    assert outbound[2] == f"RESULTS:{GOLD_SEARCH_ID}"
    assert outbound[3] == marketing_outreach.HANDOFF_MESSAGE
    assert any(text.startswith("Noted:") and "sultamycin" in text.lower() for text in outbound[4:])
    assert f"RESULTS:{PHARMA_SEARCH_ID}" in outbound
    gold_results_at = outbound.index(f"RESULTS:{GOLD_SEARCH_ID}")
    pharma_results_at = outbound.index(f"RESULTS:{PHARMA_SEARCH_ID}")
    assert gold_results_at < pharma_results_at
    assert store.contexts[WA_ID]["status"] == "queued"
    assert store.contexts[WA_ID]["search_id"] == GOLD_SEARCH_ID
    assert (await store.get_whatsapp_session(WA_ID) or {}).get("turn_status") == "idle"
