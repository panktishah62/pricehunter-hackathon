from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FlowEndpointException(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class DecryptedFlowRequest:
    decrypted_body: dict
    aes_key: bytes
    initial_vector: bytes


def _normalize_private_key(private_key_pem: str) -> str:
    return private_key_pem.replace("\\n", "\n").strip()


def verify_request_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str | None,
) -> bool:
    if not app_secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    provided = signature_header.removeprefix("sha256=").encode("utf-8")
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest().encode("utf-8")
    return hmac.compare_digest(digest, provided)


def decrypt_request(
    body: dict,
    *,
    private_key_pem: str,
    passphrase: str | None = None,
) -> DecryptedFlowRequest:
    try:
        encrypted_aes_key = base64.b64decode(body["encrypted_aes_key"])
        encrypted_flow_data = base64.b64decode(body["encrypted_flow_data"])
        initial_vector = base64.b64decode(body["initial_vector"])
    except Exception as exc:
        raise FlowEndpointException(400, "Invalid encrypted flow payload.") from exc

    try:
        private_key = serialization.load_pem_private_key(
            _normalize_private_key(private_key_pem).encode("utf-8"),
            password=(passphrase or "").encode("utf-8") if passphrase else None,
        )
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
            421,
            "Failed to decrypt the request. Please verify your private key.",
        ) from exc

    try:
        aesgcm = AESGCM(aes_key)
        decrypted_json = aesgcm.decrypt(initial_vector, encrypted_flow_data, None).decode("utf-8")
        decrypted_body = json.loads(decrypted_json)
    except Exception as exc:
        raise FlowEndpointException(400, "Failed to decrypt flow data.") from exc

    return DecryptedFlowRequest(
        decrypted_body=decrypted_body,
        aes_key=aes_key,
        initial_vector=initial_vector,
    )


def encrypt_response(
    response: dict,
    *,
    aes_key: bytes,
    initial_vector: bytes,
) -> str:
    flipped_iv = bytes((~byte) & 0xFF for byte in initial_vector)
    aesgcm = AESGCM(aes_key)
    encrypted = aesgcm.encrypt(
        flipped_iv,
        json.dumps(response, separators=(",", ":")).encode("utf-8"),
        None,
    )
    return base64.b64encode(encrypted).decode("utf-8")
