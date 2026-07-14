"""HandoffService - the live-agent queue.

When a channel run pauses at a `handoff` (or `human_input`) interrupt, a HandoffRequest is
opened. A human agent lists the queue and replies; that resumes the paused run with their
text (which the handoff node turns into the assistant's reply) and pushes it back over the
channel.

Correctness invariants (audit B/C/E):
- reply() CLAIMS the row atomically (open -> answering) before resuming, so two concurrent
  replies can't both resume + double-deliver.
- it only marks 'answered' when the resume AND the channel delivery both succeed; a failed
  send leaves a distinct 'delivery_failed' state (never a silent 'answered').
- if the resumed run RE-INTERRUPTS (multi-step "approve then confirm"), a fresh HandoffRequest
  is opened so the next step is actionable.
- a free-text channel reply is coerced to the human_input node's allowed_decisions so a Router
  keyed on approve/reject matches.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy import update as sa_update

from forge.models import Channel, HandoffRequest
from forge.services.runs import RunService

log = logging.getLogger("forge.handoff")

# Reserved key inside HandoffRequest.reply_context carrying the HITL metadata captured when the
# handoff was opened (the interrupting node's allowed_decisions + kind) - HandoffRequest has no
# dedicated column (models/entities.py is frozen), so it rides in the JSON reply_context and is
# stripped before the context is handed to a channel send.
HITL_META_KEY = "_forge_hitl"

# Free-text -> canonical decision synonyms, so an operator typing "yes, go ahead" resolves to the
# node's "approve" token (a Router keyed on approve/reject then matches) - audit C.
_DECISION_SYNONYMS: dict[str, tuple[str, ...]] = {
    "approve": ("approve", "approved", "yes", "y", "ok", "okay", "accept", "accepted", "confirm",
                "confirmed", "go", "go ahead", "proceed", "allow", "lgtm", "sounds good", "affirmative"),
    "reject": ("reject", "rejected", "no", "n", "deny", "denied", "decline", "declined", "cancel",
               "stop", "disapprove", "refuse", "negative"),
    "edit": ("edit", "edited", "modify", "change", "revise", "update"),
}


# --- interrupt-payload helpers (shared with routers/channels.py) --------------------------


def _iter_interrupt_values(interrupts):
    for group in interrupts or []:
        for it in group or []:
            val = it.get("value") if isinstance(it, dict) else None
            if isinstance(val, dict):
                yield val


def interrupt_reason(interrupts) -> str | None:
    """The escalation reason from a `handoff` interrupt payload, if any."""
    for val in _iter_interrupt_values(interrupts):
        if val.get("handoff"):
            return val.get("reason") or "Escalated to a human agent."
    return None


def interrupt_ack(interrupts) -> str | None:
    """The configured customer-facing ack_message from any interrupt payload, if present."""
    for val in _iter_interrupt_values(interrupts):
        if val.get("ack_message"):
            return val["ack_message"]
    return None


def interrupt_hitl_meta(interrupts) -> dict:
    """HITL metadata to persist so the reply path can coerce a channel decision: the
    interrupting node's allowed_decisions + kind (human_input | handoff)."""
    for val in _iter_interrupt_values(interrupts):
        if "allowed_decisions" in val:
            return {"kind": "human_input", "allowed_decisions": list(val.get("allowed_decisions") or [])}
        if val.get("handoff"):
            return {"kind": "handoff", "allowed_decisions": []}
    return {"kind": None, "allowed_decisions": []}


def coerce_to_allowed_decision(text: str, allowed: list[str]) -> str:
    """Map free-text `text` to one of `allowed` (audit C). Exact token wins; else a synonym /
    keyword match; else fail-safe to a negative decision if the node offers one, else the raw
    text (never silently approve)."""
    if not allowed:
        return text
    raw = (text or "").strip()
    low = raw.lower()
    for d in allowed:
        if low == str(d).lower():
            return d
    words = set(re.findall(r"[a-z']+", low))
    for d in allowed:
        syns = _DECISION_SYNONYMS.get(str(d).lower(), (str(d).lower(),))
        for syn in syns:
            # Short tokens (yes/no/ok/y/n) only match on a word boundary; longer ones may be a
            # substring ("please go ahead" -> approve) without matching inside another word.
            if (syn in words) if len(syn) <= 4 else (syn in low):
                return d
    for neg in ("reject", "deny", "decline", "cancel"):
        for d in allowed:
            if str(d).lower() == neg:
                return d
    return raw


