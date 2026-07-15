"""AuditService - append-only audit trail (Doc 2 §12).

Writes in its own session so an audit record persists independently of the caller's
transaction, and never raises (auditing must not break the request it records).

APPEND-ONLY invariant: audit rows are only ever INSERTed and read; nothing in the codebase
UPDATEs them, and there is no update/delete endpoint. On Postgres this is enforced at the DB
(infra/postgres_rls.sql grants SELECT/INSERT/DELETE but NOT UPDATE on audit_logs). The one
sanctioned removal is the time-based retention purge (services/retention.py) past the configured
horizon - never a targeted row edit/delete.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import String, and_, or_, select
from sqlalchemy import inspect as sa_inspect

from forge.db.base import SessionLocal
from forge.models import AuditLog

log = logging.getLogger("forge.audit")


def _encode_cursor(created_at: datetime | None, row_id: str) -> str:
    raw = f"{created_at.isoformat() if created_at else ''}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    ts_s, _, row_id = raw.partition("|")
    return datetime.fromisoformat(ts_s), row_id


def _truncate_string_columns(instance) -> None:
    """Clamp every String(length) column on `instance` to its column length, in place.

    Auditing must never fail on a length overflow. Postgres enforces VARCHAR(n) and
    rejects an over-length value (StringDataRightTruncationError); SQLite does not, so
    such overflows only surface in the Dockerized Postgres stack. This is the backstop for
    any caller-supplied value that could exceed its column (e.g. a crafted login email or a
    spoofed X-Forwarded-For IP). Uses mapper introspection so it stays correct as columns
    are added/resized, and keys on the Python attribute name (attr.key) - so the
    meta -> "metadata" column-name mismatch is irrelevant (meta is JSON, not String, and is
    skipped by the type check anyway).
    """
    for attr in sa_inspect(instance).mapper.column_attrs:
        col_type = getattr(attr.expression, "type", None)
        length = getattr(col_type, "length", None)
        if not isinstance(col_type, String) or not length:
            continue
        value = getattr(instance, attr.key, None)
        if isinstance(value, str) and len(value) > length:
            log.warning("audit: truncating %s from %d to %d chars", attr.key, len(value), length)
            setattr(instance, attr.key, value[:length])


class AuditService:
    @staticmethod
    async def log(
        *,
        tenant_id: str,
        action: str,
        actor_id: str | None = None,
        actor_email: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        project_id: str | None = None,
        ip: str | None = None,
        status: str = "ok",
        meta: dict[str, Any] | None = None,
    ) -> None:
        try:
            row = AuditLog(
                tenant_id=tenant_id, action=action, actor_id=actor_id,
                actor_email=actor_email, resource_type=resource_type,
                resource_id=resource_id, project_id=project_id, ip=ip,
                status=status, meta=meta or {},
            )
            _truncate_string_columns(row)  # never let a length overflow drop the record
            async with SessionLocal() as s:
                s.add(row)
                await s.commit()
        except Exception:  # noqa: BLE001 - auditing must never break the request
            log.exception("audit write failed for action=%s", action)

    @staticmethod
    async def recent(session, tenant_id: str, *, project_id: str | None = None, limit: int = 200) -> list[AuditLog]:
        q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if project_id:
            q = q.where(AuditLog.project_id == project_id)
        q = q.order_by(AuditLog.created_at.desc()).limit(limit)
        return list((await session.execute(q)).scalars())

    @staticmethod
    def _filtered(
        tenant_id: str, *, action: str | None = None, actor: str | None = None,
        project_id: str | None = None, start: datetime | None = None, end: datetime | None = None,
    ):
        q = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if action:
            q = q.where(AuditLog.action == action)
        if actor:  # match either the email or the id
            q = q.where(or_(AuditLog.actor_email == actor, AuditLog.actor_id == actor))
        if project_id:
            q = q.where(AuditLog.project_id == project_id)
        if start:
            q = q.where(AuditLog.created_at >= start)
        if end:
            q = q.where(AuditLog.created_at <= end)
        return q

    @staticmethod
    async def query(
        session, tenant_id: str, *, action: str | None = None, actor: str | None = None,
        project_id: str | None = None, start: datetime | None = None, end: datetime | None = None,
        cursor: str | None = None, limit: int = 100,
    ) -> tuple[list[AuditLog], str | None]:
        """Keyset-paginated, filtered audit query (finding g). Returns (rows, next_cursor);
        next_cursor is None on the last page. Ordered newest-first, tie-broken by id."""
        limit = max(1, min(int(limit), 1000))
        q = AuditService._filtered(tenant_id, action=action, actor=actor, project_id=project_id,
                                   start=start, end=end)
        if cursor:
            try:
                c_ts, c_id = _decode_cursor(cursor)
                q = q.where(or_(AuditLog.created_at < c_ts,
                                and_(AuditLog.created_at == c_ts, AuditLog.id < c_id)))
            except Exception as e:  # noqa: BLE001 - a bad cursor is a client error, not a 500
                raise ValueError("invalid cursor") from e
        q = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
        rows = list((await session.execute(q)).scalars())
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id)
        return rows, next_cursor

    @staticmethod
    async def export(
        session, tenant_id: str, *, action: str | None = None, actor: str | None = None,
        project_id: str | None = None, start: datetime | None = None, end: datetime | None = None,
        batch: int = 1000,
    ):
        """Async generator over ALL matching rows (oldest-first), paged internally so a bulk
        export never loads the whole table into memory (finding g)."""
        q = AuditService._filtered(tenant_id, action=action, actor=actor, project_id=project_id,
                                   start=start, end=end).order_by(
            AuditLog.created_at.asc(), AuditLog.id.asc()
        )
        offset = 0
        while True:
            rows = list((await session.execute(q.offset(offset).limit(batch))).scalars())
            if not rows:
                break
            for r in rows:
                yield r
            if len(rows) < batch:
                break
            offset += batch
