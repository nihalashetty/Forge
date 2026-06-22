"""HandoffService - the live-agent queue.

When a channel run pauses at a `handoff` interrupt, a HandoffRequest is opened. A human
agent lists the queue and replies; that resumes the paused run with their text (which the
handoff node turns into the assistant's reply) and pushes it back over the channel.
"""

from __future__ import annotations

from sqlalchemy import select

from forge.models import Channel, HandoffRequest
from forge.services.runs import RunService


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
        """Resume the paused run with the agent's `message` and push it over the channel."""
        result = await run_service.resume(run_id=handoff.run_id, tenant_id=handoff.tenant_id, value=message)
        # Deliver over the originating channel (email/teams).
        channel = None
        if handoff.channel_id:
            channel = (await session.execute(
                select(Channel).where(Channel.id == handoff.channel_id)
            )).scalar_one_or_none()
        if channel is not None and handoff.reply_context:
            try:
                if channel.type == "email":
                    from forge.channels import email as email_ch
                    await email_ch.send_reply(channel, handoff.reply_context, message)
                elif channel.type == "teams":
                    from forge.channels import teams as teams_ch
                    await teams_ch.send_reply(channel, handoff.reply_context, message)
            except Exception:  # noqa: BLE001 - delivery failure shouldn't lose the reply record
                pass
        handoff.status = "answered"
        handoff.agent_id = agent_id
        await session.commit()
        return {"ok": True, "resume": result, "delivered_via": channel.type if channel else None}
