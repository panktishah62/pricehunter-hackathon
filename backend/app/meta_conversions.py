"""Meta Conversions API client for server-side event tracking."""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _hash_value(value: str | None) -> str | None:
    """SHA-256 hash a value for Meta's required hashing format."""
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


async def send_event(
    event_name: str,
    *,
    event_source_url: Optional[str] = None,
    user_agent: Optional[str] = None,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    client_ip: Optional[str] = None,
    fbc: Optional[str] = None,
    fbp: Optional[str] = None,
    external_id: Optional[str] = None,
    currency: str = "INR",
    value: Optional[float] = None,
    content_name: Optional[str] = None,
    content_category: Optional[str] = None,
    event_id: Optional[str] = None,
) -> bool:
    """Send a single event to Meta Conversions API.

    Returns True if successful, False otherwise. Never raises.
    """
    if not settings.meta_pixel_id or not settings.meta_conversions_api_token:
        logger.debug("Meta Conversions API not configured, skipping event: %s", event_name)
        return False

    user_data: dict = {}
    if email:
        user_data["em"] = [_hash_value(email)]
    if phone:
        user_data["ph"] = [_hash_value(phone)]
    if client_ip:
        user_data["client_ip_address"] = client_ip
    if user_agent:
        user_data["client_user_agent"] = user_agent
    if fbc:
        user_data["fbc"] = fbc
    if fbp:
        user_data["fbp"] = fbp
    if external_id:
        user_data["external_id"] = [_hash_value(external_id)]

    event: dict = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "action_source": "website",
        "user_data": user_data,
    }

    if event_source_url:
        event["event_source_url"] = event_source_url

    if event_id:
        event["event_id"] = event_id

    custom_data: dict = {}
    if currency and value is not None:
        custom_data["currency"] = currency
        custom_data["value"] = value
    if content_name:
        custom_data["content_name"] = content_name
    if content_category:
        custom_data["content_category"] = content_category

    if custom_data:
        event["custom_data"] = custom_data

    payload = {"data": [event]}

    url = (
        f"https://graph.facebook.com/{settings.meta_api_version}"
        f"/{settings.meta_pixel_id}/events"
    )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.meta_conversions_api_token}"},
                json=payload,
            )

        if response.status_code == 200:
            logger.info("Meta CAPI event sent: %s", event_name)
            return True
        else:
            logger.warning(
                "Meta CAPI event failed: %s, status=%d, body=%s",
                event_name,
                response.status_code,
                response.text[:300],
            )
            return False
    except Exception as exc:
        logger.warning("Meta CAPI request error for %s: %s", event_name, exc)
        return False
