"""AES-256-GCM encryption helpers for sensitive ledger & DPDP transcript data."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import ROOT_DIR, get_settings


_NONCE_LEN = 12
_KEY_FILE = ROOT_DIR / ".ledger.key"


def _decode_or_derive_key(raw: str) -> bytes:
    """Normalize a user-supplied secret into a 32-byte AES-256 key."""
    cleaned = raw.strip()
    if len(cleaned) == 64:
        try:
            return bytes.fromhex(cleaned)
        except ValueError:
            pass
    try:
        decoded = base64.urlsafe_b64decode(cleaned + "=" * (-len(cleaned) % 4))
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    return hashlib.sha256(cleaned.encode("utf-8")).digest()


class FieldCipher:
    """Authenticated encryption wrapper using AES-256-GCM."""

    def __init__(self, key_material: str | None = None) -> None:
        raw = key_material or get_settings().ledger_encryption_key
        if raw and raw.strip():
            key = _decode_or_derive_key(raw)
        elif _KEY_FILE.exists():
            key = _decode_or_derive_key(_KEY_FILE.read_text(encoding="utf-8"))
        else:
            generated = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            _KEY_FILE.write_text(generated, encoding="utf-8")
            key = _decode_or_derive_key(generated)
        self._aes = AESGCM(key)

    def encrypt(self, plaintext: str | None) -> str | None:
        """Encrypt UTF-8 text into a base64 token; preserve None/empty cleanly."""
        if plaintext is None or plaintext == "":
            return plaintext
        nonce = os.urandom(_NONCE_LEN)
        ciphertext = self._aes.encrypt(nonce, plaintext.encode("utf-8"), None)
        return "enc:v1:" + base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, token: str | None) -> str | None:
        """Decrypt a previously encrypted token, tolerating plain legacy strings."""
        if token is None or token == "":
            return token
        if not token.startswith("enc:v1:"):
            return token
        raw = base64.urlsafe_b64decode(token[len("enc:v1:") :].encode("ascii"))
        nonce, ciphertext = raw[:_NONCE_LEN], raw[_NONCE_LEN:]
        return self._aes.decrypt(nonce, ciphertext, None).decode("utf-8")

