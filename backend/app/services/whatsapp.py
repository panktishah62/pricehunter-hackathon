from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

import httpx

from app.config import settings
from app.models.schemas import ChatMessageResponse, SearchProgressSnapshot, UnifiedResult
from app.services import chat_session, location_resolver, marketing_outreach, persistence, search_progress, whatsapp_flows
from app.whatsapp_flows.gold_live_rates import build_gold_live_rates_screen_data

logger = logging.getLogger(__name__)

GRAPH_API_BASE_URL = "https://graph.facebook.com"

# Shared httpx client for Meta Graph API — reuses TCP connections across calls.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30)
    return _http_client


async def close_whatsapp_http_client() -> None:
    """Release Meta Graph pooled connections at process shutdown."""

    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = None
        return
    await _http_client.aclose()
    _http_client = None


GREETING_TERMS = {"hi", "hello", "hey", "help", "menu", "start"}
# Inbound looks like ack / chit-chat only — skip instant "working on it" ping (router may still run).
_QUICK_ACK_SKIP: frozenset[str] = frozenset(
    {
        "ok",
        "k",
        "kk",
        "okay",
        "yes",
        "no",
        "yeah",
        "yep",
        "nah",
        "nope",
        "thanks",
        "thank you",
        "thx",
        "ty",
        "pls",
        "please",
        "sure",
        "bye",
        "good",
        "great",
        "cool",
        "nice",
        "fine",
        "later",
        "done",
        "noted",
    }
)
_background_tasks: set[asyncio.Task[None]] = set()


def _should_send_immediate_feedback(normalized: str) -> bool:
    """True when we show typing before slow LLM + search work (skip pure ack replies)."""

    t = normalized.strip()
    if len(t) <= 1:
        return False
    if t in _QUICK_ACK_SKIP:
        return False
    return True



async def download_whatsapp_media(media_id: str) -> tuple[bytes, str]:
    """Download inbound WhatsApp image/audio/document media by Meta media id."""
    if not settings.whatsapp_access_token:
        raise ValueError("WhatsApp access token is not configured.")
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    client = _get_http_client()
    meta_url = f"{GRAPH_API_BASE_URL}/{settings.whatsapp_api_version}/{media_id}"
    meta_response = await client.get(meta_url, headers=headers)
    meta_response.raise_for_status()
    meta_payload = meta_response.json()
    download_url = str(meta_payload.get("url") or "")
    if not download_url:
        raise ValueError("WhatsApp media URL missing from Meta response.")
    content_type = str(meta_payload.get("mime_type") or "image/jpeg")
    file_response = await client.get(download_url, headers=headers)
    file_response.raise_for_status()
    return file_response.content, content_type

    """Mark inbound as read and show typing dots (Meta Cloud API; dismissed on send or ~25s)."""

    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        return

    url = (
        f"{GRAPH_API_BASE_URL}/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": inbound_whatsapp_message_id,
        "typing_indicator": {"type": "text"},
    }
    client = _get_http_client()
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()


async def _refresh_typing_indicator(inbound_message_id: str) -> None:
    """Refresh typing with Meta's read+typing payload using the inbound WhatsApp message id."""

    await send_read_receipt_and_typing_indicator(inbound_message_id)


async def maybe_show_typing_while_processing(
    *,
    inbound_message_id: str,
    normalized_message: str,
) -> None:
    """Best-effort typing indicator so the user sees activity during long work."""

    if not _should_send_immediate_feedback(normalized_message):
        return
    try:
        await send_read_receipt_and_typing_indicator(inbound_message_id)
    except Exception as exc:
        logger.warning("WhatsApp typing indicator skipped (continuing): %s", exc)


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    message_id: str
    wa_id: str
    phone_number: str
    profile_name: str | None
    message_type: str
    text: str
    payload: dict[str, Any]
    latitude: float | None = None
    longitude: float | None = None


def extract_messages(payload: dict[str, Any]) -> list[IncomingWhatsAppMessage]:
    messages: list[IncomingWhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            contacts_by_wa_id = {
                contact.get("wa_id"): contact
                for contact in value.get("contacts", [])
                if contact.get("wa_id")
            }
            for raw_message in value.get("messages", []):
                parsed = _parse_message(raw_message, contacts_by_wa_id)
                if parsed is not None:
                    messages.append(parsed)
    return messages


def _parse_message(
    raw_message: dict[str, Any],
    contacts_by_wa_id: dict[str, dict[str, Any]],
) -> IncomingWhatsAppMessage | None:
    message_id = raw_message.get("id")
    wa_id = raw_message.get("from")
    message_type = raw_message.get("type", "unknown")
    if not message_id or not wa_id:
        return None

    text = ""
    if message_type == "text":
        text = (raw_message.get("text") or {}).get("body", "")
    elif message_type == "image":
        image = raw_message.get("image") or {}
        text = str(image.get("caption") or "").strip()
    elif message_type == "interactive":
        interactive = raw_message.get("interactive") or {}
        if interactive.get("type") == "button_reply":
            reply = interactive.get("button_reply") or {}
            text = reply.get("title") or reply.get("id") or ""
        elif interactive.get("type") == "list_reply":
            reply = interactive.get("list_reply") or {}
            text = reply.get("title") or reply.get("id") or ""
        elif interactive.get("type") == "nfm_reply":
            reply = interactive.get("nfm_reply") or {}
            text = reply.get("body") or reply.get("name") or "Flow response"
    elif message_type == "button":
        button = raw_message.get("button") or {}
        text = button.get("text") or button.get("payload") or ""
    elif message_type == "location":
        location = raw_message.get("location") or {}
        text = str(location.get("address") or location.get("name") or "Shared location")

    text = text.strip()
    if not text and message_type != "image":
        text = "help"

    contact = contacts_by_wa_id.get(wa_id) or {}
    profile = contact.get("profile") or {}
    location = raw_message.get("location") or {}
    return IncomingWhatsAppMessage(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=wa_id,
        profile_name=profile.get("name"),
        message_type=message_type,
        text=text,
        payload=raw_message,
        latitude=_parse_float(location.get("latitude")),
        longitude=_parse_float(location.get("longitude")),
    )


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def handle_webhook_payload(payload: dict[str, Any]) -> None:
    try:
        await persistence.record_raw_webhook(None, payload)
    except Exception as exc:  # pragma: no cover - external persistence
        logger.warning("WhatsApp raw webhook persistence skipped: %s", exc)

    for message in extract_messages(payload):
        try:
            await handle_incoming_message(message)
        except Exception:
            logger.exception("WhatsApp message handling failed for %s", message.message_id)


def _inbound_to_doc(message: IncomingWhatsAppMessage) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "wa_id": message.wa_id,
        "phone_number": message.phone_number,
        "profile_name": message.profile_name,
        "message_type": message.message_type,
        "text": message.text,
        "payload": message.payload,
        "latitude": message.latitude,
        "longitude": message.longitude,
    }


def _inbound_from_doc(doc: dict[str, Any]) -> IncomingWhatsAppMessage:
    return IncomingWhatsAppMessage(
        message_id=str(doc.get("message_id") or ""),
        wa_id=str(doc.get("wa_id") or ""),
        phone_number=str(doc.get("phone_number") or doc.get("wa_id") or ""),
        profile_name=doc.get("profile_name"),
        message_type=str(doc.get("message_type") or "text"),
        text=str(doc.get("text") or ""),
        payload=doc.get("payload") or {},
        latitude=_parse_float(doc.get("latitude")),
        longitude=_parse_float(doc.get("longitude")),
    )


async def _finish_turn_and_drain(wa_id: str) -> None:
    """After the current turn (including search delivery), process parked inbounds in order."""
    while True:
        nxt = await persistence.pop_whatsapp_pending_inbound(wa_id)
        if nxt is None:
            return
        queued = _inbound_from_doc(nxt)
        try:
            existing = await persistence.get_whatsapp_session(wa_id)
            session_id = (existing or {}).get("session_id") or f"whatsapp-{wa_id}"
            await _dispatch_incoming_message(
                queued,
                session_id=session_id,
                is_returning=True,
                existing_session=existing,
            )
        except Exception:
            logger.exception("Queued WhatsApp message failed for %s", queued.message_id)


