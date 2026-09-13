from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.config import settings
from app.services import push_service, request_context

router = APIRouter()


class PushSubscribeRequest(BaseModel):
    subscription: dict[str, Any]
    session_id: str | None = None


class PushUnsubscribeRequest(BaseModel):
    endpoint: str


@router.get("/api/push/config")
async def push_config() -> dict[str, Any]:
    """Public config the client needs to subscribe. Safe to expose."""
    return {
        "enabled": push_service.is_enabled(),
        "vapid_public_key": settings.vapid_public_key if push_service.is_enabled() else "",
    }


@router.post("/api/push/subscribe")
async def push_subscribe(request: Request, payload: PushSubscribeRequest) -> dict[str, Any]:
    metadata = request_context.extract_request_metadata(request)
    saved = await push_service.save_subscription(
        subscription=payload.subscription,
        device_id=metadata.get("device_id"),
        session_id=payload.session_id,
    )
    return {"status": "ok" if saved else "invalid"}


@router.post("/api/push/unsubscribe")
async def push_unsubscribe(payload: PushUnsubscribeRequest) -> dict[str, Any]:
    await push_service.remove_subscription(payload.endpoint)
    return {"status": "ok"}
