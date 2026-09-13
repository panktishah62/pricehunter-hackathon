"""Web Push (VAPID) notifications for the PWA/TWA.

Subscriptions are stored per browser endpoint and linked to a device_id and the
session(s) that opted in. Sending is best-effort: a 404/410 from the push
service means the subscription is gone, so we prune it.

No FCM project is needed — modern Web Push on Android Chrome rides FCM transport
automatically; we only need a VAPID keypair (settings.vapid_*).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.database import push_subscriptions_collection

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_enabled() -> bool:
    return bool(
        settings.web_push_enabled
        and settings.vapid_public_key
        and settings.vapid_private_key
    )


async def save_subscription(
    *,
    subscription: dict[str, Any],
    device_id: str | None,
    session_id: str | None,
) -> bool:
    """Upsert a push subscription (unique by endpoint). Links the session that
    opted in so we can target it later. Returns False if the payload is invalid."""
    endpoint = (subscription or {}).get("endpoint")
    keys = (subscription or {}).get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return False

    now = _now()
    set_fields: dict[str, Any] = {
        "endpoint": endpoint,
        "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
        "updated_at": now,
    }
    if device_id:
        set_fields["device_id"] = device_id

    update: dict[str, Any] = {"$set": set_fields, "$setOnInsert": {"created_at": now}}
    if session_id:
        update["$addToSet"] = {"session_ids": session_id}

    await push_subscriptions_collection.update_one({"endpoint": endpoint}, update, upsert=True)
    return True


async def remove_subscription(endpoint: str) -> None:
    if endpoint:
        await push_subscriptions_collection.delete_one({"endpoint": endpoint})


def _send_one(sub: dict[str, Any], payload: dict[str, Any]) -> str:
    """Blocking send of a single web-push (run in a thread). Returns a status
    string: 'ok', 'gone' (prune), or 'error'."""
    from pywebpush import WebPushException, webpush  # local import keeps cold-start light

    try:
        webpush(
            subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
            data=json.dumps(payload),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            timeout=10,
        )
        return "ok"
    except WebPushException as exc:  # pragma: no cover - network
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            return "gone"
        logger.warning("web push failed (%s): %s", status, exc)
        return "error"
    except Exception as exc:  # pragma: no cover - network
        logger.warning("web push error: %s", exc)
        return "error"


async def _send_to_query(query: dict[str, Any], payload: dict[str, Any]) -> int:
    if not is_enabled():
        return 0
    subs = await push_subscriptions_collection.find(query).to_list(length=500)
    if not subs:
        return 0
    # Send concurrently so a slow/timing-out endpoint can't block the caller
    # (e.g. ops posting a quote) for N x timeout seconds.
    results = await asyncio.gather(
        *[asyncio.to_thread(_send_one, sub, payload) for sub in subs]
    )
    sent = 0
    for sub, result in zip(subs, results):
        if result == "ok":
            sent += 1
        elif result == "gone":
            await remove_subscription(sub.get("endpoint", ""))
    return sent


def _payload(title: str, body: str, *, url: str = "/#/app", tag: str | None = None,
             data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "title": title,
        "body": body,
        "url": url,
        "tag": tag,
        "data": data or {},
    }


async def send_to_session(session_id: str, *, title: str, body: str,
                          url: str = "/#/app", tag: str | None = None,
                          data: dict[str, Any] | None = None) -> int:
    """Push to every subscription that opted in from this chat session."""
    if not session_id:
        return 0
    return await _send_to_query(
        {"session_ids": session_id},
        _payload(title, body, url=url, tag=tag, data=data),
    )


async def send_to_device(device_id: str, *, title: str, body: str,
                         url: str = "/#/app", tag: str | None = None,
                         data: dict[str, Any] | None = None) -> int:
    if not device_id:
        return 0
    return await _send_to_query(
        {"device_id": device_id},
        _payload(title, body, url=url, tag=tag, data=data),
    )
