"""
Encrypt/decrypt tenant delivery_config at rest (bot tokens, webhook URLs).
Uses AES-256-GCM. Key from DELIVERY_CONFIG_KEY (32-byte urlsafe base64 or hex).
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key_bytes() -> bytes:
    raw = os.getenv("DELIVERY_CONFIG_KEY", "")
    if not raw:
        raise RuntimeError(
            "DELIVERY_CONFIG_KEY not set — generate with: python -c "
            "\"import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())\""
        )
    # Accept urlsafe base64 or hex
    try:
        k = base64.urlsafe_b64decode(raw)
        if len(k) == 32:
            return k
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    try:
        k = bytes.fromhex(raw)
        if len(k) == 32:
            return k
    except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    # Derive from passphrase (dev only) via SHA-256
    import hashlib
    return hashlib.sha256(raw.encode()).digest()


def encrypt_config(config: dict[str, Any]) -> str:
    """Return base64(nonce + ciphertext)."""
    key = _key_bytes()
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    plaintext = json.dumps(config, separators=(",", ":")).encode()
    ct = aes.encrypt(nonce, plaintext, None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_config(blob: str) -> dict[str, Any]:
    key = _key_bytes()
    raw = base64.urlsafe_b64decode(blob)
    nonce, ct = raw[:12], raw[12:]
    aes = AESGCM(key)
    plaintext = aes.decrypt(nonce, ct, None)
    return json.loads(plaintext.decode())


def store_delivery_config(tenant_id: str, config: dict) -> None:
    """Encrypt and write to tenants.delivery_config_enc; clear plaintext column."""
    from app.memory.supabase_client import get_supabase

    enc = encrypt_config(config)
    sb = get_supabase()
    sb.table("tenants").update({
        "delivery_config_enc": enc,
        "delivery_config": {},  # wipe plaintext
    }).eq("id", tenant_id).execute()


def load_delivery_config(tenant_row: dict) -> dict:
    """Prefer encrypted; fall back to legacy plaintext jsonb."""
    enc = tenant_row.get("delivery_config_enc")
    if enc:
        try:
            return decrypt_config(enc)
        except Exception as exc: __import__("logging").getLogger(__name__).debug("suppressed optional failure: %s", exc)
    return tenant_row.get("delivery_config") or {}
