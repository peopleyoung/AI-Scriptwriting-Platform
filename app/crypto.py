"""Authenticated encryption with read compatibility for earlier local records."""
import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag


def encrypt_bytes(payload: bytes, key: str) -> bytes:
    nonce = os.urandom(12)
    return b"MJ02" + nonce + AESGCM(hashlib.sha256(key.encode()).digest()).encrypt(nonce, payload, b"mujian-v2")


def decrypt_bytes(payload: bytes, key: str) -> bytes:
    digest = hashlib.sha256(key.encode()).digest()
    if payload.startswith(b"MJ02"):
        try:
            return AESGCM(digest).decrypt(payload[4:16], payload[16:], b"mujian-v2")
        except (InvalidTag, ValueError):
            raise ValueError("encrypted data cannot be verified") from None
    # Legacy records are read-only; all new writes use AES-GCM.
    tag, stream = payload[:16], payload[16:]
    if not hmac.compare_digest(hashlib.sha256(digest + stream).digest()[:16], tag):
        raise ValueError("encrypted data cannot be verified")
    return bytes(value ^ digest[index % len(digest)] for index, value in enumerate(stream))


def encrypt_secret(value: str | None, key: str) -> str | None:
    return base64.urlsafe_b64encode(encrypt_bytes(value.encode(), key)).decode() if value is not None else None


def decrypt_secret(value: str | None, key: str) -> str | None:
    if value is None:
        return None
    try:
        return decrypt_bytes(base64.urlsafe_b64decode(value), key).decode()
    except (ValueError, UnicodeError):
        raise ValueError("model credential cannot be decrypted") from None
