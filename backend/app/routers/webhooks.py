from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.services import persistence
from app.services.voice_agent import (
    persist_pipecat_webhook,
    store_elevenlabs_webhook,
    store_execution_webhook,
    store_pipecat_webhook,
    store_plivo_bridge_webhook,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _verify_elevenlabs_signature(raw_body: bytes, signature_header: str | None) -> None:
    if not settings.elevenlabs_webhook_secret:
        return
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing ElevenLabs webhook signature.")

    parts = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()

    timestamp = parts.get("t")
    signature = parts.get("v0")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Invalid ElevenLabs webhook signature.")

    try:
        timestamp_int = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid ElevenLabs webhook timestamp.") from exc

    if abs(time.time() - timestamp_int) > 30 * 60:
        raise HTTPException(status_code=401, detail="Stale ElevenLabs webhook signature.")

    signed_payload = timestamp.encode("utf-8") + b"." + raw_body
    expected = hmac.new(
        settings.elevenlabs_webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid ElevenLabs webhook signature.")


@router.post("/api/webhooks/voice")
async def voice_webhook(request: Request) -> dict[str, str]:
    """Receives call completion callbacks from Bolna."""

    body = await request.json()
    execution_id = store_execution_webhook(body)
    logger.info("Received Bolna webhook payload for execution=%s.", execution_id or "unknown")
    try:
        await persistence.record_raw_webhook(execution_id, body)
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Failed to persist webhook payload: %s", exc)
    return {"status": "received"}


@router.post("/api/webhooks/voice/elevenlabs")
async def elevenlabs_voice_webhook(request: Request) -> dict[str, str]:
    """Receives post-call callbacks from ElevenLabs."""

    raw_body = await request.body()
    _verify_elevenlabs_signature(raw_body, request.headers.get("elevenlabs-signature"))
    body = json.loads(raw_body)
    conversation_id = store_elevenlabs_webhook(body)
    logger.info("Received ElevenLabs webhook payload for conversation=%s.", conversation_id or "unknown")
    try:
        await persistence.record_raw_webhook(conversation_id, body)
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Failed to persist ElevenLabs webhook payload: %s", exc)
    return {"status": "received"}


@router.post("/api/webhooks/voice/plivo-bridge")
async def plivo_bridge_voice_webhook(request: Request) -> dict[str, str]:
    """Receives call completion callbacks from the PriceHunter Plivo bridge."""

    if settings.plivo_bridge_webhook_secret:
        provided = request.headers.get("x-pricehunter-bridge-secret", "")
        if not hmac.compare_digest(provided, settings.plivo_bridge_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid bridge webhook secret.")

    body = await request.json()
    call_id = store_plivo_bridge_webhook(body)
    logger.info("Received Plivo bridge webhook payload for call=%s.", call_id or "unknown")
    try:
        await persistence.record_raw_webhook(call_id, body)
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Failed to persist Plivo bridge webhook payload: %s", exc)
    return {"status": "received"}


@router.post("/api/webhooks/voice/pipecat")
async def pipecat_voice_webhook(request: Request) -> dict[str, str]:
    """Receives call completion callbacks from the hosted Pipecat agent."""

    if settings.pipecat_webhook_secret:
        provided = request.headers.get("x-pricehunter-pipecat-secret", "")
        if not hmac.compare_digest(provided, settings.pipecat_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid Pipecat webhook secret.")

    body = await request.json()
    call_id = store_pipecat_webhook(body)
    logger.info("Received Pipecat webhook payload for call=%s.", call_id or "unknown")
    if call_id:
        # Persist synchronously so other Cloud Run replicas can pick the
        # payload up via Mongo as soon as we've ack'd the webhook.
        try:
            await persist_pipecat_webhook(call_id=call_id, payload=body)
        except Exception as exc:  # pragma: no cover - depends on external service
            logger.warning("Failed to persist Pipecat webhook payload to Mongo: %s", exc)
    recording_url = (body.get("recording_url") or "").strip()
    if call_id and recording_url:
        try:
            await persistence.record_call_recording_ready(
                call_id=call_id,
                provider="pipecat",
                recording_url=recording_url,
                provider_metadata=body.get("provider_metadata") if isinstance(body.get("provider_metadata"), dict) else None,
            )
        except Exception as exc:  # pragma: no cover - depends on external service
            logger.warning("Failed to persist Pipecat recording URL: %s", exc)
    if call_id:
        try:
            await persistence.apply_pipecat_webhook_to_call_attempt(
                call_id=call_id,
                payload=body,
            )
        except Exception as exc:  # pragma: no cover - depends on external service
            logger.warning("Failed to apply Pipecat webhook to call attempt: %s", exc)
    try:
        await persistence.record_raw_webhook(call_id, body)
    except Exception as exc:  # pragma: no cover - depends on external service
        logger.warning("Failed to persist Pipecat webhook payload: %s", exc)
    return {"status": "received"}
