"""Human-in-the-loop fulfilment requests.

A user query becomes a request (with a human-readable Request ID and a snapshot
of the results we already showed). Ops picks it up on dashboard.zwig.in and
appends supplier quotes; each quote is mirrored into the user's chat session and
pushed as a notification.

Originally gold-only (gold.zwig.in, fully manual); now also opened for the
normal flow (electronics/medicine/…) ALONGSIDE the automated voice-calling +
online search, so ops can supplement/override. The collection stays named
`gold_requests` for back-compat; each doc carries a `category`.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.database import gold_requests_collection
from app.services import persistence

logger = logging.getLogger(__name__)

STATUS_AWAITING = "awaiting_supplier"
STATUS_RESPONDED = "responded"
STATUS_CLOSED = "closed"

_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous 0/O/1/I


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_request_id() -> str:
    suffix = "".join(secrets.choice(_ID_ALPHABET) for _ in range(5))
    return f"GR-{_now():%y%m%d}-{suffix}"


def _first_present(row: dict[str, Any], *keys: str) -> Any:
    """First value that is actually present — keeps a numeric 0 (a valid rate)
    while skipping None and empty strings, unlike a plain `or` chain."""
    for key in keys:
        value = row.get(key)
        if value is not None and value != "":
            return value
    return None


def _summarize_results(
    results: list[dict[str, Any]] | None,
    *,
    category: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Compact, category-agnostic snapshot of what the user saw, for ops context.
    Maps from the UnifiedResult shape (name + price + url + nested gold_terms)."""
    is_gold = (category or "").strip().lower() == "gold"
    summary: list[dict[str, Any]] = []
    for r in (results or [])[:limit]:
        terms = r.get("gold_terms") or {}
        summary.append(
            {
                "vendor_name": _first_present(r, "name", "vendor_name", "vendor", "title"),
                "city": _first_present(r, "city", "location"),
                "label": _first_present(terms, "script_name") or _first_present(r, "label", "product"),
                # `sell_rate` kept as the price key for dashboard back-compat.
                "sell_rate": _first_present(r, "price", "sell_rate"),
                "url": _first_present(r, "url", "link", "product_url"),
                "purity": _first_present(terms, "purity") or r.get("purity"),
                "unit": "per 10g" if is_gold else None,
                "result_type": r.get("result_type"),
            }
        )
    return summary


async def create_request(
    *,
    session_id: str,
    product: str | None,
    location: str | None,
    query: dict[str, Any] | None = None,
    results: list[dict[str, Any]] | None = None,
    live_rates: list[dict[str, Any]] | None = None,
    category: str | None = None,
    search_id: str | None = None,
) -> dict[str, Any]:
    request_id = generate_request_id()
    now = _now()
    # `live_rates` kept as the gold-era param name; `results` is the generic one.
    shown = results if results is not None else live_rates
    resolved_category = (category or (query or {}).get("category") or "gold")
    doc = {
        "request_id": request_id,
        "session_id": session_id,
        "category": resolved_category,
        "product": product,
        "location": location,
        # Denormalized for easy display/filtering on the dashboard (the full
        # value also lives in query.quantity). e.g. "100 gram", "1 kg".
        "quantity": (query or {}).get("quantity"),
        "query": query or {},
        "search_id": search_id,
        # Stored under `live_rates` for dashboard back-compat; holds the generic
        # results snapshot now.
        "live_rates": _summarize_results(shown, category=resolved_category),
        "status": STATUS_AWAITING,
        "responses": [],
        "created_at": now,
        "updated_at": now,
    }
    await gold_requests_collection.insert_one(dict(doc))
    doc.pop("_id", None)
    logger.info(
        "fulfilment request created %s session=%s category=%s product=%s",
        request_id, session_id, resolved_category, product,
    )
    return doc


async def get_open_request_for_session(session_id: str) -> dict[str, Any] | None:
    return await gold_requests_collection.find_one(
        {"session_id": session_id, "status": {"$ne": STATUS_CLOSED}},
        sort=[("created_at", -1)],
    )


async def get_request(request_id: str) -> dict[str, Any] | None:
    doc = await gold_requests_collection.find_one({"request_id": request_id})
    if doc:
        doc.pop("_id", None)
    return doc


async def update_request_results(request_id: str, *, results: list[dict[str, Any]]) -> bool:
    """Backfill/refresh the results snapshot ops see on the dashboard. `results`
    must already be in the summarized row shape (see `_summarize_results`). Used
    by the WhatsApp pipeline, whose results arrive after the request is opened."""
    res = await gold_requests_collection.update_one(
        {"request_id": request_id},
        {"$set": {"live_rates": results, "updated_at": _now()}},
    )
    return res.matched_count > 0


