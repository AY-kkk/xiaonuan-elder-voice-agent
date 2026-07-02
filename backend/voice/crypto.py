"""Encrypt/decrypt sensitive voice sample bytes at rest."""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    secret = (
        os.getenv("VOICE_SAMPLE_SECRET", "").strip()
        or os.getenv("FAMILY_API_TOKEN", "").strip()
        or "local-demo-voice-sample-secret"
    )
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt_sample(data: bytes) -> bytes:
    return _fernet().encrypt(data)


def decrypt_sample(data: bytes) -> bytes:
    return _fernet().decrypt(data)
