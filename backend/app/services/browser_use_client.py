from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings


logger = logging.getLogger(__name__)

BROWSER_USE_BROWSER_URL = "https://api.browser-use.com/api/v2/browsers"


class BrowserUseError(RuntimeError):
    def __init__(self, message: str, *, code: str = "browser_use_error", session_id: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.session_id = session_id


class BrowserUseClient:
    def __init__(self) -> None:
        self.api_key = settings.browser_use_api_key

    async def create_session(self) -> dict[str, Any]:
        if not self.api_key:
            raise BrowserUseError("BROWSER_USE_API_KEY is not configured", code="missing_browser_use_api_key")

        payload = {
            "proxyCountryCode": settings.browser_use_proxy_country,
            "timeout": settings.browser_use_session_timeout_minutes,
            "browserScreenWidth": 1512,
            "browserScreenHeight": 982,
            "enableRecording": False,
        }
        headers = {
            "X-Browser-Use-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(BROWSER_USE_BROWSER_URL, headers=headers, json=payload)

        if response.status_code >= 400:
            raise BrowserUseError(
                f"Browser Use session creation failed: {_response_error_message(response)}",
                code="session_create_failed",
            )

        data = response.json()
        if data.get("status") != "active":
            raise BrowserUseError(
                f"Browser Use session is not active: {data}",
                code="session_not_active",
                session_id=data.get("id"),
            )
        if not data.get("cdpUrl"):
            raise BrowserUseError(
                "Browser Use session did not return a CDP URL",
                code="missing_cdp_url",
                session_id=data.get("id"),
            )
        return data

    async def stop_session(self, session_id: str) -> dict[str, Any] | None:
        if not self.api_key:
            return None

        headers = {
            "X-Browser-Use-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.patch(
                    f"{BROWSER_USE_BROWSER_URL}/{session_id}",
                    headers=headers,
                    json={"action": "stop"},
                )
            if response.status_code >= 400:
                logger.warning("Browser Use session stop failed for %s: %s", session_id, _response_error_message(response))
                return None
            return response.json()
        except Exception as exc:  # pragma: no cover - cleanup best effort
            logger.warning("Browser Use session stop errored for %s: %s", session_id, exc)
            return None


def classify_browser_error(exc: Exception) -> str:
    text = str(exc)
    if "ERR_TUNNEL_CONNECTION_FAILED" in text:
        return "provider_tunnel_failed"
    if "402" in text or "Insufficient balance" in text:
        return "provider_insufficient_balance"
    if "Timeout" in text or "timed out" in text.lower():
        return "route_timeout"
    return "browser_navigation_failed"


def _response_error_message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:500] or f"HTTP {response.status_code}"
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("error") or data.get("message")
        if detail:
            return str(detail)
    return str(data)[:500]