async def list_requests(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if status:
        query["status"] = status
    cursor = gold_requests_collection.find(query).sort("created_at", -1).limit(limit)
    docs = await cursor.to_list(length=limit)
    for d in docs:
        d.pop("_id", None)
    return docs


def _format_quote(supplier: str, price: str | None, note: str | None, url: str | None = None) -> str:
    bits = [f"💬 {supplier}"]
    if price:
        bits.append(f"— {price}")
    line = " ".join(bits)
    if note:
        line = f"{line}\n{note}"
    if url:
        line = f"{line}\n{url}"
    return line


async def add_response(
    *,
    request_id: str,
    supplier: str,
    price: str | None = None,
    note: str | None = None,
    url: str | None = None,
    author: str | None = None,
) -> dict[str, Any] | None:
    """Append a supplier quote to the request AND mirror it into the user's chat
    session so it reaches the user via in-session polling. Returns the response."""
    doc = await gold_requests_collection.find_one({"request_id": request_id})
    if not doc:
        return None

    # Only allow http(s) links — an ops-typed `javascript:`/`data:` URL would
    # otherwise execute when rendered as an <a href> on the dashboard.
    if url and not re.match(r"^https?://", url.strip(), re.IGNORECASE):
        url = None

    now = _now()
    response = {
        "response_id": str(uuid4()),
        "supplier": supplier,
        "price": price,
        "note": note,
        "url": url,
        "author": author,
        "created_at": now,
    }
    await gold_requests_collection.update_one(
        {"request_id": request_id},
        {
            "$push": {"responses": response},
            "$set": {"status": STATUS_RESPONDED, "updated_at": now},
        },
    )

    # Mirror into the user's chat session so it shows up in history + polling.
    session_id = doc.get("session_id")
    if session_id:
        try:
            await persistence.append_chat_message(
                session_id=session_id,
                message_id=f"resp-{response['response_id']}",
                role="assistant",
                content=_format_quote(supplier, price, note, url),
                # ChatHistoryMessage.kind only allows text/status/results, so the
                # quote is stored as a normal assistant text (the structured data
                # lives in payload + in the request's responses[]).
                kind="text",
                payload={
                    "request_id": request_id,
                    "response_id": response["response_id"],
                    "supplier": supplier,
                    "price": price,
                    "note": note,
                    "url": url,
                    "kind": "supplier_quote",
                },
            )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to mirror request response into chat %s: %s", session_id, exc)

        # Push notification so the user gets pinged even if the app is closed.
        try:
            from app.services import push_service

            quote_bits = supplier + (f" — {price}" if price else "")
            await push_service.send_to_session(
                session_id,
                title=f"New quote for {request_id}",
                body=quote_bits,
                url="/#/app",
                tag=request_id,
                data={"request_id": request_id, "kind": "supplier_quote"},
            )
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Failed to push gold_request response for %s: %s", session_id, exc)

        # WhatsApp-originated requests: deliver the ops quote straight back to the
        # user's WhatsApp chat (web push + in-session poll above only reach the web
        # app). Lazy import avoids the whatsapp -> chat_session -> gold_requests cycle.
        # Note: Meta only permits free-form sends inside the 24h customer-service
        # window; later replies need an approved template and will fail here.
        if session_id.startswith("whatsapp-"):
            try:
                from app.services import whatsapp as whatsapp_service

                wa_session = await persistence.get_whatsapp_session_by_session_id(session_id)
                phone = (wa_session or {}).get("phone_number") or session_id[len("whatsapp-"):]
                if phone:
                    await whatsapp_service.send_text_message(
                        phone,
                        _format_quote(supplier, price, note, url),
                        session_id=session_id,
                    )
            except Exception as exc:  # pragma: no cover - external WhatsApp API
                logger.warning("Failed to deliver request response to WhatsApp %s: %s", session_id, exc)

    return response


async def list_responses_for_session(session_id: str) -> list[dict[str, Any]]:
    """All supplier responses across the session's request(s), oldest first —
    consumed by the user-side poll on gold.zwig.in."""
    docs = await gold_requests_collection.find({"session_id": session_id}).sort("created_at", 1).to_list(length=50)
    out: list[dict[str, Any]] = []
    for d in docs:
        rid = d.get("request_id")
        for resp in d.get("responses", []) or []:
            created = resp.get("created_at")
            out.append(
                {
                    "request_id": rid,
                    "response_id": resp.get("response_id"),
                    "supplier": resp.get("supplier"),
                    "price": resp.get("price"),
                    "note": resp.get("note"),
                    "url": resp.get("url"),
                    "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
                }
            )
    out.sort(key=lambda r: r.get("created_at") or "")
    return out


async def close_request(request_id: str) -> bool:
    result = await gold_requests_collection.update_one(
        {"request_id": request_id},
        {"$set": {"status": STATUS_CLOSED, "updated_at": _now()}},
    )
    return result.modified_count > 0
