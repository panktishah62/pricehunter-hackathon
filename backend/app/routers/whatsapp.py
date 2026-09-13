from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from amplitude import BaseEvent

from app.amplitude import get_amplitude_client
from app.config import settings
from app.services import whatsapp, whatsapp_flows

logger = logging.getLogger(__name__)

router = APIRouter()
_background_tasks: set[asyncio.Task[None]] = set()


@router.get("/api/whatsapp/webhook")
async def verify_whatsapp_webhook(request: Request) -> PlainTextResponse:
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token and token == settings.whatsapp_verify_token and challenge:
        return PlainTextResponse(challenge)

    raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook verification token.")


@router.post("/api/whatsapp/webhook")
async def receive_whatsapp_webhook(request: Request) -> dict[str, str]:
    payload = await request.json()

    # Amplitude: track incoming WhatsApp messages (message count from payload)
    client = get_amplitude_client()
    if client:
        try:
            entries = payload.get("entry", [])
            message_count = sum(
                len(change.get("value", {}).get("messages", []))
                for entry in entries
                for change in entry.get("changes", [])
            )
            if message_count > 0:
                client.track(BaseEvent(
                    event_type="WhatsApp Message Received",
                    device_id="whatsapp",
                    event_properties={
                        "message_count": message_count,
                        "entry_count": len(entries),
                    },
                ))
        except Exception:
            logger.debug("Could not extract WhatsApp message count for Amplitude")

    task = asyncio.create_task(whatsapp.handle_webhook_payload(payload))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_background_failure)
    return {"status": "accepted"}


@router.post("/api/whatsapp/flows/endpoint")
async def receive_whatsapp_flow_endpoint(request: Request) -> PlainTextResponse:
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc

    try:
        encrypted_response = await whatsapp_flows.handle_encrypted_endpoint_request(
            body=payload,
            raw_body=raw_body,
            signature_header=request.headers.get("x-hub-signature-256"),
        )
    except whatsapp_flows.FlowEndpointException as exc:
        logger.warning("WhatsApp Flow endpoint rejected request: %s", exc)
        return PlainTextResponse(status_code=exc.status_code, content="")
    except Exception:
        logger.exception("WhatsApp Flow endpoint failed")
        return PlainTextResponse(status_code=500, content="")

    return PlainTextResponse(encrypted_response, media_type="text/plain")


@router.get("/api/whatsapp/call-vendor")
async def redirect_call_vendor(phone: str = Query(..., min_length=8, max_length=20)) -> RedirectResponse:
    normalized = whatsapp.normalize_vendor_phone_e164(phone)
    if not normalized:
        raise HTTPException(status_code=400, detail="Invalid phone number.")
    return RedirectResponse(url=f"tel:+{normalized}", status_code=302)


def _log_background_failure(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("WhatsApp webhook background task was cancelled")
    except Exception:
        logger.exception("WhatsApp webhook background task failed")
