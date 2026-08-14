"""A project's triggers (webhook URLs, schedules): who they run as, and who they belong to.

Two separate questions, deliberately kept apart - conflating them is what makes automation
ownership muddy:

  * `run_as_user_id` - WHOSE connected accounts the run uses. A trigger fires with nobody signed
    in, so it carries an identity; that is what lets a scheduled workflow send from a connected
    Gmail. `PUT /{id}/run-as` moves it.
  * `scope` - WHO the trigger belongs to, and therefore who sees it here. `project` is a team
    automation (the prod monitor, the nightly build) that everyone in the project works with;
    `user` is someone's own (a salesperson's lead-chaser), listed only for them.
    `PUT /{id}/scope` moves it.

They vary independently on purpose: a shared team automation can legitimately act through one
person's Slack account, and a personal automation can be handed to a colleague without becoming
everybody's.

SCOPE IS OWNERSHIP, NOT A LOCK. A webhook URL is a credential - anyone holding it fires the
trigger whatever its scope says - and every run is visible in Traces regardless. Scope decides
whose list a trigger appears in and who may change it, nothing more.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.config import settings
from forge.deps import (
    CurrentUser,
    current_tenant_id,
    effective_role,
    get_current_user,
    get_session,
    role_at_least,
)
from forge.models import Trigger, User
from forge.services.triggers import SCOPES

router = APIRouter(prefix="/v1/projects/{project_id}/triggers", tags=["triggers"])


class RunAsIn(BaseModel):
    # Omitted / null means "me" - the common case, someone claiming an automation they now own.
    user_id: str | None = None


class ScopeIn(BaseModel):
    scope: str  # project | user


async def _labels(session: AsyncSession, tenant_id: str, user_ids: set[str]) -> dict[str, str]:
    """Map user id -> email for display. A missing id (a user who has since been removed) is
    deliberately absent, so the UI can say the automation has no working identity rather than
    printing a bare uuid that means nothing to anyone."""
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = (await session.execute(
        select(User).where(User.tenant_id == tenant_id, User.id.in_(ids))
    )).scalars()
    return {u.id: u.email for u in rows}


@router.get("")
async def list_triggers(
    request: Request,
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    user: CurrentUser = Depends(get_current_user),
):
    """Project triggers, plus your own personal ones.

    Someone else's personal trigger is hidden - a colleague's private lead-chaser is noise on
    your screen. It is NOT hidden from an admin, though: every run it produces is already in
    Traces, so filtering it out of the one screen that explains WHY those runs happen would be
    false privacy that only costs the person who has to answer for the project.
    """
    rows = list((await session.execute(
        select(Trigger).where(Trigger.tenant_id == tenant_id, Trigger.project_id == project_id)
    )).scalars())
    me = str(user.id)
    oversight = role_at_least(await effective_role(user, request), "admin")
    base = settings.public_base_url.rstrip("/")
    emails = await _labels(session, tenant_id, {t.run_as_user_id for t in rows if t.run_as_user_id})
    out = []
    for t in rows:
        personal = t.scope == "user"
        mine = bool(t.run_as_user_id) and t.run_as_user_id == me
        if personal and not mine and not oversight:
            continue
        item = {
            "id": t.id, "workflow_id": t.workflow_id, "node_id": t.node_id, "kind": t.kind,
            "enabled": t.enabled, "config": t.config,
            "last_fired_at": t.last_fired_at.isoformat() if t.last_fired_at else None,
            "scope": t.scope or "project",
            "run_as_user_id": t.run_as_user_id,
            # None when unset OR when that user no longer exists - both mean "this trigger has
            # no connected accounts to draw on", which is the thing worth showing.
            "run_as_email": emails.get(t.run_as_user_id or ""),
            "run_as_is_me": mine,
            # True only when you are seeing someone else's personal trigger because of your role.
            "visible_via_oversight": personal and not mine,
        }
        if t.kind == "webhook_in" and t.key:
            item["webhook_url"] = f"{base}/v1/hooks/{t.key}"
        out.append(item)
    return out


@router.put("/{trigger_id}/scope")
async def set_scope(
    request: Request,
    project_id: str,
    trigger_id: str,
    body: ScopeIn,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    user: CurrentUser = Depends(get_current_user),
):
    """Move a trigger between "the team's" and "mine".

    Sharing it with the project is an editor decision: colleagues will see it, depend on it, and
    be able to reassign it. Making one personal is open to the person it runs as - it is their
    account doing the work - but not to a bystander, because hiding a trigger others rely on
    would look exactly like it disappearing.

    Making one personal is deliberately NOT an ordinary editor power. "Personal" means "listed
    only for the person it runs as", so an editor doing it to a trigger that runs as someone else
    - or as nobody - removes it from their own screen the instant they click, with no control
    left to put it back. Only the person it runs as, or an admin (who keeps oversight visibility
    either way), can take that step.
    """
    if body.scope not in SCOPES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"scope must be one of {list(SCOPES)}")
    trigger = (await session.execute(
        select(Trigger).where(
            Trigger.tenant_id == tenant_id, Trigger.project_id == project_id, Trigger.id == trigger_id,
        )
    )).scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger not found")

    role = await effective_role(user, request)
    is_editor = role_at_least(role, "editor")
    is_admin = role_at_least(role, "admin")
    owns_it = bool(trigger.run_as_user_id) and trigger.run_as_user_id == str(user.id)
    if body.scope == "project" and not is_editor:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sharing a trigger with the project requires role 'editor'")
    if body.scope == "user":
        if not trigger.run_as_user_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "this trigger doesn't run as anyone yet - set 'Runs as' first, or it would be "
                "personal to nobody and listed for nobody",
            )
        if not (owns_it or is_admin):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "only the person a trigger runs as (or an admin) can make it personal",
            )
    trigger.scope = body.scope
    await session.commit()
    return {"scope": trigger.scope}


@router.put("/{trigger_id}/run-as")
async def set_run_as(
    request: Request,
    project_id: str,
    trigger_id: str,
    body: RunAsIn | None = None,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
    user: CurrentUser = Depends(get_current_user),
):
    """Point this trigger at a person's connected accounts.

    Claiming it for YOURSELF is open to any real logged-in user - it only ever narrows the run to
    accounts you personally connected. Assigning it to SOMEONE ELSE means their credentials get
    used by a workflow they may not have touched, so that is an editor decision.
    """
    if str(user.id).startswith(("apikey:", "service")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "run-as requires a user identity")
    trigger = (await session.execute(
        select(Trigger).where(
            Trigger.tenant_id == tenant_id, Trigger.project_id == project_id, Trigger.id == trigger_id,
        )
    )).scalar_one_or_none()
    if trigger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "trigger not found")

    target = (body.user_id if body else None) or str(user.id)
    if target != str(user.id):
        if not role_at_least(await effective_role(user, request), "editor"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "assigning a trigger to another person's accounts requires role 'editor'",
            )
        exists = (await session.execute(
            select(User).where(User.tenant_id == tenant_id, User.id == target)
        )).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such user in this workspace")

    trigger.run_as_user_id = target
    await session.commit()
    emails = await _labels(session, tenant_id, {target})
    return {"run_as_user_id": target, "run_as_email": emails.get(target)}
