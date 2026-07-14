"""Channel CRUD + public inbound endpoints (email inbound-parse, Teams bot)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from forge.channels import email as email_ch
from forge.channels import teams as teams_ch
from forge.config import settings
from forge.db.base import SessionLocal
from forge.deps import CurrentUser, current_tenant_id, get_run_service, get_session, require_role
from forge.services.channels import ChannelService
from forge.services.dispatch import dispatch_message
from forge.services.handoff import (
    HITL_META_KEY,
    HandoffService,
    interrupt_ack,
    interrupt_hitl_meta,
    interrupt_reason,
)
from forge.services.runs import RunService
from forge.util.ratelimit import rate_limiter

log = logging.getLogger("forge.channels.router")

router = APIRouter(prefix="/v1/projects/{project_id}/channels", tags=["channels"])
public = APIRouter(tags=["channels"])  # unauthenticated inbound webhooks


class ChannelIn(BaseModel):
    type: str
    name: str
    workflow_id: str | None = None
    config: dict = {}


class ChannelPatch(BaseModel):
    name: str | None = None
    workflow_id: str | None = None
    config: dict | None = None
    enabled: bool | None = None


def _out(ch) -> dict:
    base = settings.public_base_url.rstrip("/")
    item = {"id": ch.id, "type": ch.type, "name": ch.name, "workflow_id": ch.workflow_id,
            "enabled": ch.enabled, "config": ch.config, "key": ch.key}
    if ch.type == "email":
        item["inbound_url"] = f"{base}/v1/channels/email/{ch.key}/inbound"
    elif ch.type == "teams":
        item["messaging_endpoint"] = f"{base}/v1/channels/teams/{ch.key}/messages"
    return item


@router.get("")
async def list_channels(project_id: str, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id)):
    return [_out(c) for c in await ChannelService.list(session, tenant_id, project_id)]


@router.post("", status_code=201)
async def create_channel(project_id: str, body: ChannelIn, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    try:
        ch = await ChannelService.create(session, tenant_id, project_id, type_=body.type, name=body.name,
                                         workflow_id=body.workflow_id, config=body.config)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _out(ch)


@router.patch("/{channel_id}")
async def update_channel(project_id: str, channel_id: str, body: ChannelPatch,
                         session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    ch = await ChannelService.get(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
    ch = await ChannelService.update(session, ch, name=body.name, workflow_id=body.workflow_id,
                                     config=body.config, enabled=body.enabled)
    return _out(ch)


@router.delete("/{channel_id}")
async def delete_channel(project_id: str, channel_id: str, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    ch = await ChannelService.get(session, tenant_id, channel_id)
    if not ch:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "channel not found")
    await ChannelService.delete(session, ch)
    return {"ok": True}


# --------------------- public inbound ---------------------

async def _resolve(type_: str, key: str):
    async with SessionLocal() as s:
        ch = await ChannelService.by_key(s, type_, key)
        if ch is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown or disabled channel")
        workflow_id = await ChannelService.resolve_workflow_id(s, ch)
    if not workflow_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "channel has no workflow bound")
    return ch, workflow_id


async def _maybe_open_handoff(ch, result: dict, *, customer, customer_message, reply_context) -> str | None:
    """If the run paused at an interrupt, open a HandoffRequest and return a customer-facing
    acknowledgement. A text channel (email/Teams) can't resume an HITL pause inline, so ANY
    interrupt - explicit handoff OR an approval/input pause - must be tracked and acknowledged
    rather than falling through to a stale/empty partial answer (audit F8)."""
    if not result.get("interrupted"):
        return None
    interrupts = result.get("interrupts")
    reason = interrupt_reason(interrupts) or (
        "Conversation paused awaiting input/approval - a team member will follow up."
    )
    # Persist the interrupting node's allowed_decisions so the human's channel reply is coerced
    # to a valid decision before resuming (a Router keyed on approve/reject then matches) - C.
    hitl_meta = interrupt_hitl_meta(interrupts)
    ctx = dict(reply_context or {})
    if hitl_meta.get("allowed_decisions"):
        ctx[HITL_META_KEY] = {
            "allowed_decisions": hitl_meta["allowed_decisions"], "kind": hitl_meta.get("kind"),
        }
    async with SessionLocal() as s:
        await HandoffService.create(
            s, channel=ch, tenant_id=ch.tenant_id, project_id=ch.project_id,
            workflow_id=result.get("workflow_id"), run_id=result.get("run_id"),
            thread_id=result.get("thread_id"), customer=customer, customer_message=customer_message,
            reason=reason, reply_context=ctx,
        )
    return interrupt_ack(interrupts) or "A team member will follow up with you shortly."


@public.post("/v1/channels/email/{key}/inbound")
async def email_inbound(key: str, request: Request, run_service: RunService = Depends(get_run_service)):
    ch, workflow_id = await _resolve("email", key)
    if not rate_limiter.allow(f"email:{key}", rate=120, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - provider posts form-encoded
        form = await request.form()
        payload = dict(form)
    parsed = email_ch.parse_inbound(payload)
    text = email_ch.build_input_text(parsed, include_subject=(ch.config or {}).get("include_subject", True))
    # Continue the same email thread across replies so the conversation keeps context (F6).
    conv_key = parsed.get("thread_ref") or (parsed.get("from_addr") or None)
    result = await dispatch_message(run_service, tenant_id=ch.tenant_id, project_id=ch.project_id,
                                    workflow_id=workflow_id, text=text, conversation_key=conv_key,
                                    source="channel_email")
    ack = await _maybe_open_handoff(ch, result, customer=parsed.get("from_addr"), customer_message=text, reply_context=parsed)
    reply_text = ack or result.get("answer")
    delivered = None
    if (ch.config or {}).get("reply", True) and reply_text:
        try:
            # send_reply now retries with backoff and returns whether an email was actually sent
            # (False = SMTP not configured); a failure raises so we can record it (audit E).
            delivered = await email_ch.send_reply(ch, parsed, reply_text)
        except Exception:  # noqa: BLE001 - reply delivery failure shouldn't 500 the webhook
            log.warning("email reply delivery failed for channel %s", ch.id, exc_info=True)
            delivered = False
    return {"ok": True, "handoff": bool(ack), "delivered": delivered}


@public.post("/v1/channels/teams/{key}/messages")
async def teams_messages(key: str, request: Request, run_service: RunService = Depends(get_run_service),
                         authorization: str | None = Header(default=None)):
    ch, workflow_id = await _resolve("teams", key)
    activity = await request.json()
    # Authenticate the inbound Bot Framework activity (audit D): the public messaging endpoint is
    # otherwise driveable by anyone. When the channel has an app_id and verify_jwt isn't disabled,
    # require a valid connector JWT (audience == app_id). Without an app_id the audience can't be
    # validated, so we can only warn - configure app_id to enable authentication.
    cfg = ch.config or {}
    app_id = cfg.get("app_id")
    if app_id and cfg.get("verify_jwt", True):
        if not await teams_ch.verify_bot_jwt(authorization, app_id=app_id):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid Bot Framework token")
    elif not app_id:
        log.warning("Teams inbound for channel %s is UNAUTHENTICATED (no app_id configured to verify the JWT)", ch.id)
    parsed = teams_ch.parse_activity(activity)
    # Accept a typed message OR a card action (Action.Submit) - a button press has no `text`, its
    # data rides in `value`; inbound_text derives an input string from either (audit D).
    text = teams_ch.inbound_text(parsed)
    if parsed.get("type") not in ("message",) or not text:
        return {"ok": True}  # ignore non-message activities (typing, conversationUpdate, …)
    if not rate_limiter.allow(f"teams:{key}", rate=120, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    result = await dispatch_message(run_service, tenant_id=ch.tenant_id, project_id=ch.project_id,
                                    workflow_id=workflow_id, text=text,
                                    conversation_key=parsed.get("conversation_id"),
                                    source="channel_teams")
    ack = await _maybe_open_handoff(ch, result, customer=parsed.get("from_name"), customer_message=text, reply_context=parsed)
    reply_text = ack or result.get("answer")
    delivered = None
    if reply_text:
        try:
            delivered = await teams_ch.send_reply(ch, parsed, reply_text)
        except Exception:  # noqa: BLE001 - reply delivery failure shouldn't 500 the webhook
            log.warning("teams reply delivery failed for channel %s", ch.id, exc_info=True)
            delivered = False
    return {"ok": True, "handoff": bool(ack), "delivered": delivered}
