from __future__ import annotations

import asyncio
import base64
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.models.schemas import VoiceCallResult
from app.observability import capture_exception

logger = logging.getLogger(__name__)

_gcs_client_instance: Any | None = None
_gcs_client_lock = threading.Lock()


def _safe_path_part(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-._")
    return normalized[:120] or fallback


def _recording_extension(url: str, content_type: str | None) -> str:
    path = urlsplit(url).path.lower()
    for suffix in (".mp3", ".wav", ".m4a", ".ogg"):
        if path.endswith(suffix):
            return suffix.lstrip(".")
    normalized_type = (content_type or "").split(";", 1)[0].strip().lower()
    return {
        "audio/mpeg": "mp3",
        "audio/mp3": "mp3",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mp4": "m4a",
        "audio/ogg": "ogg",
    }.get(normalized_type, "mp3")


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _recording_headers(call: VoiceCallResult) -> dict[str, str]:
    provider = call.provider.strip().lower()
    if provider in {"plivo", "plivo_bridge", "pipecat"} and settings.plivo_auth_id and settings.plivo_auth_token:
        return {"Authorization": _basic_auth_header(settings.plivo_auth_id, settings.plivo_auth_token)}
    if provider == "exotel" and settings.exotel_api_key and settings.exotel_api_token:
        return {"Authorization": _basic_auth_header(settings.exotel_api_key, settings.exotel_api_token)}
    return {}


def _safe_error_message(exc: Exception) -> str:
    text = str(exc)
    try:
        parts = urlsplit(text)
        if parts.scheme and parts.netloc:
            text = f"{parts.scheme}://{parts.netloc}{parts.path}"
    except ValueError:
        pass
    return re.sub(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)", "[Filtered]", text)[:500]


def _object_name(*, search_id: str, call: VoiceCallResult, ext: str) -> str:
    now = datetime.now(timezone.utc)
    prefix = settings.voice_recordings_prefix.strip().strip("/") or "call-recordings"
    provider = _safe_path_part(call.provider, "provider")
    call_id = _safe_path_part(call.call_id, "call")
    search = _safe_path_part(search_id, "search")
    filename = f"{call_id}.{ext}"
    return str(
        PurePosixPath(prefix)
        / provider
        / f"{now:%Y}"
        / f"{now:%m}"
        / f"{now:%d}"
        / search
        / filename
    )


def _gcs_client() -> Any:
    global _gcs_client_instance
    try:
        from google.cloud import storage
    except Exception as exc:  # pragma: no cover - depends on optional package
        raise RuntimeError("google-cloud-storage is not installed") from exc
    if _gcs_client_instance is None:
        with _gcs_client_lock:
            if _gcs_client_instance is None:
                _gcs_client_instance = storage.Client()
    return _gcs_client_instance


async def archive_call_recording(
    *,
    search_id: str,
    call: VoiceCallResult,
) -> dict[str, Any]:
    provider_recording_url = call.recording_url
    base: dict[str, Any] = {
        "provider_recording_url": provider_recording_url,
        "recording_archive_status": "skipped",
        "recording_storage_provider": None,
        "recording_gcs_uri": None,
        "recording_object_name": None,
        "recording_archived_at": None,
        "recording_content_type": None,
        "recording_size_bytes": None,
    }

    if not provider_recording_url:
        return {**base, "recording_archive_status": "no_recording_url"}
    if not settings.voice_recording_archive_enabled:
        return {**base, "recording_archive_status": "disabled"}
    if not settings.voice_recordings_bucket:
        return {**base, "recording_archive_status": "bucket_not_configured"}

    try:
        timeout = max(1.0, settings.voice_recording_archive_timeout_seconds)
        max_bytes = max(1, settings.voice_recording_archive_max_bytes)
        chunks: list[bytes] = []
        downloaded_bytes = 0
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", provider_recording_url, headers=_recording_headers(call)) as response:
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length and content_length.isdigit() and int(content_length) > max_bytes:
                    raise RuntimeError(f"recording exceeds max archive size: {content_length} bytes")

                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > max_bytes:
                        raise RuntimeError(f"recording exceeds max archive size: {downloaded_bytes} bytes")
                    chunks.append(chunk)
                content_type = response.headers.get("content-type") or "audio/mpeg"

        content = b"".join(chunks)
        ext = _recording_extension(provider_recording_url, content_type)
        object_name = _object_name(search_id=search_id, call=call, ext=ext)

        def _upload_to_gcs() -> None:
            bucket = _gcs_client().bucket(settings.voice_recordings_bucket)
            blob = bucket.blob(object_name)
            blob.metadata = {
                "search_id": search_id,
                "call_id": call.call_id,
                "provider": call.provider,
            }
            blob.upload_from_string(content, content_type=content_type)

        await asyncio.to_thread(_upload_to_gcs)
        gcs_uri = f"gs://{settings.voice_recordings_bucket}/{object_name}"
        return {
            **base,
            "recording_archive_status": "archived",
            "recording_storage_provider": "gcs",
            "recording_gcs_uri": gcs_uri,
            "recording_object_name": object_name,
            "recording_archived_at": datetime.now(timezone.utc),
            "recording_content_type": content_type,
            "recording_size_bytes": len(content),
        }
    except httpx.HTTPStatusError as exc:
        # Provider recording URLs (Plivo/Exotel) frequently 403/404 for a short
        # window after the call ends while the media is still being processed.
        # This is expected and transient: the recording stays reachable via the
        # provider URL once ready, so treat it as "pending" rather than an
        # error and skip Sentry to avoid noise. Any other status is a real
        # failure and falls through to the generic handler below.
        if exc.response is not None and exc.response.status_code in {403, 404}:
            logger.info(
                "Recording not ready yet for call=%s provider=%s status=%s; "
                "leaving provider URL for later playback.",
                call.call_id,
                call.provider,
                exc.response.status_code,
            )
            return {
                **base,
                "recording_archive_status": "provider_url_pending",
            }
        raise
    except Exception as exc:
        logger.warning("Recording archive failed for call=%s provider=%s: %s", call.call_id, call.provider, exc)
        capture_exception(
            exc,
            provider=call.provider,
            phase="recording_archive",
            call_failure_type="recording_archive_failed",
            call_id=call.call_id,
            search_id=search_id,
        )
        return {
            **base,
            "recording_archive_status": "failed",
            "recording_archive_error": _safe_error_message(exc),
        }
