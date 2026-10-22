"""Symmetric encryption for tool credentials — Fernet, keyed by ENCRYPTION_KEY.

Mirrors services/orchestrator/app/secrets.py; this half only ever encrypts
(on credential creation), the orchestrator's half only ever decrypts (at
dispatch time) — see docs/02-schema.md §1 for why secrets live in their own
table instead of as a column on tool_credentials.
"""

from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())