async def handle_incoming_message(message: IncomingWhatsAppMessage) -> None:
    t_start = time.monotonic()

    if await persistence.whatsapp_message_exists(message.message_id):
        logger.info("Skipping duplicate WhatsApp message %s", message.message_id)
        return

    t_dedup = time.monotonic()
    existing_session = await persistence.get_whatsapp_session(message.wa_id)
    t_session = time.monotonic()
    session_id = (existing_session or {}).get("session_id") or f"whatsapp-{message.wa_id}"
    is_returning = existing_session is not None

    # Fire both persistence writes in parallel — neither blocks downstream logic
    await asyncio.gather(
        persistence.record_whatsapp_message(
            message_id=message.message_id,
            wa_id=message.wa_id,
            phone_number=message.phone_number,
            session_id=session_id,
            direction="inbound",
            message_type=message.message_type,
            text=message.text,
            payload=message.payload,
        ),
        persistence.upsert_whatsapp_session(
            wa_id=message.wa_id,
            phone_number=message.phone_number,
            profile_name=message.profile_name,
            session_id=session_id,
            last_message=message.text,
        ),
    )
    t_persist = time.monotonic()
    logger.info(
        "WhatsApp pre-processing: dedup=%.2fs session_lookup=%.2fs persist=%.2fs total=%.2fs msg=%s",
        t_dedup - t_start, t_session - t_dedup, t_persist - t_session, t_persist - t_start,
        message.message_id,
    )

    if marketing_outreach.is_opt_out_cta(message.text):
        await _dispatch_incoming_message(
            message,
            session_id=session_id,
            is_returning=is_returning,
            existing_session=existing_session,
        )
        return

    if not await persistence.try_acquire_whatsapp_turn(message.wa_id):
        if marketing_outreach.is_get_quotes_cta(message.text):
            logger.info(
                "Ignoring duplicate Get Live quotes while a turn is in flight wa_id=%s",
                message.wa_id,
            )
            return
        await persistence.enqueue_whatsapp_inbound(message.wa_id, _inbound_to_doc(message))
        logger.info(
            "Parked WhatsApp inbound %s for wa_id=%s until the current turn finishes",
            message.message_id,
            message.wa_id,
        )
        return

    try:
        await _dispatch_incoming_message(
            message,
            session_id=session_id,
            is_returning=is_returning,
            existing_session=existing_session,
        )
    finally:
        await _finish_turn_and_drain(message.wa_id)


async def _dispatch_incoming_message(
    message: IncomingWhatsAppMessage,
    *,
    session_id: str,
    is_returning: bool,
    existing_session: dict[str, Any] | None,
) -> None:
    metadata = {
        "source": "whatsapp",
        "wa_id": message.wa_id,
        "phone_number": message.phone_number,
        "profile_name": message.profile_name,
        "message_type": message.message_type,
        "latitude": str(message.latitude) if message.latitude is not None else None,
        "longitude": str(message.longitude) if message.longitude is not None else None,
    }

    if message.message_type == "interactive":
        try:
            from app.whatsapp_flows.replies import maybe_handle_flow_reply

            handled = await maybe_handle_flow_reply(
                payload=message.payload,
                to_phone=message.phone_number,
                session_id=session_id,
            )
            if handled:
                return
        except Exception:
            logger.exception("WhatsApp Flow reply handling failed for %s", message.message_id)

    # Marketing on-ramp only: opt-out / Get Live Quotes. Everything else stays normal bot.
    if marketing_outreach.is_opt_out_cta(message.text):
        try:
            await marketing_outreach.record_opt_out(message.wa_id)
        except Exception:
            logger.exception("Marketing opt-out persist failed for %s", message.wa_id)
        await send_text_message(
            message.phone_number,
            "You’re unsubscribed from Zwig rate updates. You can still message anytime to search.",
            session_id=session_id,
        )
        return

    marketing_handoff = False
    if marketing_outreach.is_get_quotes_cta(message.text):
        try:
            ctx = await marketing_outreach.peek_pending_context(message.wa_id)
        except Exception:
            logger.exception("Marketing context lookup failed for %s", message.wa_id)
            ctx = None
        if ctx and (ctx.get("product_query") or "").strip():
            injected = str(ctx["product_query"]).strip()
            logger.info(
                "Marketing CTA injected query for wa_id=%s campaign=%s query=%r",
                message.wa_id,
                ctx.get("campaign_id"),
                injected,
            )
            message = replace(message, text=injected)
            marketing_handoff = True
            from app.services.search_wait_copy import SEARCH_WAIT_START

            await send_text_message(
                message.phone_number,
                marketing_outreach.search_started_copy(injected),
                session_id=session_id,
            )
            await send_text_message(
                message.phone_number,
                SEARCH_WAIT_START,
                session_id=session_id,
            )
            try:
                await marketing_outreach.mark_context_queued(message.wa_id)
            except Exception:
                logger.exception("Marketing context queue mark failed for %s", message.wa_id)
        else:
            await send_text_message(
                message.phone_number,
                "Tell Zwig what you need — product and city — and I’ll find live supplier quotes.",
                session_id=session_id,
            )
            return

    if marketing_handoff:
        metadata["marketing_handoff"] = True
        # Skip Priya intake — go straight to search results for campaign CTAs.
        metadata["marketing_direct"] = True
        metadata["search_started_sent"] = True

    normalized = message.text.strip().lower()
    if normalized in {"yes, sure", "yes sure", "show more", "show more vendors", "show more vendor prices"}:
        last_search_id = (existing_session or {}).get("last_search_id")
        if last_search_id:
            await _send_more_results_for_search(
                to=message.phone_number,
                wa_id=message.wa_id,
                session_id=session_id,
                search_id=str(last_search_id),
            )
        else:
            await send_text_message(
                message.phone_number,
                "I do not have a recent search to continue. Please send the product and city again.",
                session_id=session_id,
            )
        return
    if normalized in {"no, i am satisfied", "no i am satisfied", "no", "no thanks"}:
        await send_text_message(
            message.phone_number,
            "Perfect. Send another product and city whenever you want me to search again.",
            session_id=session_id,
        )
        return

    if normalized in GREETING_TERMS or normalized == "search product":
        # Greeting/menu does not run through chat_session.process_message, but the same
        # session_id may still hold a prior raw_query from an earlier search. Clear it so
        # the user's next product message is not merged with stale context.
        await chat_session.reset_session_for_fresh_intake(
            session_id,
            request_metadata=metadata,
        )
        await send_welcome_message(message.phone_number, returning=is_returning)
        return

    if message.message_type == "image":
        image_payload = message.payload.get("image") or {}
        media_id = str(image_payload.get("id") or "")
        if not media_id:
            await send_text_message(
                message.phone_number,
                "I couldn't read that image. Please try sending it again.",
                session_id=session_id,
            )
            return
        try:
            await maybe_show_typing_while_processing(
                inbound_message_id=message.message_id,
                normalized_message=message.text or "image",
            )
            image_bytes, content_type = await download_whatsapp_media(media_id)
            response = await chat_session.process_image_intake(
                image_bytes,
                content_type=content_type,
                user_note=message.text or None,
                session_id=session_id,
                location=None,
                request_metadata=metadata,
            )
        except Exception as exc:
            logger.warning("WhatsApp image intake failed for %s: %s", message.message_id, exc)
            await send_text_message(
                message.phone_number,
                "Sorry, I couldn't analyze that image. Please describe the product in text.",
                session_id=session_id,
            )
            return
        search_id = response.search_progress.search_id if response.search_progress else None
        await persistence.upsert_whatsapp_session(
            wa_id=message.wa_id,
            phone_number=message.phone_number,
            profile_name=message.profile_name,
            session_id=response.session_id,
            last_message=message.text or "image",
            last_search_id=search_id,
        )
        if response.ready_to_search and response.search_progress:
            from app.services.search_wait_copy import SEARCH_WAIT_START

            if not metadata.get("search_started_sent"):
                await send_text_message(
                    message.phone_number,
                    response.assistant_message,
                    session_id=response.session_id,
                    search_id=search_id,
                )
                await send_text_message(
                    message.phone_number,
                    SEARCH_WAIT_START,
                    session_id=response.session_id,
                    search_id=search_id,
                )
            await persistence.set_whatsapp_active_search(message.wa_id, search_id)
            await send_search_results_when_ready(
                to=message.phone_number,
                wa_id=message.wa_id,
                session_id=response.session_id,
                search_id=response.search_progress.search_id,
                inbound_message_id=message.message_id,
            )
        else:
            await _send_intake_reply(to=message.phone_number, response=response)
        return

    user_message = message.text
    if message.message_type == "interactive":
        reply_id = _extract_interactive_reply_id(message.payload)
        if reply_id and (
            reply_id.startswith("other_city_yes:") or reply_id.startswith("other_city_no:")
        ):
            from app.services import search_progress as search_progress_service

            accept = reply_id.startswith("other_city_yes:")
            search_id = reply_id.split(":", 1)[1].strip()
            before = await search_progress_service.resolve_snapshot(search_id)
            before_ids = {result.id for result in (before.partial_results if before else [])}
            snapshot = await search_progress_service.respond_other_city_expand(
                search_id,
                accept=accept,
            )
            if snapshot is None:
                await send_text_message(
                    message.phone_number,
                    "I couldn’t find that search anymore. Please send your requirement again.",
                    session_id=session_id,
                )
                return
            if accept:
                new_results = [
                    result
                    for result in snapshot.partial_results
                    if result.id not in before_ids
                ]
                await send_text_message(
                    message.phone_number,
                    (
                        f"Got it — here are {len(new_results)} supplier quote"
                        f"{'' if len(new_results) == 1 else 's'} from other cities."
                        if new_results
                        else "Got it — checking suppliers from other cities."
                    ),
                    session_id=session_id,
                    search_id=search_id,
                )
                if new_results:
                    await send_text_message(
                        message.phone_number,
                        format_results_message(new_results),
                        session_id=session_id,
                        search_id=search_id,
                    )
                if snapshot.has_more_results:
                    await _send_more_results_prompt(
                        to=message.phone_number,
                        wa_id=message.wa_id,
                        session_id=session_id,
                        search_id=search_id,
                        next_offset=snapshot.next_result_offset,
                        total_results=snapshot.total_ranked_results,
                    )
            else:
                await send_text_message(
                    message.phone_number,
                    "Okay — I’ll stick with suppliers in your city.",
                    session_id=session_id,
                    search_id=search_id,
                )
            return
        if reply_id and (
            reply_id.startswith("brand_fallback_yes:") or reply_id.startswith("brand_fallback_no:")
        ):
            from app.services import search_progress as search_progress_service

            accept = reply_id.startswith("brand_fallback_yes:")
            search_id = reply_id.split(":", 1)[1].strip()
            before = await search_progress_service.resolve_snapshot(search_id)
            before_ids = {result.id for result in (before.partial_results if before else [])}
            snapshot = await search_progress_service.respond_brand_fallback_expand(
                search_id,
                accept=accept,
            )
            if snapshot is None:
                await send_text_message(
                    message.phone_number,
                    "I couldn’t find that search anymore. Please send your requirement again.",
                    session_id=session_id,
                )
                return
            if accept:
                new_results = [
                    result
                    for result in snapshot.partial_results
                    if result.id not in before_ids
                ]
                await send_text_message(
                    message.phone_number,
                    (
                        f"Here are {len(new_results)} other-brand supplier quote"
                        f"{'' if len(new_results) == 1 else 's'} in your city."
                        if new_results
                        else "Checking other-brand suppliers."
                    ),
                    session_id=session_id,
                    search_id=search_id,
                )
                if new_results:
                    await send_text_message(
                        message.phone_number,
                        format_results_message(new_results),
                        session_id=session_id,
                        search_id=search_id,
                    )
                if snapshot.has_more_results:
                    await _send_more_results_prompt(
                        to=message.phone_number,
                        wa_id=message.wa_id,
                        session_id=session_id,
                        search_id=search_id,
                        next_offset=snapshot.next_result_offset,
                        total_results=snapshot.total_ranked_results,
                    )
                prompt = snapshot.other_city_prompt
                if prompt and prompt.status == "awaiting" and prompt.message:
                    await send_button_message(
                        message.phone_number,
                        prompt.message,
                        buttons=[
                            (f"other_city_yes:{search_id}", "Yes, show them"),
                            (f"other_city_no:{search_id}", "No, thanks"),
                        ],
                        session_id=session_id,
                        search_id=search_id,
                    )
            else:
                await send_text_message(
                    message.phone_number,
                    "Okay — I’ll stick with the online brand options for now.",
                    session_id=session_id,
                    search_id=search_id,
                )
            return
        user_message = await chat_session.resolve_whatsapp_suggestion_reply(
            session_id,
            reply_id=reply_id,
            fallback_text=message.text,
        )
    resolved_location: str | None = None

    # Fire typing indicator and location resolution in parallel with each other
    # (process_message will run after since it depends on resolved_location)
    typing_coro = maybe_show_typing_while_processing(
        inbound_message_id=message.message_id,
        normalized_message=normalized,
    )
    if message.message_type == "location":
        typing_task: asyncio.Task[None] = asyncio.create_task(typing_coro)
        try:
            resolved_location = await resolve_shared_location(message, session_id=session_id)
            user_message = resolved_location or message.text
        finally:
            try:
                await typing_task
            except Exception:
                logger.debug("Typing indicator task finished with error", exc_info=True)
    else:
        await typing_coro

    t_before_process = time.monotonic()
    try:
        response = await chat_session.process_message(
            user_message,
            session_id=session_id,
            location=resolved_location,
            request_metadata=metadata,
            default_urgency="immediate",
        )
    except Exception:
        if marketing_handoff:
            logger.exception("Marketing direct search failed for %s", message.wa_id)
            try:
                await marketing_outreach.restore_pending_context(message.wa_id)
            except Exception:
                logger.exception("Marketing context restore failed for %s", message.wa_id)
            await send_text_message(
                message.phone_number,
                "I couldn’t start that search. Tap Get Live quotes again and I’ll retry.",
                session_id=session_id,
            )
            return
        raise
    t_after_process = time.monotonic()
    logger.info(
        "WhatsApp process_message took %.2fs for session=%s awaiting=%s ready=%s",
        t_after_process - t_before_process,
        session_id,
        response.state.awaiting_field,
        response.ready_to_search,
    )

    search_id = response.search_progress.search_id if response.search_progress else None
    await persistence.upsert_whatsapp_session(
        wa_id=message.wa_id,
        phone_number=message.phone_number,
        profile_name=message.profile_name,
        session_id=response.session_id,
        last_message=message.text,
        last_search_id=search_id,
    )
    if search_id:
        await persistence.set_whatsapp_active_search(message.wa_id, search_id)
        if marketing_handoff:
            try:
                await marketing_outreach.mark_context_queued(message.wa_id, search_id=search_id)
            except Exception:
                logger.exception("Marketing context search_id update failed for %s", message.wa_id)

    if response.ready_to_search and response.search_progress:
        from app.services.search_wait_copy import SEARCH_WAIT_START

        if not metadata.get("search_started_sent"):
            # Send Priya's confirmation message as-is, then wait for search delivery
            await send_text_message(
                message.phone_number,
                response.assistant_message,
                session_id=response.session_id,
                search_id=response.search_progress.search_id,
            )
            await send_text_message(
                message.phone_number,
                SEARCH_WAIT_START,
                session_id=response.session_id,
                search_id=response.search_progress.search_id,
            )
        await send_search_results_when_ready(
            to=message.phone_number,
            wa_id=message.wa_id,
            session_id=response.session_id,
            search_id=response.search_progress.search_id,
            inbound_message_id=message.message_id,
        )
        return

    await _send_intake_reply(to=message.phone_number, response=response)


