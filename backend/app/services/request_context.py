from __future__ import annotations

from fastapi import Request


def extract_request_metadata(request: Request) -> dict[str, str | None]:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    forwarded_ip = forwarded_for.split(",")[0].strip() if forwarded_for else None
    ip = (
        request.headers.get("cf-connecting-ip")
        or request.headers.get("x-real-ip")
        or forwarded_ip
        or (request.client.host if request.client else None)
    )

    return {
        "device_id": request.headers.get("x-device-id"),
        "ip": ip,
        "user_agent": request.headers.get("user-agent"),
        "origin": request.headers.get("origin"),
        "referer": request.headers.get("referer"),
    }