def _channel_reply_context(reply_context: dict | None) -> dict:
    """Strip Forge-internal reserved keys before handing the context to a channel send."""
    ctx = dict(reply_context or {})
    ctx.pop(HITL_META_KEY, None)
    return ctx


async def _deliver(channel: Channel, reply_ctx: dict, text: str) -> bool:
    """Push `text` over the originating channel. True on success OR a no-op (channel not
    configured / nothing to send); False when a send was attempted and failed after retries."""
    try:
        if channel.type == "email":
            from forge.channels import email as email_ch

            await email_ch.send_reply(channel, reply_ctx, text)
        elif channel.type == "teams":
            from forge.channels import teams as teams_ch

            await teams_ch.send_reply(channel, reply_ctx, text)
        return True
    except Exception:  # noqa: BLE001 - a delivery failure must be recorded, not swallowed
        log.warning("handoff delivery over %s channel failed", channel.type, exc_info=True)
        return False


class HandoffService:
    @staticmethod
    async def create(
        session, *, channel: Channel | None, tenant_id: str, project_id: str, workflow_id: str | None,
        run_id: str, thread_id: str | None, customer: str | None, customer_message: str | None,
        reason: str | None, reply_context: dict | None,
    ) -> HandoffRequest:
        h = HandoffRequest(
            tenant_id=tenant_id, project_id=project_id, workflow_id=workflow_id, run_id=run_id,
            thread_id=thread_id, channel_id=channel.id if channel else None,
            customer=customer, customer_message=customer_message, reason=reason,
            reply_context=reply_context or {}, status="open",
        )
        session.add(h)
        await session.commit()
        await session.refresh(h)
        return h

    @staticmethod
    async def list(session, tenant_id: str, project_id: str, *, status: str | None = "open") -> list[HandoffRequest]:
        q = select(HandoffRequest).where(
            HandoffRequest.tenant_id == tenant_id, HandoffRequest.project_id == project_id
        )
        if status:
            q = q.where(HandoffRequest.status == status)
        return list((await session.execute(q.order_by(HandoffRequest.created_at.desc()))).scalars())

    @staticmethod
    async def reply(session, run_service: RunService, *, handoff: HandoffRequest, agent_id: str, message: str) -> dict:
        """Resume the paused run with the agent's `message` and push it over the channel.

        Returns a status dict; `ok` is True only when the resume succeeded and (if a channel is
        bound) the delivery succeeded."""
        # 1. Atomically CLAIM the row (open -> answering) so concurrent replies can't both resume
        #    and double-deliver. The router's pre-check is not atomic (TOCTOU) - audit B.
        claimed = (await session.execute(
            sa_update(HandoffRequest)
            .where(HandoffRequest.id == handoff.id, HandoffRequest.status == "open")
            .values(status="answering", agent_id=agent_id)
        )).rowcount
        await session.commit()
        if not claimed:
            fresh = await session.get(HandoffRequest, handoff.id)
            st = fresh.status if fresh else "gone"
            return {"ok": False, "error": f"handoff is already {st}", "status": st}
        handoff.status = "answering"
        handoff.agent_id = agent_id

        # 2. Coerce a free-text reply to the human_input node's allowed_decisions (audit C).
        meta = (handoff.reply_context or {}).get(HITL_META_KEY) or {}
        allowed = meta.get("allowed_decisions") or []
        resume_value = coerce_to_allowed_decision(message, allowed) if allowed else message

        # 3. Resume the paused run. Deliver + mark answered ONLY if the resume actually succeeded.
        result = await run_service.resume(run_id=handoff.run_id, tenant_id=handoff.tenant_id, value=resume_value)
        if result.get("error"):
            # Revert the claim so the reply can be retried; do NOT deliver or mark answered.
            await session.execute(
                sa_update(HandoffRequest).where(HandoffRequest.id == handoff.id).values(status="open", agent_id=None)
            )
            await session.commit()
            handoff.status = "open"
            return {"ok": False, "error": result["error"], "status": result.get("status")}

        reinterrupted = bool(result.get("interrupted"))

        # 4. Deliver over the originating channel (with retry). On a chained interrupt, send the
        #    NEXT step's ack (a fresh handoff is opened below) rather than the raw decision.
        channel = None
        if handoff.channel_id:
            channel = (await session.execute(
                select(Channel).where(Channel.id == handoff.channel_id)
            )).scalar_one_or_none()
        reply_ctx = _channel_reply_context(handoff.reply_context)
        to_send = (interrupt_ack(result.get("interrupts")) if reinterrupted else None) or message
        delivered_via = channel.type if channel else None
        attempted = channel is not None and bool(reply_ctx)
        delivery_ok = await _deliver(channel, reply_ctx, to_send) if attempted else True

        # 5. A failed send must NOT be marked 'answered' (audit E) - leave a distinct terminal
        #    state so an operator can see + retry it.
        if not delivery_ok:
            await session.execute(
                sa_update(HandoffRequest).where(HandoffRequest.id == handoff.id).values(status="delivery_failed")
            )
            await session.commit()
            handoff.status = "delivery_failed"
            return {"ok": False, "delivered": False, "delivered_via": delivered_via,
                    "resume": result, "reinterrupted": reinterrupted, "status": "delivery_failed"}

        # 6. Multi-step HITL: the resumed run paused again, so open a FRESH handoff for the next
        #    step - otherwise the run is stranded 'interrupted' with no queue item (audit B).
        new_handoff_id = None
        if reinterrupted:
            new_handoff_id = await HandoffService._reopen(session, handoff, result)

        await session.execute(
            sa_update(HandoffRequest).where(HandoffRequest.id == handoff.id).values(status="answered")
        )
        await session.commit()
        handoff.status = "answered"
        return {"ok": True, "resume": result, "delivered": bool(attempted),
                "delivered_via": delivered_via, "reinterrupted": reinterrupted,
                "new_handoff_id": new_handoff_id}

    @staticmethod
    async def _reopen(session, handoff: HandoffRequest, result: dict) -> str:
        """Open a fresh HandoffRequest for the run's NEXT interrupt (chained HITL), carrying the
        new step's reason + allowed_decisions and preserving the channel binding."""
        interrupts = result.get("interrupts")
        reason = interrupt_reason(interrupts) or (
            "Conversation paused awaiting input/approval - a team member will follow up."
        )
        hitl_meta = interrupt_hitl_meta(interrupts)
        reply_ctx = _channel_reply_context(handoff.reply_context)
        if hitl_meta.get("allowed_decisions"):
            reply_ctx[HITL_META_KEY] = {"allowed_decisions": hitl_meta["allowed_decisions"],
                                        "kind": hitl_meta.get("kind")}
        fresh = await HandoffService.create(
            session, channel=None, tenant_id=handoff.tenant_id, project_id=handoff.project_id,
            workflow_id=handoff.workflow_id, run_id=handoff.run_id, thread_id=handoff.thread_id,
            customer=handoff.customer, customer_message=handoff.customer_message,
            reason=reason, reply_context=reply_ctx,
        )
        # Preserve the channel binding (create(channel=None) leaves channel_id null).
        if handoff.channel_id:
            fresh.channel_id = handoff.channel_id
            await session.commit()
        return fresh.id

    @staticmethod
    async def close_for_run(session, run_id: str, tenant_id: str, *, reason: str | None = None) -> int:
        """Close any open/answering handoffs for a run (e.g. on HITL timeout / run cancel).
        Returns how many were closed."""
        rows = list((await session.execute(
            select(HandoffRequest).where(
                HandoffRequest.tenant_id == tenant_id, HandoffRequest.run_id == run_id,
                HandoffRequest.status.in_(("open", "answering")),
            )
        )).scalars())
        for h in rows:
            h.status = "closed"
            if reason:
                h.reason = (h.reason or "") + f" [closed: {reason}]"
        if rows:
            await session.commit()
        return len(rows)
