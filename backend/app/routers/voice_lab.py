from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.services import gold_requests, persistence, request_context, voice_lab

router = APIRouter()

VOICE_LAB_SESSION_COOKIE = "pricehunter_voice_lab_session"
VOICE_LAB_SESSION_TTL_SECONDS = 12 * 60 * 60


class VoiceLabSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    location: str | None = None
    max_vendors: int = Field(default=8, ge=1, le=25)
    include_online: bool = True
    include_vendors: bool = True


class VoiceLabCallRequest(BaseModel):
    vendor_id: str | None = None
    provider_id: str = Field(default="plivo_bridge", min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=10, le=600)
    direct_phone: str | None = None
    direct_name: str | None = None
    direct_address: str | None = None
    campaign_config: dict[str, Any] | None = None


class VoiceLabLoginRequest(BaseModel):
    password: str = Field(min_length=1)


class VoiceLabBulkCallRequest(BaseModel):
    vendor_ids: list[str] | None = None
    provider_id: str = Field(default="pipecat", min_length=1)
    timeout_seconds: int | None = Field(default=None, ge=10, le=600)
    campaign_config: dict[str, Any] | None = None
    concurrency: int | None = Field(default=None, ge=1, le=50)


class VoiceLabCampaignPresetRequest(BaseModel):
    preset_id: str | None = None
    name: str = Field(min_length=1)
    description: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class VoiceLabBackfillRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    min_age_minutes: int = Field(default=15, ge=0, le=1440)
    dry_run: bool = False


class GoldRequestResponseCreate(BaseModel):
    supplier: str = Field(min_length=1)
    price: str | None = None
    note: str | None = None
    url: str | None = None
    author: str | None = None


class GoldRequestStatusUpdate(BaseModel):
    status: str = Field(min_length=1)


def _voice_lab_token(request: Request, token: str | None = None) -> str:
    return (
        token
        or request.headers.get("X-Voice-Lab-Token")
        or request.headers.get("X-Admin-Token")
        or ""
    )


def _dashboard_request(request: Request) -> bool:
    if request.headers.get("x-voice-lab-dashboard") == "1":
        return True
    values = [
        request.headers.get("host"),
        request.headers.get("x-forwarded-host"),
        request.headers.get("origin"),
        request.headers.get("referer"),
    ]
    return any("dashboard.zwig.in" in (value or "") for value in values)


def _session_signing_secret() -> str:
    return settings.voice_lab_admin_token.strip() or settings.voice_lab_login_password.strip()


