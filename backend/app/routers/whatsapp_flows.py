from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.whatsapp_flows.crypto import (
    FlowEndpointException,
    decrypt_request,
    encrypt_response,
    verify_request_signature,
)
from app.whatsapp_flows.service import build_flow_data_exchange_response

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/api/whatsapp/flows/data-exchange")
async def whatsapp_flows_data_exchange(request: Request) -> dict:
    payload = await request.json()
    return await build_flow_data_exchange_response(payload)


@router.post("/api/whatsapp/flows/endpoint")
async def whatsapp_flows_endpoint(request: Request) -> Response:
    raw_body = await request.body()
    signature_header = request.headers.get("x-hub-signature-256")

    if not verify_request_signature(
        raw_body=raw_body,
        signature_header=signature_header,
        app_secret=settings.whatsapp_app_secret,
    ):
        return Response(status_code=432)

    if not settings.whatsapp_flows_private_key:
        raise HTTPException(status_code=500, detail="WhatsApp Flows private key is not configured.")

    try:
        encrypted_payload = await request.json()
        decrypted = decrypt_request(
            encrypted_payload,
            private_key_pem=settings.whatsapp_flows_private_key,
            passphrase=settings.whatsapp_flows_private_key_passphrase,
        )
    except FlowEndpointException as exc:
        logger.warning("WhatsApp Flow endpoint decryption failed: %s", exc)
        return Response(status_code=exc.status_code)

    response_payload = await build_flow_data_exchange_response(decrypted.decrypted_body)
    encrypted_response = encrypt_response(
        response_payload,
        aes_key=decrypted.aes_key,
        initial_vector=decrypted.initial_vector,
    )
    return PlainTextResponse(encrypted_response, status_code=200)
