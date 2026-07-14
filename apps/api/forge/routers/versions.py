"""Entity version-history endpoints: list prior versions and restore one.

Generic over the versionable entity types (see forge.services.versions). Listing needs any
authenticated member (viewer+); restoring is a mutation (editor+). Everything is tenant-scoped
via the resolved user, so one tenant can't read or restore another's history.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_current_user, get_session, require_role
from forge.services.versions import VersionService, versioned_types

router = APIRouter(prefix="/v1/versions", tags=["versions"])


class VersionOut(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    version_no: int
    label: str | None = None
    author_email: str | None = None
    created_at: datetime


class VersionDetailOut(VersionOut):
    snapshot: dict


class RestoreIn(BaseModel):
    version_no: int


def _check_type(entity_type: str) -> None:
    if entity_type not in versioned_types():
        raise HTTPException(404, f"unknown entity type '{entity_type}'")


@router.get("/{entity_type}/{entity_id}", response_model=list[VersionOut])
async def list_versions(
    entity_type: str,
    entity_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    _check_type(entity_type)
    return await VersionService.list(session, tenant_id, entity_type, entity_id)


@router.get("/{entity_type}/{entity_id}/{version_no}", response_model=VersionDetailOut)
async def get_version(
    entity_type: str,
    entity_id: str,
    version_no: int,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    _check_type(entity_type)
    ev = await VersionService.get(session, tenant_id, entity_type, entity_id, version_no)
    if ev is None:
        raise HTTPException(404, "version not found")
    return ev


@router.post("/{entity_type}/{entity_id}/restore", response_model=VersionDetailOut)
async def restore_version(
    entity_type: str,
    entity_id: str,
    body: RestoreIn,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    user: CurrentUser = Depends(require_role("editor")),
):
    _check_type(entity_type)
    obj = await VersionService.restore(
        session, tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id,
        version_no=body.version_no, author_id=user.id, author_email=user.email,
    )
    if obj is None:
        raise HTTPException(404, "entity or version not found")
    # Return the newest version row (the restored state) so the client can refresh its view.
    versions = await VersionService.list(session, tenant_id, entity_type, entity_id)
    return versions[0]


# get_current_user is imported for symmetry with other routers that attribute actions; the
# restore route uses require_role which already resolves the user.
_ = get_current_user
