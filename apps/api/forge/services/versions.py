"""VersionService - generic entity change history (view + restore).

Every save of a versionable entity (workflow / agent / tool / component / auth provider /
knowledge source / project settings) captures an immutable `EntityVersion` snapshot of its
restorable fields, so a user can inspect the history and roll back a bad edit. Retention is
pruned to a limit resolved in precedence order: the entity's `project.config
["version_history_limit"]` (what the console Settings > Versioning panel writes), then a
`tenant.settings["version_history_limit"]` override, then the global
`settings.version_history_limit` (default 5) - framework-configurable, since Forge is meant
to be embedded in any application.

Kept generic (a field-spec registry per entity type) so a new versionable entity is one line,
and defensive (snapshotting must NEVER break the underlying save - `safe_snapshot` swallows).
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select

from forge.config import settings
from forge.models import (
    Agent,
    AuthProvider,
    Component,
    EntityVersion,
    KbSource,
    Project,
    Tenant,
    Tool,
    Workflow,
)
from forge.util.serialize import jsonable

log = logging.getLogger("forge.versions")

# entity_type -> (ORM model, restorable field names). Snapshotting serializes these fields;
# restore writes them back onto the live row. Anything not listed (ids, timestamps, embeddings,
# runtime status) is intentionally excluded so a restore only reverts user-authored config.
_SPEC: dict[str, tuple[type, list[str]]] = {
    "workflow": (Workflow, ["name", "description", "canvas", "executable", "status"]),
    "agent": (Agent, ["name", "config"]),
    "tool": (Tool, ["name", "kind", "config", "auth_provider_id", "enabled"]),
    "component": (
        Component,
        ["name", "title", "description", "props_schema", "html", "css", "actions", "sample_props", "kind", "enabled"],
    ),
    "auth_provider": (AuthProvider, ["name", "kind", "config", "credentials_ref"]),
    "kb_source": (KbSource, ["kind", "name", "folder", "uri", "meta"]),
    "project": (Project, ["name", "slug", "description", "config", "status"]),
}


def versioned_types() -> list[str]:
    return list(_SPEC.keys())


def _limit_for(tenant_settings: dict | None, project_config: dict | None = None) -> int:
    """Retention limit, in precedence order: the PROJECT's own config (what the console
    Settings > Versioning screen writes into project.config.version_history_limit), then a
    per-tenant override, then the global default. <=0 means keep all."""
    for src in (project_config, tenant_settings):
        val = (src or {}).get("version_history_limit")
        if val is not None:
            try:
                return int(val)
            except (TypeError, ValueError):
                pass
    return int(settings.version_history_limit)


def serialize(entity_type: str, obj) -> dict:
    _, fields = _SPEC[entity_type]
    return jsonable({f: getattr(obj, f, None) for f in fields})


class VersionService:
    @staticmethod
    async def snapshot(
        session,
        *,
        tenant_id: str,
        entity_type: str,
        entity_id: str,
        data: dict,
        project_id: str | None = None,
        label: str | None = None,
        author_id: str | None = None,
        author_email: str | None = None,
        tenant_settings: dict | None = None,
    ) -> EntityVersion | None:
        """Append a new snapshot (auto-incrementing version_no) and prune to the retention
        limit. `data` is the already-serialized field dict (see `serialize`)."""
        if entity_type not in _SPEC:
            return None
        last = (
            await session.execute(
                select(func.max(EntityVersion.version_no)).where(
                    EntityVersion.tenant_id == tenant_id,
                    EntityVersion.entity_type == entity_type,
                    EntityVersion.entity_id == entity_id,
                )
            )
        ).scalar() or 0
        ev = EntityVersion(
            tenant_id=tenant_id,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            version_no=int(last) + 1,
            label=label,
            snapshot=jsonable(data),
            author_id=author_id,
            author_email=author_email,
        )
        session.add(ev)
        await session.flush()
        # Resolve retention overrides so the documented precedence (project.config ->
        # tenant.settings -> global default in _limit_for) actually applies even on the
        # safe_snapshot path, which never passes tenant_settings. Fetch both from the DB here.
        project_config = None
        if project_id:
            proj = (await session.execute(
                select(Project).where(Project.tenant_id == tenant_id, Project.id == project_id)
            )).scalar_one_or_none()
            project_config = proj.config if proj else None
        if tenant_settings is None:
            tenant = (await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )).scalar_one_or_none()
            tenant_settings = tenant.settings if tenant else None
        await VersionService._prune(session, tenant_id, entity_type, entity_id, tenant_settings, project_config)
        return ev

    @staticmethod
    async def _prune(session, tenant_id: str, entity_type: str, entity_id: str,
                     tenant_settings: dict | None, project_config: dict | None = None) -> None:
        limit = _limit_for(tenant_settings, project_config)
        if limit <= 0:
            return  # keep-all
        rows = (
            await session.execute(
                select(EntityVersion.version_no)
                .where(
                    EntityVersion.tenant_id == tenant_id,
                    EntityVersion.entity_type == entity_type,
                    EntityVersion.entity_id == entity_id,
                )
                .order_by(EntityVersion.version_no.desc())
            )
        ).scalars().all()
        if len(rows) <= limit:
            return
        cutoff = rows[limit - 1]  # keep version_no >= cutoff (the newest `limit`)
        await session.execute(
            delete(EntityVersion).where(
                EntityVersion.tenant_id == tenant_id,
                EntityVersion.entity_type == entity_type,
                EntityVersion.entity_id == entity_id,
                EntityVersion.version_no < cutoff,
            )
        )

    @staticmethod
    async def list(session, tenant_id: str, entity_type: str, entity_id: str) -> list[EntityVersion]:
        return list(
            (
                await session.execute(
                    select(EntityVersion)
                    .where(
                        EntityVersion.tenant_id == tenant_id,
                        EntityVersion.entity_type == entity_type,
                        EntityVersion.entity_id == entity_id,
                    )
                    .order_by(EntityVersion.version_no.desc())
                )
            ).scalars()
        )

    @staticmethod
    async def get(session, tenant_id: str, entity_type: str, entity_id: str, version_no: int) -> EntityVersion | None:
        return (
            await session.execute(
                select(EntityVersion).where(
                    EntityVersion.tenant_id == tenant_id,
                    EntityVersion.entity_type == entity_type,
                    EntityVersion.entity_id == entity_id,
                    EntityVersion.version_no == version_no,
                )
            )
        ).scalar_one_or_none()

    @staticmethod
    async def restore(
        session,
        *,
        tenant_id: str,
        entity_type: str,
        entity_id: str,
        version_no: int,
        author_id: str | None = None,
        author_email: str | None = None,
        tenant_settings: dict | None = None,
    ):
        """Restore an entity to a prior snapshot. Snapshots the CURRENT state first (so the
        restore is itself undoable), then writes the snapshot's fields back onto the row and
        records a fresh version. Returns the restored ORM object, or None if not found."""
        if entity_type not in _SPEC:
            return None
        model, fields = _SPEC[entity_type]
        ev = await VersionService.get(session, tenant_id, entity_type, entity_id, version_no)
        if ev is None:
            return None
        obj = (
            await session.execute(
                select(model).where(model.id == entity_id, model.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if obj is None:
            return None
        # Snapshot current state so "restore" can itself be reverted.
        await VersionService.snapshot(
            session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
            data=serialize(entity_type, obj), project_id=getattr(obj, "project_id", None),
            label=f"before restore to v{version_no}", author_id=author_id, author_email=author_email,
            tenant_settings=tenant_settings,
        )
        snap = ev.snapshot or {}
        for f in fields:
            if f in snap:
                setattr(obj, f, snap[f])
        if hasattr(obj, "version") and isinstance(getattr(obj, "version", None), int):
            obj.version = obj.version + 1
        # Record the restored state as the newest version too (so history reads linearly).
        await VersionService.snapshot(
            session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
            data=serialize(entity_type, obj), project_id=getattr(obj, "project_id", None),
            label=f"restored from v{version_no}", author_id=author_id, author_email=author_email,
            tenant_settings=tenant_settings,
        )
        await session.commit()
        await session.refresh(obj)
        return obj


async def safe_snapshot(
    session,
    entity_type: str,
    obj,
    *,
    author: object | None = None,
    tenant_settings: dict | None = None,
) -> None:
    """Router-friendly helper: snapshot `obj` after a successful create/update. NEVER raises -
    versioning must not break the underlying save. `author` is a CurrentUser (id/email)."""
    try:
        await VersionService.snapshot(
            session,
            tenant_id=obj.tenant_id,
            entity_type=entity_type,
            entity_id=obj.id,
            data=serialize(entity_type, obj),
            project_id=getattr(obj, "project_id", None),
            label=getattr(obj, "name", None),
            author_id=getattr(author, "id", None),
            author_email=getattr(author, "email", None),
            tenant_settings=tenant_settings,
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - history is best-effort; the save already succeeded
        log.exception("failed to snapshot %s %s", entity_type, getattr(obj, "id", "?"))
