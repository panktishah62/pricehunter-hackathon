from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings
from app.models.schemas import UnifiedResult
from app.services import persistence
from app.whatsapp_flows.service import build_flow_data_exchange_response

logger = logging.getLogger(__name__)

FLOW_ENDPOINT_SIGNATURE_ERROR = 432
FLOW_ENDPOINT_DECRYPTION_ERROR = 421


class FlowEndpointException(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DecryptedFlowRequest:
    body: dict[str, Any]
    aes_key: bytes
    initial_vector: bytes


def is_enabled_for_gold_live_rates() -> bool:
    return bool(
        settings.whatsapp_flows_enabled
        and settings.whatsapp_gold_live_rates_flow_id
        and settings.whatsapp_flows_private_key
    )


def verify_request_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.whatsapp_app_secret:
        logger.error("WHATSAPP_APP_SECRET is not set; rejecting Flow endpoint request")
        return False

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


def decrypt_request(body: dict[str, Any]) -> DecryptedFlowRequest:
    private_key = _load_private_key()
    try:
        encrypted_aes_key = base64.b64decode(body["encrypted_aes_key"])
        encrypted_flow_data = base64.b64decode(body["encrypted_flow_data"])
        initial_vector = base64.b64decode(body["initial_vector"])
    except (KeyError, ValueError) as exc:
        raise FlowEndpointException(400, "Malformed encrypted Flow request.") from exc

    try:
        aes_key = private_key.decrypt(
            encrypted_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    except Exception as exc:
        raise FlowEndpointException(
            FLOW_ENDPOINT_DECRYPTION_ERROR,
            "Failed to decrypt Flow request AES key.",
        ) from exc

    try:
        decrypted = AESGCM(aes_key).decrypt(initial_vector, encrypted_flow_data, None)
        decrypted_body = json.loads(decrypted.decode("utf-8"))
    except (InvalidTag, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FlowEndpointException(400, "Failed to decrypt Flow request body.") from exc

    return DecryptedFlowRequest(
        body=decrypted_body,
        aes_key=aes_key,
        initial_vector=initial_vector,
    )


def encrypt_response(response: dict[str, Any], *, aes_key: bytes, initial_vector: bytes) -> str:
    flipped_iv = bytes(byte ^ 0xFF for byte in initial_vector)
    plaintext = json.dumps(response, separators=(",", ":")).encode("utf-8")
    encrypted = AESGCM(aes_key).encrypt(flipped_iv, plaintext, None)
    return base64.b64encode(encrypted).decode("utf-8")


async def handle_encrypted_endpoint_request(
    *,
    body: dict[str, Any],
    raw_body: bytes,
    signature_header: str | None,
) -> str:
    if not settings.whatsapp_flows_enabled:
        raise FlowEndpointException(503, "WhatsApp Flows are disabled.")
    if not settings.whatsapp_flows_private_key:
        raise FlowEndpointException(503, "WhatsApp Flow private key is missing.")
    if not verify_request_signature(raw_body, signature_header):
        raise FlowEndpointException(FLOW_ENDPOINT_SIGNATURE_ERROR, "Invalid Flow endpoint signature.")

    decrypted = decrypt_request(body)
    logger.info("Received WhatsApp Flow request action=%s screen=%s", decrypted.body.get("action"), decrypted.body.get("screen"))
    response = await get_next_screen(decrypted.body)
    return encrypt_response(response, aes_key=decrypted.aes_key, initial_vector=decrypted.initial_vector)


async def get_next_screen(decrypted_body: dict[str, Any]) -> dict[str, Any]:
    action = decrypted_body.get("action")
    data = decrypted_body.get("data") or {}
    flow_token = str(decrypted_body.get("flow_token") or "")

    if not isinstance(data, dict):
        data = {}

    # Bridge legacy flow sends into the modular provider contract.
    if not data.get("flow_key"):
        normalized = dict(data)
        normalized["flow_key"] = "gold_live_rates"
        if not normalized.get("search_id") and flow_token:
            if ":" in flow_token:
                parts = flow_token.split(":")
                if len(parts) >= 2 and parts[1].strip():
                    normalized["search_id"] = parts[1].strip()
            else:
                normalized["search_id"] = flow_token
        data = normalized
        decrypted_body = {**decrypted_body, "data": data}

    if action == "ping":
        return {"data": {"status": "active"}}

    if isinstance(data, dict) and data.get("error"):
        logger.warning("WhatsApp Flow client error: %s", data)
        return {"data": {"acknowledged": True}}

    if action in {"INIT", "data_exchange"}:
        return await build_flow_data_exchange_response(decrypted_body)

    logger.warning("Unhandled WhatsApp Flow request body: %s", decrypted_body)
    return {
        "screen": "GOLD_LIVE_RATES",
        "data": _fallback_screen_data("I could not load these live rates. Please check the WhatsApp chat for the text results."),
    }


async def _gold_live_rates_screen_data(flow_token: str) -> dict[str, Any]:
    if not flow_token:
        return _fallback_screen_data("I could not identify this search. Please ask Priya for fresh gold rates again.")

    search_doc = await persistence.get_search_session(flow_token)
    if not search_doc:
        return _fallback_screen_data("I could not find this search anymore. Please ask Priya for fresh gold rates again.")

    return gold_live_rates_screen_data_from_search_doc(search_doc)


def gold_live_rates_screen_data_from_search_doc(search_doc: dict[str, Any]) -> dict[str, Any]:
    results = _results_from_search_doc(search_doc)
    query = search_doc.get("query") or {}
    completed_at = search_doc.get("completed_at") or search_doc.get("updated_at")
    summary = search_doc.get("summary") or {}
    return gold_live_rates_screen_data_from_results(
        query=query,
        results=results,
        completed_at=completed_at,
        best_price=summary.get("best_price"),
    )


def gold_live_rates_screen_data_from_results(
    *,
    query: dict[str, Any] | None,
    results: list[UnifiedResult],
    completed_at: Any = None,
    best_price: Any = None,
) -> dict[str, Any]:
    updated_label = _format_updated_at(completed_at)
    resolved_best_price = best_price
    if resolved_best_price is None:
        resolved_best_price = min((result.price for result in results if result.price is not None), default=None)

    if not results:
        return _fallback_screen_data("I could not find matching gold prices this time. Try a more specific product or city.")

    return {
        "product": str((query or {}).get("product") or "Gold product"),
        "location": str((query or {}).get("location") or "your location"),
        "updated_at": updated_label,
        "best_price": f"INR {float(resolved_best_price):,.0f}" if isinstance(resolved_best_price, (int, float)) else "Not available",
        "result_1": _format_flow_result(results[0]),
        "result_2": _format_flow_result(results[1]) if len(results) > 1 else "",
        "result_3": _format_flow_result(results[2]) if len(results) > 2 else "",
        "disclaimer": "Final jewellery price can vary by purity, making charges, GST, and live market movement.",
    }


def _results_from_search_doc(search_doc: dict[str, Any]) -> list[UnifiedResult]:
    results: list[UnifiedResult] = []
    for raw_result in search_doc.get("top_results") or []:
        try:
            results.append(UnifiedResult.model_validate(raw_result))
        except Exception as exc:
            logger.warning("Skipping malformed Flow result for search=%s: %s", search_doc.get("search_id"), exc)
    return results[:3]


def _format_flow_result(result: UnifiedResult) -> str:
    parts = [result.name]
    if result.price is not None:
        parts.append(f"INR {result.price:,.0f}")
    parts.append("Online" if result.source_type == "online" else "Offline")
    if result.delivery_time:
        parts.append(result.delivery_time)
    if result.notes:
        parts.append(result.notes)
    if result.phone:
        parts.append(f"Phone: {result.phone}")
    if result.address:
        parts.append(result.address)
    return "\n".join(parts)


def _format_updated_at(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %I:%M %p")
    if isinstance(value, str) and value:
        return value
    return "Just now"


def _fallback_screen_data(message: str) -> dict[str, Any]:
    return {
        "product": "Gold live rates",
        "location": "India",
        "updated_at": "Just now",
        "best_price": "Not available",
        "result_1": message,
        "result_2": "",
        "result_3": "",
        "disclaimer": "You can continue in chat for fresh results.",
    }


def _load_private_key() -> rsa.RSAPrivateKey:
    key_text = settings.whatsapp_flows_private_key.replace("\\n", "\n")
    passphrase = (
        settings.whatsapp_flows_private_key_passphrase.encode("utf-8")
        if settings.whatsapp_flows_private_key_passphrase
        else None
    )
    key = serialization.load_pem_private_key(
        key_text.encode("utf-8"),
        password=passphrase,
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise FlowEndpointException(503, "Configured WhatsApp Flow key is not an RSA private key.")
    return key
