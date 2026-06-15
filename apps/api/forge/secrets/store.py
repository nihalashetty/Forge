"""SecretStore — encrypted read/write over the `secrets` table.

Values are stored as encrypted JSON (so a credential can be a string OR a structured
object like {"username","password"}). References use `secret://proj/<name>`; the
tenant + project scope comes from the caller (never the ref), so refs can't cross
tenants. Reads are audited (Doc 2 §12) — TODO: wire audit_log.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.models import Secret
from forge.secrets.fernet import decrypt, encrypt


class SecretNotFound(KeyError):
    pass


class SecretStore:
    def __init__(self, session_factory=SessionLocal) -> None:
        self._sf = session_factory

    @staticmethod
    def parse_ref(ref: str) -> tuple[str, str]:
        """('secret'|'vault', name) from 'secret://proj/<name>' or 'secret://<name>'."""
        if "://" not in ref:
            return "secret", ref
        scheme, rest = ref.split("://", 1)
        name = rest.rstrip("/").split("/")[-1]
        return scheme, name

    async def write(
        self, session, *, tenant_id: str, project_id: str, name: str, value: Any, kind: str = "generic"
    ) -> Secret:
        existing = (
            await session.execute(
                select(Secret).where(
                    Secret.tenant_id == tenant_id, Secret.project_id == project_id, Secret.name == name
                )
            )
        ).scalar_one_or_none()
        blob = encrypt(json.dumps(value))
        if existing:
            existing.encrypted_value = blob
            existing.version += 1
            existing.kind = kind
            secret = existing
        else:
            secret = Secret(tenant_id=tenant_id, project_id=project_id, name=name, kind=kind, encrypted_value=blob)
            session.add(secret)
        await session.commit()
        await session.refresh(secret)
        return secret

    # last_used_at is bookkeeping, not security audit — committing it on EVERY read
    # added a write+fsync (~12ms+) to every node that resolves a key. Throttle to
    # at most one write per ref per window.
    _LAST_USED_WRITE_WINDOW = timedelta(minutes=5)

    async def read_ref(self, *, tenant_id: str, project_id: str, ref: str) -> Any:
        scheme, name = self.parse_ref(ref)
        if scheme == "vault":  # pragma: no cover - enterprise path
            raise NotImplementedError("vault:// refs require the Vault adapter (enterprise).")
        async with self._sf() as session:
            secret = (
                await session.execute(
                    select(Secret).where(
                        Secret.tenant_id == tenant_id, Secret.project_id == project_id, Secret.name == name
                    )
                )
            ).scalar_one_or_none()
            if secret is None:
                raise SecretNotFound(f"No secret named {name!r} in project")
            now = datetime.utcnow()
            if secret.last_used_at is None or now - secret.last_used_at > self._LAST_USED_WRITE_WINDOW:
                secret.last_used_at = now
                await session.commit()
            return json.loads(decrypt(secret.encrypted_value))
