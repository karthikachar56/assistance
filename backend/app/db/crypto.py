"""AES-256-GCM encryption helpers for sensitive ledger & DPDP transcript data."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import ROOT_DIR, get_settings


logger = logging.getLogger(__name__)
_NONCE_LEN = 12
_KEY_FILE = ROOT_DIR / ".ledger.key"
_TMP_KEY_FILE = Path(tempfile.gettempdir()) / "voice_khata_ledger.key"


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


def _serverless_fallback_seed(settings: Any) -> str | None:
    """Return stable shared secret material across serverless instances when LEDGER_ENCRYPTION_KEY is unset."""
    mongo = (settings.mongodb_url or os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI") or "").strip().strip('"').strip("'")
    if mongo:
        return f"mongo:{mongo}"
    gemini = (settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
    if gemini and gemini != "your_gemini_api_key_here":
        return f"gemini:{gemini}"
    gnani = (settings.gnani_api_key or os.environ.get("GNANI_API_KEY") or "").strip().strip('"').strip("'")
    if gnani:
        return f"gnani:{gnani}"
    project_id = (os.environ.get("VERCEL_PROJECT_ID") or "").strip()
    if project_id:
        return f"vercel:{project_id}"
    return None


class FieldCipher:
    """Authenticated encryption wrapper using AES-256-GCM."""

    def __init__(self, key_material: str | None = None) -> None:
        settings = get_settings()
        raw = key_material or settings.ledger_encryption_key
        if raw and raw.strip():
            key = _decode_or_derive_key(raw)
        elif _KEY_FILE.exists():
            key = _decode_or_derive_key(_KEY_FILE.read_text(encoding="utf-8"))
        elif settings.is_serverless and (serverless_seed := _serverless_fallback_seed(settings)):
            key = hashlib.sha256(b"voice-khata-serverless:" + serverless_seed.encode("utf-8")).digest()
        elif _TMP_KEY_FILE.exists():
            key = _decode_or_derive_key(_TMP_KEY_FILE.read_text(encoding="utf-8"))
        else:
            generated = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
            target_file = _TMP_KEY_FILE if os.environ.get("VERCEL") else _KEY_FILE
            try:
                target_file.write_text(generated, encoding="utf-8")
            except OSError:
                try:
                    _TMP_KEY_FILE.write_text(generated, encoding="utf-8")
                except OSError:
                    pass
            key = _decode_or_derive_key(generated)
        self._aes = AESGCM(key)
        self._signing_key = hashlib.sha256(b"voice-khata-auth:" + key).digest()

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
        try:
            return self._aes.decrypt(nonce, ciphertext, None).decode("utf-8")
        except InvalidTag:
            logger.warning("Failed to decrypt ciphertext token; check LEDGER_ENCRYPTION_KEY.")
            return None

    def sign_token(self, payload: dict[str, Any], expires_in_seconds: int = 7 * 24 * 3600) -> str:
        """Sign a compact HMAC-SHA256 bearer token for stateless & DB-backed sessions."""
        now = int(time.time())
        body = {**payload, "iat": now, "exp": now + max(60, int(expires_in_seconds))}
        raw_json = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
        sig = hmac.new(self._signing_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
        return f"vk1.{payload_b64}.{sig_b64}"

    def verify_token(self, token: str | None) -> dict[str, Any] | None:
        """Verify HMAC signature and expiry of a bearer token; return claims or None."""
        if not token or not isinstance(token, str):
            return None
        parts = token.strip().split(".")
        if len(parts) != 3 or parts[0] != "vk1":
            return None
        payload_b64, sig_b64 = parts[1], parts[2]
        expected_sig = hmac.new(self._signing_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        expected_b64 = base64.urlsafe_b64encode(expected_sig).decode("ascii").rstrip("=")
        if not hmac.compare_digest(sig_b64, expected_b64):
            return None
        try:
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(claims, dict) or int(claims.get("exp", 0)) < int(time.time()):
            return None
        return claims


def hash_password(password: str) -> str:
    """Hash a password with a per-user random salt using scrypt."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return (
        "scrypt$"
        + base64.urlsafe_b64encode(salt).decode("ascii")
        + "$"
        + base64.urlsafe_b64encode(dk).decode("ascii")
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Constant-time verification against a stored scrypt hash."""
    if not password or not stored_hash:
        return False
    parts = stored_hash.split("$", 2)
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    try:
        salt = base64.urlsafe_b64decode(parts[1].encode("ascii"))
        expected = base64.urlsafe_b64decode(parts[2].encode("ascii"))
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=len(expected))
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)

