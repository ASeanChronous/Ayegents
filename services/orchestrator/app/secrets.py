"""Symmetric encryption for tool credentials — Fernet, keyed by ENCRYPTION_KEY.

Deliberately simple for the scaffold. docs/02-schema.md §1 calls for
pgsodium/Vault so the key never passes through application memory at all —
swap this module for that once that's wired up. Until then, this file (and
its twin in services/api) is the only code that ever touches plaintext.
"""

from cryptography.fernet import Fernet

from app.config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()
