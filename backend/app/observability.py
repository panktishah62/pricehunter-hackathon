from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import sentry_sdk

from app.config import settings

logger = logging.getLogger(__name__)

PHONE_PATTERN = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

SENSITIVE_KEY_PARTS = (
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "phone",
    "mobile",
    "wa_id",
    "whatsapp",
    "recording",
    "transcript",
)


def _redact_text(value: str) -> str:
    value = PHONE_PATTERN.sub("[Filtered]", value)
    return EMAIL_PATTERN.sub("[Filtered]", value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _scrub(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        scrubbed: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                scrubbed[key] = "[Filtered]"
            else:
                scrubbed[key] = _scrub(item, key_text)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item, parent_key) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item, parent_key) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _strip_url_query(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return _redact_text(value)
    if not parts.scheme or not parts.netloc:
        return _redact_text(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _before_send(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        if isinstance(request.get("url"), str):
            request["url"] = _strip_url_query(request["url"])
        request["headers"] = _scrub(request.get("headers", {}))
        request["cookies"] = "[Filtered]"
        request["query_string"] = "[Filtered]"
        if "data" in request:
            request["data"] = "[Filtered]"

    if "user" in event:
        event["user"] = _scrub(event["user"])
    if "extra" in event:
        event["extra"] = _scrub(event["extra"])
    if "contexts" in event:
        event["contexts"] = _scrub(event["contexts"])
    if "breadcrumbs" in event:
        event["breadcrumbs"] = _scrub(event["breadcrumbs"])
    if "exception" in event:
        for exc in event["exception"].get("values") or []:
            if isinstance(exc.get("value"), str):
                exc["value"] = _redact_text(exc["value"])
    return event


def init_sentry() -> None:
    if not settings.sentry_dsn:
        return

    release = settings.sentry_release or os.getenv("K_REVISION") or None
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        release=release,
        send_default_pii=False,
        enable_logs=settings.sentry_enable_logs,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=_before_send,
        max_breadcrumbs=50,
    )
    sentry_sdk.set_tag("service", "pricehunter-api")
    if os.getenv("K_SERVICE"):
        sentry_sdk.set_tag("cloud_run_service", os.getenv("K_SERVICE"))
    if os.getenv("K_REVISION"):
        sentry_sdk.set_tag("cloud_run_revision", os.getenv("K_REVISION"))
    logger.info("Sentry initialized for pricehunter-api")


def capture_message(message: str, *, level: str = "warning", **context: Any) -> None:
    if not settings.sentry_dsn:
        return
    with sentry_sdk.push_scope() as scope:
        sanitized = _scrub(context)
        scope.set_level(level)
        for key, value in sanitized.items():
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            scope.set_tag(str(key), str(value)[:200])
        scope.set_context("pricehunter", sanitized)
        sentry_sdk.capture_message(message)


def capture_exception(exc: BaseException, **context: Any) -> None:
    if not settings.sentry_dsn:
        return
    with sentry_sdk.push_scope() as scope:
        sanitized = _scrub(context)
        for key, value in sanitized.items():
            if value is None or isinstance(value, (dict, list, tuple, set)):
                continue
            scope.set_tag(str(key), str(value)[:200])
        scope.set_context("pricehunter", sanitized)
        sentry_sdk.capture_exception(exc)