def _signed_session_value(now: int | None = None) -> str:
    issued_at = int(now or time.time())
    secret = _session_signing_secret()
    signature = hmac.new(
        secret.encode("utf-8"),
        str(issued_at).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{issued_at}.{signature}"


def _valid_dashboard_session(value: str | None) -> bool:
    if not value or not _session_signing_secret():
        return False
    try:
        issued_at_text, signature = value.split(".", 1)
        issued_at = int(issued_at_text)
    except ValueError:
        return False
    if issued_at < int(time.time()) - VOICE_LAB_SESSION_TTL_SECONDS:
        return False
    expected = _signed_session_value(issued_at).split(".", 1)[1]
    return hmac.compare_digest(signature, expected)


def _require_voice_lab_access(
    request: Request,
    token: str | None = None,
    *,
    require_dashboard_login: bool = True,
) -> None:
    expected = settings.voice_lab_admin_token.strip()
    if not expected:
        if (
            require_dashboard_login
            and settings.voice_lab_login_password.strip()
            and not _valid_dashboard_session(request.cookies.get(VOICE_LAB_SESSION_COOKIE))
        ):
            raise HTTPException(status_code=401, detail="Voice Lab login required.")
        return
    if not hmac.compare_digest(_voice_lab_token(request, token).strip(), expected):
        raise HTTPException(status_code=401, detail="Voice Lab token is required.")
    if (
        require_dashboard_login
        and settings.voice_lab_login_password.strip()
        and _dashboard_request(request)
        and not _valid_dashboard_session(request.cookies.get(VOICE_LAB_SESSION_COOKIE))
    ):
        raise HTTPException(status_code=401, detail="Voice Lab login required.")


@router.post("/api/voice-lab/login")
async def voice_lab_login(request: Request, payload: VoiceLabLoginRequest, response: Response) -> dict[str, Any]:
    _require_voice_lab_access(request, require_dashboard_login=False)
    expected = settings.voice_lab_login_password.strip()
    if not expected:
        return {"status": "disabled", "requires_login": False}
    if not hmac.compare_digest(payload.password.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid Voice Lab password.")
    response.set_cookie(
        VOICE_LAB_SESSION_COOKIE,
        _signed_session_value(),
        max_age=VOICE_LAB_SESSION_TTL_SECONDS,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"status": "ok", "requires_login": True}


@router.post("/api/voice-lab/logout")
async def voice_lab_logout(response: Response) -> dict[str, str]:
    response.delete_cookie(VOICE_LAB_SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/api/voice-lab/config")
async def voice_lab_config(request: Request, token: str | None = Query(default=None)) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    return {
        "provider_options": voice_lab.provider_options(),
        "archive_enabled": settings.voice_recording_archive_enabled,
        "recordings_bucket_configured": bool(settings.voice_recordings_bucket),
        "requires_token": bool(settings.voice_lab_admin_token.strip()),
        "requires_login": bool(settings.voice_lab_login_password.strip()),
        "bulk_max_concurrency": max(1, int(settings.voice_lab_bulk_max_concurrency)),
        "bulk_default_concurrency": max(1, int(settings.voice_lab_bulk_max_concurrency)),
    }


@router.post("/api/voice-lab/sessions")
async def create_voice_lab_session(request: Request, payload: VoiceLabSearchRequest) -> dict[str, Any]:
    _require_voice_lab_access(request)
    metadata = request_context.extract_request_metadata(request)
    metadata["source"] = "voice_lab"
    session = await voice_lab.create_session(
        raw_query=payload.query,
        location=payload.location,
        max_vendors=payload.max_vendors,
        include_online=payload.include_online,
        include_vendors=payload.include_vendors,
        request_metadata=metadata,
    )
    return await voice_lab.session_payload(session)


@router.get("/api/voice-lab/sessions/{search_id}")
async def get_voice_lab_session(
    search_id: str,
    request: Request,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    session = await voice_lab.ensure_session(search_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Voice Lab session not found.")
    return await voice_lab.session_payload(session)


@router.post("/api/voice-lab/sessions/{search_id}/calls")
async def call_voice_lab_vendor(
    search_id: str,
    request: Request,
    payload: VoiceLabCallRequest,
) -> dict[str, Any]:
    _require_voice_lab_access(request)
    try:
        result = await voice_lab.call_selected_vendor(
            search_id=search_id,
            vendor_id=payload.vendor_id,
            provider_id=payload.provider_id,
            timeout_seconds=payload.timeout_seconds,
            direct_phone=payload.direct_phone,
            direct_name=payload.direct_name,
            direct_address=payload.direct_address,
            campaign_config=payload.campaign_config,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result


@router.get("/api/voice-lab/calls")
async def list_voice_lab_calls(
    request: Request,
    token: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    calls, has_more = await voice_lab.list_recent_calls(limit=limit, offset=offset)
    return {"calls": calls, "limit": limit, "offset": offset, "has_more": has_more}


@router.get("/api/voice-lab/calls/{call_id}")
async def get_voice_lab_call(
    call_id: str,
    request: Request,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    call = await voice_lab.get_call_attempt(call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call attempt not found.")
    return call


@router.post("/api/voice-lab/sessions/{search_id}/calls/bulk")
async def call_voice_lab_vendors_bulk(
    search_id: str,
    request: Request,
    payload: VoiceLabBulkCallRequest,
) -> dict[str, Any]:
    _require_voice_lab_access(request)
    try:
        return await voice_lab.call_vendors_bulk(
            search_id=search_id,
            vendor_ids=payload.vendor_ids,
            provider_id=payload.provider_id,
            timeout_seconds=payload.timeout_seconds,
            campaign_config=payload.campaign_config,
            concurrency=payload.concurrency,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/voice-lab/campaign-presets")
async def list_voice_lab_campaign_presets(
    request: Request,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    return {"presets": await voice_lab.list_campaign_presets()}


@router.post("/api/voice-lab/campaign-presets")
async def upsert_voice_lab_campaign_preset(
    request: Request,
    payload: VoiceLabCampaignPresetRequest,
) -> dict[str, Any]:
    _require_voice_lab_access(request)
    try:
        preset = await voice_lab.upsert_campaign_preset(
            preset_id=payload.preset_id,
            name=payload.name,
            description=payload.description,
            config=payload.config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"preset": preset}


@router.delete("/api/voice-lab/campaign-presets/{preset_id}")
async def delete_voice_lab_campaign_preset(
    preset_id: str,
    request: Request,
) -> dict[str, str]:
    _require_voice_lab_access(request)
    deleted = await voice_lab.delete_campaign_preset(preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Preset not found.")
    return {"status": "deleted"}


@router.get("/api/voice-lab/calls/{call_id}/recording")
async def get_voice_lab_recording(
    call_id: str,
    request: Request,
    token: str | None = Query(default=None),
) -> Response:
    _require_voice_lab_access(request, token)
    recording = await voice_lab.recording_bytes(call_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found.")
    content, content_type = recording
    return _ranged_audio_response(content, content_type, request.headers.get("range"))


def _ranged_audio_response(
    content: bytes,
    content_type: str,
    range_header: str | None,
) -> Response:
    """Serve audio with HTTP range support.

    Phone-call MP3s from Plivo/Exotel often lack a duration (Xing/Info) header,
    so browsers issue a Range request to probe the file before they can show a
    duration or allow seeking. A plain 200 response makes the <audio> element
    report 0:00 and disables the timeline. Advertising `Accept-Ranges` and
    answering Range requests with 206 + `Content-Range` fixes both.
    """
    total = len(content)
    base_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=3600",
    }

    start, end = _parse_byte_range(range_header, total)
    if start is None:
        return Response(content=content, media_type=content_type, headers=base_headers)

    if start >= total or start > end:
        # Unsatisfiable range.
        return Response(
            status_code=416,
            headers={**base_headers, "Content-Range": f"bytes */{total}"},
        )

    chunk = content[start : end + 1]
    headers = {
        **base_headers,
        "Content-Range": f"bytes {start}-{end}/{total}",
    }
    return Response(
        content=chunk,
        status_code=206,
        media_type=content_type,
        headers=headers,
    )


def _parse_byte_range(range_header: str | None, total: int) -> tuple[int | None, int | None]:
    """Parse a single `bytes=start-end` range. Returns (None, None) if absent
    or unparseable (caller then serves the full body)."""
    if not range_header or total <= 0:
        return None, None
    value = range_header.strip()
    if not value.lower().startswith("bytes="):
        return None, None
    spec = value[len("bytes=") :].split(",", 1)[0].strip()
    if "-" not in spec:
        return None, None
    start_text, end_text = spec.split("-", 1)
    start_text, end_text = start_text.strip(), end_text.strip()
    try:
        if start_text == "":
            # Suffix range: last N bytes.
            if end_text == "":
                return None, None
            length = int(end_text)
            if length <= 0:
                return None, None
            start = max(0, total - length)
            return start, total - 1
        start = int(start_text)
        if start < 0:
            # RFC 7233: first-byte-pos must be a non-negative integer.
            return None, None
        end = int(end_text) if end_text else total - 1
    except ValueError:
        return None, None
    end = min(end, total - 1)
    return start, end



@router.post("/api/voice-lab/recordings/backfill")
async def backfill_recordings(
    request: Request,
    payload: VoiceLabBackfillRequest | None = None,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    """Safety-net sweep that re-archives recordings whose provider URL is known
    but never landed in GCS (e.g. a lost recording-ready callback).

    Admin-gated. Designed to be invoked periodically (e.g. Cloud Scheduler) as
    well as manually. Idempotent: already-archived rows are excluded by the
    query, and GCS uploads are keyed by a deterministic object name.
    """
    _require_voice_lab_access(request, token)
    params = payload or VoiceLabBackfillRequest()
    result = await persistence.backfill_pending_recordings(
        limit=params.limit,
        min_age_minutes=params.min_age_minutes,
        dry_run=params.dry_run,
    )
    return result


# ── Gold fulfilment requests (gold.zwig.in human-in-the-loop) ────────────────


@router.get("/api/voice-lab/requests")
async def list_gold_requests(
    request: Request,
    status: str | None = Query(default=None),
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    requests = await gold_requests.list_requests(status=status, limit=200)
    return {"requests": requests}


@router.get("/api/voice-lab/requests/{request_id}")
async def get_gold_request(
    request: Request,
    request_id: str,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    doc = await gold_requests.get_request(request_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    return doc


@router.post("/api/voice-lab/requests/{request_id}/responses")
async def add_gold_request_response(
    request: Request,
    request_id: str,
    payload: GoldRequestResponseCreate,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    response = await gold_requests.add_response(
        request_id=request_id,
        supplier=payload.supplier,
        price=payload.price,
        note=payload.note,
        url=payload.url,
        author=payload.author,
    )
    if response is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    doc = await gold_requests.get_request(request_id)
    return {"response": response, "request": doc}


@router.post("/api/voice-lab/requests/{request_id}/status")
async def update_gold_request_status(
    request: Request,
    request_id: str,
    payload: GoldRequestStatusUpdate,
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    _require_voice_lab_access(request, token)
    if payload.status == gold_requests.STATUS_CLOSED:
        ok = await gold_requests.close_request(request_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Request not found.")
    elif payload.status not in (gold_requests.STATUS_AWAITING, gold_requests.STATUS_RESPONDED):
        raise HTTPException(status_code=422, detail=f"Unsupported status transition: {payload.status!r}")
    doc = await gold_requests.get_request(request_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    return doc