async def _send_intake_reply(
    *,
    to: str,
    response: ChatMessageResponse,
) -> None:
    search_id = response.search_progress.search_id if response.search_progress else None
    suggestions = response.suggested_replies or []
    mapping = await send_reply_with_suggestions(
        to=to,
        body=response.assistant_message,
        suggestions=suggestions,
        session_id=response.session_id,
        search_id=search_id,
    )
    await chat_session.save_whatsapp_suggestion_map(response.session_id, mapping)


async def resolve_shared_location(message: IncomingWhatsAppMessage, *, session_id: str) -> str | None:
    if message.latitude is None or message.longitude is None:
        await send_text_message(
            message.phone_number,
            "I could not read that location. Please type your city or pincode instead.",
            session_id=session_id,
        )
        return None
    try:
        location, formatted_address = await location_resolver.resolve_location_from_coordinates(
            message.latitude,
            message.longitude,
        )
        logger.info("Resolved WhatsApp location for %s to %s (%s)", message.wa_id, location, formatted_address)
        return formatted_address or location
    except Exception as exc:  # pragma: no cover - external geocoder
        logger.warning("Could not resolve WhatsApp shared location for %s: %s", message.wa_id, exc)
        await send_text_message(
            message.phone_number,
            "I could not resolve that location. Please type your city or pincode instead.",
            session_id=session_id,
        )
        return None


async def send_welcome_message(to: str, *, returning: bool) -> None:
    body = (
        "👋 Welcome to Zwig.\n\n"
        "Describe what you need to procure, and I'll source verified suppliers and "
        "negotiate the best available deal."
    )
    await send_text_message(to, body)


async def send_reply_with_suggestions(
    *,
    to: str,
    body: str,
    suggestions: list[str],
    session_id: str,
    search_id: str | None = None,
) -> dict[str, str]:
    """Send Priya's question with WhatsApp reply buttons (≤3) or a list (4–10 options).

    Returns a mapping of reply ID → full suggestion label for inbound resolution.
    """
    cleaned = [str(item).strip() for item in suggestions if str(item).strip()]
    if not cleaned:
        await send_text_message(to, body, session_id=session_id, search_id=search_id)
        return {}

    entries, mapping = _build_whatsapp_suggestion_entries(cleaned)
    if len(entries) <= 3:
        await send_button_message(
            to,
            body,
            buttons=[(entry["id"], entry["title"]) for entry in entries],
            session_id=session_id,
            search_id=search_id,
        )
    else:
        await send_list_message(
            to,
            body,
            rows=[
                {
                    "id": entry["id"],
                    "title": entry["list_title"],
                    **({"description": entry["description"]} if entry["description"] else {}),
                }
                for entry in entries
            ],
            session_id=session_id,
            search_id=search_id,
        )
    return mapping


