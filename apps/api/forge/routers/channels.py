"""Channel CRUD + public inbound endpoints (email inbound-parse, Teams bot)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from forge.channels import email as email_ch
from forge.channels import teams as teams_ch
from forge.config import settings
from forge.db.base import SessionLocal
from forge.deps import CurrentUser, current_tenant_id, get_run_service, get_session, require_role
from forge.services.channels import ChannelService
from forge.services.dispatch import dispatch_message
from forge.services.runs import RunService
from forge.util.ratelimit import rate_limiter

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


def _handoff_reason(result: dict) -> str | None:
    for group in result.get("interrupts") or []:
        for it in (group or []):
            val = it.get("value") if isinstance(it, dict) else None
            if isinstance(val, dict) and val.get("handoff"):
                return val.get("reason") or "Escalated to a human agent."
    return None


async def _maybe_open_handoff(ch, result: dict, *, customer, customer_message, reply_context) -> str | None:
    """If the run paused at an interrupt, open a HandoffRequest and return a customer-facing
    acknowledgement. A text channel (email/Teams) can't resume an HITL pause inline, so ANY
    interrupt - explicit handoff OR an approval/input pause - must be tracked and acknowledged
    rather than falling through to a stale/empty partial answer (audit F8)."""
    if not result.get("interrupted"):
        return None
    reason = _handoff_reason(result) or (
        "Conversation paused awaiting input/approval - a team member will follow up."
    )
    from forge.services.handoff import HandoffService
    async with SessionLocal() as s:
        await HandoffService.create(
            s, channel=ch, tenant_id=ch.tenant_id, project_id=ch.project_id,
            workflow_id=result.get("workflow_id"), run_id=result.get("run_id"),
            thread_id=result.get("thread_id"), customer=customer, customer_message=customer_message,
            reason=reason, reply_context=reply_context,
        )
    # surface the configured ack_message if present
    for group in result.get("interrupts") or []:
        for it in (group or []):
            val = it.get("value") if isinstance(it, dict) else None
            if isinstance(val, dict) and val.get("ack_message"):
                return val["ack_message"]
    return "A team member will follow up with you shortly."


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
                                    workflow_id=workflow_id, text=text, conversation_key=conv_key)
    ack = await _maybe_open_handoff(ch, result, customer=parsed.get("from_addr"), customer_message=text, reply_context=parsed)
    reply_text = ack or result.get("answer")
    if (ch.config or {}).get("reply", True) and reply_text:
        try:
            await email_ch.send_reply(ch, parsed, reply_text)
        except Exception:  # noqa: BLE001 - reply delivery failure shouldn't 500 the webhook
            pass
    return {"ok": True, "handoff": bool(ack)}


@public.post("/v1/channels/teams/{key}/messages")
async def teams_messages(key: str, request: Request, run_service: RunService = Depends(get_run_service)):
    ch, workflow_id = await _resolve("teams", key)
    activity = await request.json()
    parsed = teams_ch.parse_activity(activity)
    if parsed.get("type") != "message" or not parsed.get("text"):
        return {"ok": True}  # ignore non-message activities (typing, conversationUpdate, …)
    if not rate_limiter.allow(f"teams:{key}", rate=120, per=60):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded")
    result = await dispatch_message(run_service, tenant_id=ch.tenant_id, project_id=ch.project_id,
                                    workflow_id=workflow_id, text=parsed["text"],
                                    conversation_key=parsed.get("conversation_id"))
    ack = await _maybe_open_handoff(ch, result, customer=parsed.get("from_name"), customer_message=parsed["text"], reply_context=parsed)
    reply_text = ack or result.get("answer")
    if reply_text:
        try:
            await teams_ch.send_reply(ch, parsed, reply_text)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "handoff": bool(ack)}
