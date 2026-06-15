"""Fernet master-key management + symmetric encrypt/decrypt.

The master key is read from `settings.secret_key_file` (generated on first run,
0600 where supported). It is NEVER stored in the DB. In prod, source the key from
KMS/Vault instead and keep this interface.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet

from forge.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    path = Path(settings.secret_key_file)
    if path.exists():
        key = path.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)  # best-effort; no-op semantics on Windows
        except OSError:
            pass
    return Fernet(key)


def encrypt(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt(token: bytes) -> str:
    return _fernet().decrypt(token).decode("utf-8")