def _whatsapp_delivery_address(search_doc: dict[str, Any]) -> tuple[str, str, str] | None:
    delivery = search_doc.get("whatsapp_delivery") or {}
    metadata = search_doc.get("request_metadata") or {}
    wa_id = delivery.get("wa_id") or metadata.get("wa_id")
    to = delivery.get("phone_number") or metadata.get("phone_number") or wa_id
    session_id = delivery.get("session_id") or search_doc.get("session_id")
    if not wa_id or not to or not session_id:
        logger.warning("Missing WhatsApp delivery address for search=%s", search_doc.get("search_id"))
        return None
    return str(to), str(wa_id), str(session_id)


def _results_from_search_doc(search_doc: dict[str, Any]) -> list[UnifiedResult]:
    results: list[UnifiedResult] = []
    for raw_result in search_doc.get("final_results") or search_doc.get("top_results") or []:
        try:
            results.append(UnifiedResult.model_validate(raw_result))
        except Exception as exc:
            logger.warning(
                "Skipping malformed persisted result for search=%s: %s",
                search_doc.get("search_id"),
                exc,
            )
    return results


async def _send_once_text_message(
    *,
    message_id: str,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    message_type: str,
    text: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    inserted = await persistence.try_record_whatsapp_message(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=to,
        session_id=session_id,
        direction="outbound",
        message_type=message_type,
        text=text,
        payload=payload or {},
        search_id=search_id,
        status="sending",
    )
    if not inserted:
        retry_claimed = await persistence.claim_failed_whatsapp_message_retry(message_id)
        if not retry_claimed:
            logger.info("Skipping duplicate WhatsApp outbound message_id=%s search=%s", message_id, search_id)
            return False

    try:
        await send_text_message(to, text, session_id=session_id, search_id=search_id)
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="sent",
            payload=payload or {},
        )
        return True
    except Exception:
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="failed",
            payload=payload or {},
        )
        raise


def _preview_dict(result: UnifiedResult) -> dict[str, Any]:
    preview = (result.attributes or {}).get("product_preview") if result.attributes else None
    return preview if isinstance(preview, dict) else {}


def _is_unit_spec_label(label: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(label or "").lower())
    return compact in {"unit", "units", "priceunit", "packagingunit"}


def _result_price_unit(preview: dict[str, Any]) -> str:
    unit = _clean_whatsapp_value(preview.get("unit"))
    if unit:
        return unit
    specs = preview.get("specs") if isinstance(preview.get("specs"), list) else []
    for item in specs:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        value = item.get("value")
        if _is_unit_spec_label(label) and value not in (None, ""):
            cleaned = _clean_whatsapp_value(value)
            if cleaned:
                return cleaned
    return ""


def _format_money(result: UnifiedResult, preview: dict[str, Any]) -> str | None:
    currency = result.display_currency or preview.get("currency") or result.currency or "INR"
    value = result.display_price
    if value is None:
        value = preview.get("price")
    if value is None:
        value = result.price
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if currency == "INR":
        formatted = f"₹{numeric:,.0f}"
    else:
        formatted = f"{currency} {numeric:,.0f}"
    unit = _result_price_unit(preview)
    if unit:
        formatted = f"{formatted} / {unit}"
    return formatted


def _clean_whatsapp_value(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _seller_location_parts(result: UnifiedResult, preview: dict[str, Any]) -> tuple[str, str, str]:
    city = _clean_whatsapp_value(preview.get("supplier_city") or result.city)
    state = _clean_whatsapp_value(preview.get("supplier_state"))
    location = _clean_whatsapp_value(preview.get("supplier_location") or result.address)
    if not location:
        location = ", ".join(part for part in [city, state] if part)
    return location, city, state


def _is_internal_spec_label(label: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(label or "").lower()).strip()
    if not normalized:
        return True
    compact = normalized.replace(" ", "")
    if "indiamart" in compact:
        return True
    internal_tokens = {"id", "mcat", "catid", "mcatid", "source", "sourceurl", "url", "link"}
    if compact in internal_tokens:
        return True
    if compact.endswith("id") and any(token in compact for token in ("cat", "mcat", "listing", "product", "supplier", "vendor")):
        return True
    return False


def _is_non_product_spec_label(label: Any) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(label or "").lower())
    return compact in {"availability", "suppliertype"}


def _quote_card_text(result: UnifiedResult) -> str:
    preview = _preview_dict(result)
    title = _clean_whatsapp_value(preview.get("title") or result.name or "Product")
    price = _format_money(result, preview)
    availability = _clean_whatsapp_value(preview.get("available") or ("In stock" if result.availability else "Availability not confirmed"))
    description = _clean_whatsapp_value(preview.get("description"))
    supplier_name = _clean_whatsapp_value(preview.get("supplier_name"))
    if not supplier_name:
        supplier_name = _clean_whatsapp_value(result.name.split("|", 1)[0] if "|" in result.name else "")
    seller_phone = _clean_whatsapp_value(preview.get("supplier_phone") or result.phone)
    location, city, state = _seller_location_parts(result, preview)
    rating = preview.get("supplier_rating")
    rating_count = preview.get("supplier_rating_count")
    moq = _clean_whatsapp_value(preview.get("moq"))
    delivery = _clean_whatsapp_value(result.delivery_time)

    if price:
        intro = f"{title} is available for {price}."
        if availability:
            intro = f"{intro} Availability: {availability}."
    else:
        intro = (
            f"{title} — *Price on request*.\n"
            "*Availability checked. Contact the vendor for exact price and product specifications.*"
        )
    if delivery:
        intro = f"{intro} Delivery: {delivery}."
    if description:
        intro = f"{intro}\n\n{description[:280]}"

    lines = [intro, ""]
    if seller_phone:
        lines.append(f"Seller Contact: {seller_phone}")
    if supplier_name:
        lines.append(f"Seller Name: {supplier_name}")
    if location:
        lines.append(f"Location: {location}")
    if city:
        lines.append(f"City: {city}")
    if state:
        lines.append(f"State: {state}")
    if moq:
        lines.append(f"MOQ: {moq}")
    if rating:
        rating_text = f"Rating: {rating}"
        if rating_count:
            rating_text += f" (based on {rating_count} reviews)"
        lines.append(rating_text)

    specs = preview.get("specs") if isinstance(preview.get("specs"), list) else []
    spec_lines: list[str] = []
    for item in specs:
        if isinstance(item, dict) and item.get("label") and item.get("value"):
            label = item["label"]
            if (
                _is_internal_spec_label(label)
                or _is_non_product_spec_label(label)
                or _is_unit_spec_label(label)
            ):
                continue
            spec_lines.append(f"{label}: {item['value']}")
        if len(spec_lines) >= 4:
            break
    if spec_lines:
        lines.extend(["", "Product Details:", *spec_lines])
    return "\n".join(lines).strip()


def _quote_card_image_url(result: UnifiedResult) -> str | None:
    preview = _preview_dict(result)
    image_url = _clean_whatsapp_value(preview.get("image_url"))
    if not image_url:
        return None
    if not image_url.lower().startswith(("http://", "https://")):
        return None
    return image_url


def _result_seller_phone(result: UnifiedResult) -> str | None:
    preview = _preview_dict(result)
    phone = _clean_whatsapp_value(preview.get("supplier_phone") or result.phone)
    return phone or None


