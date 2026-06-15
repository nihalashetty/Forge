"""Forge Assistant endpoint — streams the meta-agent's narration + actions over SSE."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from forge.deps import current_tenant_id, get_checkpointer
from forge.services.assistant import AssistantService

router = APIRouter(prefix="/v1/projects/{project_id}/assistant", tags=["assistant"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


class AssistantMessage(BaseModel):
    role: str = "user"
    content: str


class AssistantIn(BaseModel):
    # Preferred: one new message + a stable thread_id (the checkpointer holds history,
    # todos, and files for the thread). `messages` remains for back-compat — when
    # thread_id is absent the full transcript is replayed statelessly.
    message: str | None = None
    thread_id: str | None = None
    messages: list[AssistantMessage] = []


class AssistantResumeIn(BaseModel):
    thread_id: str
    decision: str = "approve"  # approve | reject


@router.post("/stream")
async def assistant_stream(
    project_id: str,
    body: AssistantIn,
    tenant_id: str = Depends(current_tenant_id),
    checkpointer=Depends(get_checkpointer),
):
    if body.message:
        messages = [{"role": "user", "content": body.message}]
    else:
        messages = [m.model_dump() for m in body.messages]

    async def event_gen():
        async for frame in AssistantService.stream(
            tenant_id=tenant_id, project_id=project_id, messages=messages,
            thread_id=body.thread_id, checkpointer=checkpointer,
        ):
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

    return EventSourceResponse(event_gen(), headers=SSE_HEADERS)


@router.post("/resume")
async def assistant_resume(
    project_id: str,
    body: AssistantResumeIn,
    tenant_id: str = Depends(current_tenant_id),
    checkpointer=Depends(get_checkpointer),
):
    """Resume a paused assistant thread (HITL approval for destructive tools)."""

    async def event_gen():
        async for frame in AssistantService.resume(
            tenant_id=tenant_id, project_id=project_id, thread_id=body.thread_id,
            decision=body.decision, checkpointer=checkpointer,
        ):
            yield {"event": frame["event"], "data": json.dumps(frame["data"], default=str)}

    return EventSourceResponse(event_gen(), headers=SSE_HEADERS)