def normalize_vendor_phone_e164(raw: str) -> str | None:
    """Normalize a vendor phone to E.164 digits without the leading +."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 10:
        digits = f"91{digits}"
    elif len(digits) == 11 and digits.startswith("0"):
        digits = f"91{digits[1:]}"
    if len(digits) < 10 or len(digits) > 15:
        return None
    return digits


def _whatsapp_public_base_url() -> str:
    return (settings.voice_webhook_base_url or "").rstrip("/")


def build_vendor_call_url(phone: str) -> str | None:
    """Build an https redirect URL that opens the vendor phone dialer on tap."""
    normalized = normalize_vendor_phone_e164(phone)
    base = _whatsapp_public_base_url()
    if not normalized or not base:
        return None
    return f"{base}/api/whatsapp/call-vendor?phone={normalized}"


def _is_indiamart_url(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return host == "indiamart.com" or host.endswith(".indiamart.com")
    except Exception:
        return False


def _is_live_dealer_rate(result: UnifiedResult) -> bool:
    if (result.result_type or "").strip().lower() == "live_rate":
        return True
    haystack = " ".join(part for part in [result.delivery_time or "", result.notes or ""] if part).lower()
    if any(token in haystack for token in ("live dealer", "live-rate", "chirayu_api")):
        return True
    return bool(re.search(r"(^|\|)\s*(buy|sell)=", result.notes or "", re.I))


def _result_link_label(result: UnifiedResult) -> str:
    return "Open dealer site" if _is_live_dealer_rate(result) else "View product"


def _result_product_url(result: UnifiedResult) -> str | None:
    """External product/dealer URL — mirrors web `showProductLink` (result.url, not IndiaMART)."""
    url = _clean_whatsapp_value(result.url)
    if not url.lower().startswith(("http://", "https://")):
        return None
    if _is_indiamart_url(url):
        return None
    return url


def _result_card_cta(result: UnifiedResult) -> tuple[str, str, str] | None:
    """Pick a single card CTA: web link when available, otherwise call vendor."""
    product_url = _result_product_url(result)
    if product_url:
        return ("product", _result_link_label(result), product_url)
    seller_phone = _result_seller_phone(result)
    if seller_phone:
        call_url = build_vendor_call_url(seller_phone)
        if call_url:
            return ("call", "Call vendor", call_url)
    return None


async def _send_result_card_cta(
    *,
    to: str,
    body: str,
    button_label: str,
    button_url: str,
    image_url: str | None,
    session_id: str,
    search_id: str,
) -> None:
    await send_cta_url_message(
        to,
        body,
        button_label=button_label,
        button_url=button_url,
        image_url=image_url,
        session_id=session_id,
        search_id=search_id,
    )


async def _send_result_card_content(
    *,
    to: str,
    text: str,
    image_url: str | None,
    result: UnifiedResult,
    session_id: str,
    search_id: str,
    payload: dict[str, Any],
) -> None:
    cta = _result_card_cta(result)
    if cta:
        cta_type, label, url = cta
        try:
            await _send_result_card_cta(
                to=to,
                body=text,
                button_label=label,
                button_url=url,
                image_url=image_url,
                session_id=session_id,
                search_id=search_id,
            )
            if cta_type == "product":
                payload["product_link_cta_sent"] = True
            else:
                payload["call_cta_sent"] = True
            return
        except Exception as exc:
            logger.warning(
                "WhatsApp %s CTA failed; falling back for search=%s: %s",
                cta_type,
                search_id,
                exc,
            )
            payload[f"{cta_type}_cta_error"] = str(exc)

    if image_url:
        try:
            await send_image_message(to, image_url, text, session_id=session_id, search_id=search_id)
            payload["image_sent"] = True
            return
        except Exception as exc:
            logger.warning(
                "WhatsApp image result failed; falling back to text search=%s url=%s: %s",
                search_id,
                image_url,
                exc,
            )
            payload["image_sent"] = False
            payload["image_error"] = str(exc)
    await send_text_message(to, text, session_id=session_id, search_id=search_id)


async def _send_once_result_card_message(
    *,
    message_id: str,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    result: UnifiedResult,
) -> bool:
    text = _quote_card_text(result)
    image_url = _quote_card_image_url(result)
    payload = {
        "search_id": search_id,
        "result": result.model_dump(mode="json"),
        "image_url": image_url,
    }
    inserted = await persistence.try_record_whatsapp_message(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=to,
        session_id=session_id,
        direction="outbound",
        message_type="quote_result",
        text=text,
        payload=payload,
        search_id=search_id,
        status="sending",
    )
    if not inserted:
        retry_claimed = await persistence.claim_failed_whatsapp_message_retry(message_id)
        if not retry_claimed:
            logger.info("Skipping duplicate WhatsApp result card message_id=%s search=%s", message_id, search_id)
            return False

    try:
        await _send_result_card_content(
            to=to,
            text=text,
            image_url=image_url,
            result=result,
            session_id=session_id,
            search_id=search_id,
            payload=payload,
        )
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="sent",
            payload=payload,
        )
        return True
    except Exception:
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="failed",
            payload=payload,
        )
        raise


async def _maybe_send_marketing_handoff(
    *,
    search_doc: dict[str, Any],
    to: str,
    session_id: str,
    search_id: str,
) -> None:
    """After a marketing-triggered search finishes, return the user to free-form chat."""
    if not (search_doc.get("request_metadata") or {}).get("marketing_handoff"):
        return
    try:
        await send_text_message(
            to,
            marketing_outreach.HANDOFF_MESSAGE,
            session_id=session_id,
            search_id=search_id,
        )
    except Exception:
        logger.exception("Marketing handoff message failed for search=%s", search_id)


async def send_completed_search_from_persistence(search_id: str) -> bool:
    search_doc = await persistence.get_search_session(search_id)
    if not search_doc or search_doc.get("status") not in {"completed", "failed"}:
        return False
    if (search_doc.get("request_metadata") or {}).get("source") != "whatsapp":
        return False
    return await _send_completed_search_from_doc(search_doc)


async def _send_completed_search_from_doc(search_doc: dict[str, Any]) -> bool:
    search_id = str(search_doc.get("search_id") or "")
    address = _whatsapp_delivery_address(search_doc)
    if not search_id or address is None:
        return False

    to, wa_id, session_id = address
    delivery = search_doc.get("whatsapp_delivery") or {}
    status = search_doc.get("status")
    if status == "failed":
        message_id = f"whatsapp-failed-{search_id}"
        message_type = "status"
        text = "I could not complete this search. Please try again with a more specific product and city/pincode."
        payload = {"search_id": search_id, "error": search_doc.get("error")}
        final_status = "failed_sent"
    else:
        results = _results_from_search_doc(search_doc)
        message_id = f"whatsapp-results-{search_id}"
        message_type = "results"
        prefix = "The full search is complete now.\n\n" if delivery.get("status") == "timeout_sent" else ""
        text = f"{prefix}{format_results_message(results)}"
        payload = {"search_id": search_id, "results": [result.model_dump(mode="json") for result in results]}
        final_status = "final_sent"

        if _should_send_gold_live_rates_flow(search_doc.get("query"), results):
            try:
                sent = await _try_send_gold_live_rates_flow_with_fallback(
                    message_id=message_id,
                    fallback_message_id=f"{message_id}-text",
                    to=to,
                    wa_id=wa_id,
                    session_id=session_id,
                    search_id=search_id,
                    results=results,
                    fallback_text=text,
                    payload=payload,
                )
            except Exception as exc:
                await persistence.update_whatsapp_delivery(
                    search_id=search_id,
                    status="send_failed",
                    extra={"last_error": str(exc), "last_message_id": message_id},
                )
                raise
            await persistence.update_whatsapp_delivery(
                search_id=search_id,
                status=final_status,
                extra={"last_message_id": message_id, "sent_new_results": sent},
            )
            await _maybe_send_marketing_handoff(
                search_doc=search_doc, to=to, session_id=session_id, search_id=search_id
            )
            return True

        if results:
            first_page = results[: search_progress.RESULT_PAGE_SIZE]
            next_offset = min(search_progress.RESULT_PAGE_SIZE, len(results))
            try:
                sent = await _send_result_cards(
                    to=to,
                    wa_id=wa_id,
                    session_id=session_id,
                    search_id=search_id,
                    results=first_page,
                )
            except Exception as exc:
                await persistence.update_whatsapp_delivery(
                    search_id=search_id,
                    status="send_failed",
                    extra={"last_error": str(exc), "last_message_id": message_id},
                )
                raise
            await persistence.update_whatsapp_delivery(
                search_id=search_id,
                status=final_status,
                extra={"last_message_id": message_id, "sent_new_results": sent, "next_result_offset": next_offset},
            )
            if len(results) > next_offset:
                await _send_more_results_prompt(
                    to=to,
                    wa_id=wa_id,
                    session_id=session_id,
                    search_id=search_id,
                    next_offset=next_offset,
                    total_results=len(results),
                )
            await _maybe_send_marketing_handoff(
                search_doc=search_doc, to=to, session_id=session_id, search_id=search_id
            )
            return True

    try:
        sent = await _send_once_text_message(
            message_id=message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=search_id,
            message_type=message_type,
            text=text,
            payload=payload,
        )
    except Exception as exc:
        await persistence.update_whatsapp_delivery(
            search_id=search_id,
            status="send_failed",
            extra={"last_error": str(exc), "last_message_id": message_id},
        )
        raise

    if sent:
        await persistence.update_whatsapp_delivery(
            search_id=search_id,
            status=final_status,
            extra={"last_message_id": message_id},
        )
        await _maybe_send_marketing_handoff(
            search_doc=search_doc, to=to, session_id=session_id, search_id=search_id
        )
    return True


async def send_search_results_when_ready(
    *,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    inbound_message_id: str,
) -> None:
    from app.services.search_wait_copy import SEARCH_WAIT_NUDGE, SEARCH_WAIT_TIMEOUT

    elapsed = 0.0
    interval = settings.whatsapp_result_poll_interval_seconds
    heartbeat_interval = settings.whatsapp_typing_heartbeat_seconds
    # After the "Starting your search..." outbound send, Meta clears typing immediately.
    # Start elapsed so the first loop iteration refreshes typing without waiting a full heartbeat.
    time_since_last_typing = heartbeat_interval
    sent_result_ids: set[str] = set()
    typing_heartbeat_enabled = bool(inbound_message_id and heartbeat_interval > 0)
    wait_nudge_sent = False
    quiet_since_result = 0.0
    while elapsed < settings.whatsapp_result_timeout_seconds:
        snapshot = await search_progress.resolve_snapshot(search_id)
        if snapshot and snapshot.status in {"completed", "failed"}:
            await _send_new_search_results_update(to, wa_id, session_id, snapshot, sent_result_ids)
            await _send_completed_search(to, wa_id, session_id, snapshot, sent_result_ids=sent_result_ids)
            return
        if await send_completed_search_from_persistence(search_id):
            return
        if snapshot and snapshot.partial_results:
            sent_any = await _send_new_search_results_update(to, wa_id, session_id, snapshot, sent_result_ids)
            if sent_any:
                time_since_last_typing = 0.0  # outbound message dismisses typing, reset counter
                quiet_since_result = 0.0
            else:
                quiet_since_result += interval
        else:
            quiet_since_result += interval

        # Quiet gap with no new supplier cards — reassure once.
        if (
            not wait_nudge_sent
            and quiet_since_result >= 20
            and snapshot
            and snapshot.status not in {"completed", "failed"}
        ):
            sent_nudge = await _send_once_text_message(
                message_id=f"whatsapp-wait-nudge-{search_id}",
                to=to,
                wa_id=wa_id,
                session_id=session_id,
                search_id=search_id,
                message_type="status",
                text=SEARCH_WAIT_NUDGE,
                payload={"search_id": search_id},
            )
            wait_nudge_sent = True
            if sent_nudge:
                time_since_last_typing = 0.0

        # Refresh typing indicator before Meta's ~25s auto-dismiss
        if typing_heartbeat_enabled and time_since_last_typing >= heartbeat_interval:
            try:
                await _refresh_typing_indicator(inbound_message_id)
            except Exception as exc:
                logger.warning("WhatsApp typing heartbeat failed; disabling for this search: %s", exc)
                typing_heartbeat_enabled = False
            time_since_last_typing = 0.0

        await asyncio.sleep(interval)
        elapsed += interval
        time_since_last_typing += interval

    snapshot = await search_progress.resolve_snapshot(search_id)
    if snapshot and snapshot.status in {"completed", "failed"}:
        await _send_new_search_results_update(to, wa_id, session_id, snapshot, sent_result_ids)
        await _send_completed_search(to, wa_id, session_id, snapshot, sent_result_ids=sent_result_ids)
        return
    if await send_completed_search_from_persistence(search_id):
        return

    timeout_message = SEARCH_WAIT_TIMEOUT
    sent = await _send_once_text_message(
        message_id=f"whatsapp-timeout-{search_id}",
        to=to,
        wa_id=wa_id,
        session_id=session_id,
        search_id=search_id,
        message_type="status",
        text=timeout_message,
        payload={"search_id": search_id},
    )
    if sent:
        await persistence.update_whatsapp_delivery(search_id=search_id, status="timeout_sent")


async def _send_partial_search_update(
    to: str,
    wa_id: str,
    session_id: str,
    snapshot: SearchProgressSnapshot,
) -> None:
    results = snapshot.partial_results[:3]
    if results:
        message = (
            "Still checking offline vendors. Here are the results I have so far:\n\n"
            f"{format_results_message(results)}"
        )
        message_type = "partial_results"
        payload_results = [result.model_dump(mode="json") for result in results]
    else:
        message = "Still searching. I found vendors and am waiting for quotes/results now."
        message_type = "status"
        payload_results = []

    sent = await _send_once_text_message(
        message_id=f"whatsapp-partial-{snapshot.search_id}",
        to=to,
        wa_id=wa_id,
        session_id=session_id,
        search_id=snapshot.search_id,
        message_type=message_type,
        text=message,
        payload={"search_id": snapshot.search_id, "results": payload_results},
    )
    if sent:
        await persistence.update_whatsapp_delivery(search_id=snapshot.search_id, status="partial_sent")


def _result_delivery_key(result: UnifiedResult) -> str:
    attrs = result.attributes or {}
    return str(
        attrs.get("offering_id")
        or result.id
        or result.url
        or f"{result.source_type}:{result.result_type}:{result.name}:{result.price}:{result.phone}"
    )


def _message_safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")[:80] or "result"


async def _send_new_search_results_update(
    to: str,
    wa_id: str,
    session_id: str,
    snapshot: SearchProgressSnapshot,
    sent_result_ids: set[str],
) -> bool:
    remaining_slots = max(0, search_progress.RESULT_PAGE_SIZE - len(sent_result_ids))
    if remaining_slots <= 0:
        return False

    new_results: list[UnifiedResult] = []
    for result in snapshot.partial_results:
        key = _result_delivery_key(result)
        if key in sent_result_ids:
            continue
        new_results.append(result)
        if len(new_results) >= remaining_slots:
            break

    if not new_results:
        return False

    sent_any = False
    for result in new_results:
        key = _result_delivery_key(result)
        sent_result_ids.add(key)
        message_id = f"whatsapp-result-card-{snapshot.search_id}-{_message_safe_id(key)}"
        sent = await _send_once_result_card_message(
            message_id=message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=snapshot.search_id,
            result=result,
        )
        sent_any = sent_any or sent

    if sent_any:
        await persistence.update_whatsapp_delivery(search_id=snapshot.search_id, status="partial_sent")
    return sent_any


async def _send_result_cards(
    *,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    results: list[UnifiedResult],
    sent_result_ids: set[str] | None = None,
) -> bool:
    sent_any = False
    for result in results:
        key = _result_delivery_key(result)
        if sent_result_ids is not None:
            if key in sent_result_ids:
                continue
            sent_result_ids.add(key)
        message_id = f"whatsapp-result-card-{search_id}-{_message_safe_id(key)}"
        sent = await _send_once_result_card_message(
            message_id=message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=search_id,
            result=result,
        )
        sent_any = sent_any or sent
    return sent_any


async def _send_more_results_prompt(
    *,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    next_offset: int,
    total_results: int,
) -> None:
    remaining = max(0, total_results - next_offset)
    if remaining <= 0:
        return
    text = "Do you want more supplier quotes?"
    await _send_once_button_message(
        message_id=f"whatsapp-more-results-prompt-{search_id}-{next_offset}",
        to=to,
        wa_id=wa_id,
        session_id=session_id,
        search_id=search_id,
        text=text,
        buttons=["Yes, Sure", "No, I am satisfied"],
        payload={"search_id": search_id, "next_offset": next_offset, "remaining": remaining},
    )


async def _send_once_button_message(
    *,
    message_id: str,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    text: str,
    buttons: list[str],
    payload: dict[str, Any] | None = None,
) -> bool:
    inserted = await persistence.try_record_whatsapp_message(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=to,
        session_id=session_id,
        direction="outbound",
        message_type="button",
        text=text,
        payload=payload or {},
        search_id=search_id,
        status="sending",
    )
    if not inserted:
        retry_claimed = await persistence.claim_failed_whatsapp_message_retry(message_id)
        if not retry_claimed:
            logger.info("Skipping duplicate WhatsApp button message_id=%s search=%s", message_id, search_id)
            return False

    try:
        await send_button_message(
            to,
            text,
            buttons=buttons,
            session_id=session_id,
            search_id=search_id,
        )
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="sent",
            payload=payload or {},
        )
        return True
    except Exception:
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="failed",
            payload=payload or {},
        )
        raise


async def _send_more_results_for_search(
    *,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
) -> None:
    response = await search_progress.get_more_results(search_id)
    if response is None:
        await send_text_message(
            to,
            "I could not find the previous search anymore. Please send the product and city again.",
            session_id=session_id,
        )
        return
    if not response.results:
        await send_text_message(
            to,
            "No more vendor prices are available for this search.",
            session_id=session_id,
            search_id=search_id,
        )
        return
    await _send_result_cards(
        to=to,
        wa_id=wa_id,
        session_id=session_id,
        search_id=search_id,
        results=response.results,
    )
    await persistence.update_whatsapp_delivery(
        search_id=search_id,
        status="final_sent",
        extra={"next_result_offset": response.next_offset},
    )
    if response.has_more:
        await _send_more_results_prompt(
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=search_id,
            next_offset=response.next_offset,
            total_results=response.total_results,
        )


async def _maybe_send_marketing_handoff_for_search(
    *,
    search_id: str,
    to: str,
    session_id: str,
) -> None:
    search_doc = await persistence.get_search_session(search_id)
    if not search_doc:
        return
    await _maybe_send_marketing_handoff(
        search_doc=search_doc, to=to, session_id=session_id, search_id=search_id
    )


async def _send_completed_search(
    to: str,
    wa_id: str,
    session_id: str,
    snapshot: SearchProgressSnapshot,
    *,
    sent_result_ids: set[str] | None = None,
) -> None:
    if snapshot.status == "failed":
        message_id = f"whatsapp-failed-{snapshot.search_id}"
        try:
            sent = await _send_once_text_message(
                message_id=message_id,
                to=to,
                wa_id=wa_id,
                session_id=session_id,
                search_id=snapshot.search_id,
                message_type="status",
                text="I could not complete this search. Please try again with a more specific product and city/pincode.",
                payload={"search_id": snapshot.search_id},
            )
        except Exception as exc:
            await persistence.update_whatsapp_delivery(
                search_id=snapshot.search_id,
                status="send_failed",
                extra={"last_error": str(exc), "last_message_id": message_id},
            )
            raise
        if sent:
            await persistence.update_whatsapp_delivery(
                search_id=snapshot.search_id,
                status="failed_sent",
                extra={"last_message_id": message_id},
            )
        return

    results = snapshot.final_results.results if snapshot.final_results else snapshot.partial_results

    # No local live rates for this city, but it's a bullion-style query: surface
    # today's NATIONAL bullion rate (clearly labelled) instead of a bare "no
    # results", mirroring the gold.zwig.in desk. Non-bullion (jewellery/scrap)
    # queries fail the gate and fall through to the normal message.
    if not results and chat_session.gold_query_has_live_rates(snapshot.query):
        national_text = await _gold_national_fallback_text(snapshot.query.model_dump(mode="json"))
        if national_text:
            message_id = f"whatsapp-results-{snapshot.search_id}"
            try:
                sent = await _send_once_text_message(
                    message_id=message_id,
                    to=to,
                    wa_id=wa_id,
                    session_id=session_id,
                    search_id=snapshot.search_id,
                    message_type="results",
                    text=national_text,
                    payload={"search_id": snapshot.search_id, "national_fallback": True},
                )
            except Exception as exc:
                await persistence.update_whatsapp_delivery(
                    search_id=snapshot.search_id,
                    status="send_failed",
                    extra={"last_error": str(exc), "last_message_id": message_id},
                )
                raise
            if sent:
                await persistence.update_whatsapp_delivery(
                    search_id=snapshot.search_id,
                    status="final_sent",
                    extra={"last_message_id": message_id},
                )
                await _maybe_send_marketing_handoff_for_search(
                    search_id=snapshot.search_id, to=to, session_id=session_id
                )
            return

    formatted_results = format_results_message(results)
    message_id = f"whatsapp-results-{snapshot.search_id}"
    payload = {
        "search_id": snapshot.search_id,
        "results": [result.model_dump(mode="json") for result in results],
    }

    if _should_send_gold_live_rates_flow(snapshot.query.model_dump(mode="json"), results):
        try:
            sent = await _try_send_gold_live_rates_flow_with_fallback(
                message_id=message_id,
                fallback_message_id=f"{message_id}-text",
                to=to,
                wa_id=wa_id,
                session_id=session_id,
                search_id=snapshot.search_id,
                results=results,
                fallback_text=formatted_results,
                payload=payload,
            )
        except Exception as exc:
            await persistence.update_whatsapp_delivery(
                search_id=snapshot.search_id,
                status="send_failed",
                extra={"last_error": str(exc), "last_message_id": message_id},
            )
            raise
        if sent:
            await persistence.update_whatsapp_delivery(
                search_id=snapshot.search_id,
                status="final_sent",
                extra={"last_message_id": message_id},
            )
            await _maybe_send_marketing_handoff_for_search(
                search_id=snapshot.search_id, to=to, session_id=session_id
            )
        return

    if results:
        first_page = results[: search_progress.RESULT_PAGE_SIZE]
        next_offset = min(search_progress.RESULT_PAGE_SIZE, len(results))
        try:
            sent = await _send_result_cards(
                to=to,
                wa_id=wa_id,
                session_id=session_id,
                search_id=snapshot.search_id,
                results=first_page,
                sent_result_ids=sent_result_ids,
            )
        except Exception as exc:
            await persistence.update_whatsapp_delivery(
                search_id=snapshot.search_id,
                status="send_failed",
                extra={"last_error": str(exc), "last_message_id": message_id},
            )
            raise
        await persistence.update_whatsapp_delivery(
            search_id=snapshot.search_id,
            status="final_sent",
            extra={"last_message_id": message_id, "sent_new_results": sent, "next_result_offset": next_offset},
        )
        if len(results) > next_offset:
            await _send_more_results_prompt(
                to=to,
                wa_id=wa_id,
                session_id=session_id,
                search_id=snapshot.search_id,
                next_offset=next_offset,
                total_results=len(results),
            )
        await _maybe_send_marketing_handoff_for_search(
            search_id=snapshot.search_id, to=to, session_id=session_id
        )
        return

    try:
        sent = await _send_once_text_message(
            message_id=message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=snapshot.search_id,
            message_type="results",
            text=formatted_results,
            payload=payload,
        )
    except Exception as exc:
        await persistence.update_whatsapp_delivery(
            search_id=snapshot.search_id,
            status="send_failed",
            extra={"last_error": str(exc), "last_message_id": message_id},
        )
        raise
    if sent:
        await persistence.update_whatsapp_delivery(
            search_id=snapshot.search_id,
            status="final_sent",
            extra={"last_message_id": message_id},
        )
        await _maybe_send_marketing_handoff_for_search(
            search_id=snapshot.search_id, to=to, session_id=session_id
        )


def _log_background_task_failure(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("WhatsApp background task was cancelled")
    except Exception:
        logger.exception("WhatsApp background task failed")


def format_results_message(results: list[UnifiedResult]) -> str:
    if not results:
        return "I could not find matching prices this time. Try a more specific product name or another city/pincode."

    lines = []
    for result in results:
        lines.append(_quote_card_text(result))
    lines.append("")
    if any("Popularity score" in (result.notes or "") or "Live script" in (result.notes or "") for result in results):
        lines.append("Reply SHOW MORE VENDORS to see more local dealers, or send a vendor name to check that vendor's DB price.")
    else:
        lines.append("Send another product and city/pincode to search again.")
    return "\n".join(lines)


async def send_text_message(
    to: str,
    body: str,
    *,
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": True, "body": body[:4096]},
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=body)


async def send_image_message(
    to: str,
    image_url: str,
    caption: str,
    *,
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": {"link": image_url, "caption": caption[:1024]},
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=caption)


async def send_location_request_message(
    to: str,
    body: str,
    *,
    session_id: str | None = None,
) -> None:
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "location_request_message",
            "body": {"text": body[:1024]},
            "action": {"name": "send_location"},
        },
    }
    await _send_message(to, payload, session_id=session_id, text=body)


async def send_button_message(
    to: str,
    body: str,
    *,
    buttons: list[str] | list[tuple[str, str]],
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    reply_buttons: list[dict[str, Any]] = []
    for item in buttons[:3]:
        if isinstance(item, tuple):
            reply_id, title = item
        else:
            reply_id = _button_id(item)
            title = item
        reply_buttons.append(
            {
                "type": "reply",
                "reply": {
                    "id": str(reply_id)[:256],
                    "title": str(title)[:20],
                },
            }
        )
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": reply_buttons},
        },
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=body)


async def send_list_message(
    to: str,
    body: str,
    *,
    rows: list[dict[str, str]],
    list_button: str = "Choose option",
    section_title: str = "Options",
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    list_rows: list[dict[str, str]] = []
    for row in rows[:10]:
        reply_id = str(row.get("id") or "").strip()
        title = str(row.get("title") or "").strip()
        if not reply_id or not title:
            continue
        entry: dict[str, str] = {
            "id": reply_id[:256],
            "title": title[:24],
        }
        description = str(row.get("description") or "").strip()
        if description:
            entry["description"] = description[:72]
        list_rows.append(entry)
    if not list_rows:
        await send_text_message(to, body, session_id=session_id, search_id=search_id)
        return

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {
                "button": list_button[:20],
                "sections": [
                    {
                        "title": section_title[:24],
                        "rows": list_rows,
                    }
                ],
            },
        },
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=body)


async def send_cta_url_message(
    to: str,
    body: str,
    *,
    button_label: str,
    button_url: str,
    image_url: str | None = None,
    footer: str | None = None,
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    interactive: dict[str, Any] = {
        "type": "cta_url",
        "body": {"text": body[:1024]},
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": button_label[:20],
                "url": button_url,
            },
        },
    }
    if image_url and image_url.lower().startswith(("http://", "https://")):
        interactive["header"] = {"type": "image", "image": {"link": image_url}}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive,
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=body)


async def send_flow_message(
    to: str,
    *,
    body: str,
    flow_id: str,
    flow_cta: str,
    flow_token: str,
    flow_action: str = "data_exchange",
    flow_action_payload: dict[str, Any] | None = None,
    header_text: str | None = None,
    footer_text: str | None = None,
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    parameters: dict[str, Any] = {
        "flow_message_version": settings.whatsapp_flow_message_version,
        "flow_id": flow_id,
        "flow_cta": flow_cta[:30],
        "flow_token": flow_token[:255],
        "flow_action": flow_action,
    }
    if flow_action_payload is not None:
        parameters["flow_action_payload"] = flow_action_payload

    interactive_payload: dict[str, Any] = {
        "type": "flow",
        "body": {"text": body[:1024]},
        "action": {
            "name": "flow",
            "parameters": parameters,
        },
    }
    if header_text:
        interactive_payload["header"] = {"type": "text", "text": header_text[:60]}
    if footer_text:
        interactive_payload["footer"] = {"text": footer_text[:60]}
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": interactive_payload,
    }
    await _send_message(to, payload, session_id=session_id, search_id=search_id, text=body)


async def send_gold_live_rates_flow_message(
    to: str,
    body: str,
    *,
    flow_token: str,
    screen_data: dict[str, Any],
    session_id: str | None = None,
    search_id: str | None = None,
) -> None:
    if not whatsapp_flows.is_enabled_for_gold_live_rates():
        raise RuntimeError("WhatsApp gold live-rates Flow is not configured.")

    await send_flow_message(
        to,
        body=body,
        flow_id=settings.whatsapp_gold_live_rates_flow_id,
        flow_cta=settings.whatsapp_gold_live_rates_flow_cta or "View live rates",
        flow_token=flow_token,
        flow_action="navigate",
        flow_action_payload={
            "screen": "GOLD_LIVE_RATES",
            "data": {
                "flow_key": "gold_live_rates",
                **({"search_id": search_id} if search_id else {}),
                **screen_data,
            },
        },
        header_text="Gold live rates",
        footer_text="Price Hunter",
        session_id=session_id,
        search_id=search_id,
    )


def _button_id(label: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in label).strip("_")
    return normalized[:256] or "reply"


def _extract_interactive_reply_id(payload: dict[str, Any]) -> str | None:
    interactive = payload.get("interactive") or {}
    reply_type = interactive.get("type")
    if reply_type == "button_reply":
        reply = interactive.get("button_reply") or {}
    elif reply_type == "list_reply":
        reply = interactive.get("list_reply") or {}
    else:
        return None
    reply_id = str(reply.get("id") or "").strip()
    return reply_id or None


def _build_whatsapp_suggestion_entries(
    suggestions: list[str],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    entries: list[dict[str, str]] = []
    mapping: dict[str, str] = {}
    for idx, label in enumerate(suggestions[:10]):
        reply_id = f"sugg_{idx}_{_button_id(label)}"[:256]
        mapping[reply_id] = label
        list_title = label[:24]
        description = ""
        if len(label) > 24:
            description = label[:72]
        entries.append(
            {
                "id": reply_id,
                "title": label[:20],
                "list_title": list_title,
                "description": description,
                "full": label,
            }
        )
    return entries, mapping


def _should_send_gold_live_rates_flow(query: dict[str, Any] | None, results: list[UnifiedResult]) -> bool:
    if not results or not whatsapp_flows.is_enabled_for_gold_live_rates():
        return False
    query = query or {}
    route_handler = str(query.get("route_handler") or "").strip().lower()
    subcategory_id = str(query.get("subcategory_id") or "").strip().lower()
    category = str(query.get("category") or "").strip().lower()
    has_live_rate_rows = any((result.result_type or "").strip().lower() == "live_rate" for result in results)
    if has_live_rate_rows:
        return True
    return category == "gold" and (
        route_handler == "gold.bullion"
        or subcategory_id in {"gold_bullion", "gold_bullion_24k"}
    )


def _gold_flow_message_body(query: dict[str, Any] | None, results: list[UnifiedResult]) -> str:
    product = str((query or {}).get("product") or "gold product")
    location = str((query or {}).get("location") or "your location")
    best_price = min((result.price for result in results if result.price is not None), default=None)
    price_line = f" Best seen price: INR {best_price:,.0f}." if best_price is not None else ""
    return f"Gold live-rate search is ready for {product} in {location}.{price_line}"


async def _gold_national_fallback_text(query: dict[str, Any] | None) -> str | None:
    """Today's NATIONAL bullion consensus rate, clearly labelled, for a bullion
    query whose city has no local live rates — mirrors the gold.zwig.in desk
    (PR #143). Gold trades near-uniformly across India, so it's a valid
    reference while local supplier quotes are sourced. Returns None if the
    consensus is unavailable (caller then falls back to the generic message)."""
    try:
        from app.categories.gold.ingestion import GOLD_BULLION_CATEGORY_ID
        from app.services.gold_rate_rules import national_bullion_consensus

        rate = await national_bullion_consensus(GOLD_BULLION_CATEGORY_ID)
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("WhatsApp gold national-rate fallback lookup failed: %s", exc)
        return None
    if not rate:
        return None
    city = str((query or {}).get("location") or "").strip()
    if not city or city.lower() == "unknown":
        city = "your area"
    # "bullion rate" (not "999"): the consensus is a median over bullion-grade
    # rates and can blend 999/995, so a purity-specific label would be wrong.
    return (
        f"No local dealers in {city} yet, so here’s today’s national bullion rate: "
        f"about ₹{rate:,.0f} per 10g (gold trades near-uniformly across India). "
        "I’m sourcing local supplier quotes and will share them here as they come in."
    )


async def _try_send_gold_live_rates_flow_with_fallback(
    *,
    message_id: str,
    fallback_message_id: str,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    results: list[UnifiedResult],
    fallback_text: str,
    payload: dict[str, Any],
) -> bool:
    query: dict[str, Any] | None = None
    search_doc = await persistence.get_search_session(search_id)
    if search_doc:
        query = search_doc.get("query")

    # Build the board data in the shape the DEPLOYED Flow template binds
    # (row_1..8 / best_rate_value / title). The legacy builder emitted
    # result_1..3 / best_price keys that the template ignores, so the board
    # rendered blank even when the preview text had a price.
    q = query or {}
    screen_data = build_gold_live_rates_screen_data(
        product=str(q.get("product") or "Gold"),
        city=str(q.get("location") or "your location"),
        live_results=results,
        search_id=search_id,
    )

    try:
        return await _send_once_gold_live_rates_flow_message(
            message_id=message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=search_id,
            text=_gold_flow_message_body(query, results),
            screen_data=screen_data,
            payload=payload,
        )
    except Exception as exc:
        logger.warning("WhatsApp gold live-rates Flow send failed; falling back to text: %s", exc)
        return await _send_once_text_message(
            message_id=fallback_message_id,
            to=to,
            wa_id=wa_id,
            session_id=session_id,
            search_id=search_id,
            message_type="results",
            text=fallback_text,
            payload={**payload, "flow_fallback_error": str(exc)},
        )


async def _send_once_gold_live_rates_flow_message(
    *,
    message_id: str,
    to: str,
    wa_id: str,
    session_id: str,
    search_id: str,
    text: str,
    screen_data: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    inserted = await persistence.try_record_whatsapp_message(
        message_id=message_id,
        wa_id=wa_id,
        phone_number=to,
        session_id=session_id,
        direction="outbound",
        message_type="flow_results",
        text=text,
        payload={**payload, "flow_id": settings.whatsapp_gold_live_rates_flow_id},
        search_id=search_id,
        status="sending",
    )
    if not inserted:
        retry_claimed = await persistence.claim_failed_whatsapp_message_retry(message_id)
        if not retry_claimed:
            logger.info("Skipping duplicate WhatsApp Flow outbound message_id=%s search=%s", message_id, search_id)
            return False

    try:
        await send_gold_live_rates_flow_message(
            to,
            text,
            flow_token=search_id,
            screen_data=screen_data,
            session_id=session_id,
            search_id=search_id,
        )
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="sent",
            payload={**payload, "flow_id": settings.whatsapp_gold_live_rates_flow_id},
        )
        return True
    except Exception:
        await persistence.update_whatsapp_message_status(
            message_id=message_id,
            status="failed",
            payload={**payload, "flow_id": settings.whatsapp_gold_live_rates_flow_id},
        )
        raise


async def _send_message(
    to: str,
    payload: dict[str, Any],
    *,
    session_id: str | None,
    text: str,
    search_id: str | None = None,
) -> None:
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        logger.warning("WhatsApp credentials are missing; skipped outbound message to %s: %s", to, text)
        return

    url = (
        f"{GRAPH_API_BASE_URL}/{settings.whatsapp_api_version}/"
        f"{settings.whatsapp_phone_number_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    client = _get_http_client()
    response = await client.post(url, json=payload, headers=headers)
    response.raise_for_status()
    response_payload = response.json()

    outbound_id = _outbound_message_id(response_payload)
    await persistence.record_whatsapp_message(
        message_id=outbound_id,
        wa_id=to,
        phone_number=to,
        session_id=session_id,
        direction="outbound",
        message_type=payload["type"],
        text=text,
        payload=response_payload,
        search_id=search_id,
        status="sent",
    )


def _outbound_message_id(response_payload: dict[str, Any]) -> str:
    messages = response_payload.get("messages") or []
    if messages and messages[0].get("id"):
        return messages[0]["id"]
    return f"whatsapp-outbound-{uuid4()}"
