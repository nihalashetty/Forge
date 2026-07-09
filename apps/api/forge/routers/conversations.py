"""Conversation-centric Traces endpoints.

A conversation = one chat session (Thread); each turn is a Trace carrying the user
message, the AI response, and the actor (user name / "System"). This powers the
Traces screen: sessions grouped by end user, their turns, and the filter facets.
The per-turn span waterfall is still served by GET .../traces/{trace_id}.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import current_tenant_id, get_session, require_role
from forge.schemas.dto import ConversationDetailOut, ConversationOut, FacetsOut, TurnOut
from forge.services.conversations import ConversationService, summarize

router = APIRouter(prefix="/v1/projects/{project_id}/conversations", tags=["conversations"])


def _turn(t) -> TurnOut:
    return TurnOut(
        trace_id=t.id, run_id=t.run_id, source=t.source or "", user_message=t.user_message,
        ai_response=t.ai_response, status=t.status, error=t.error, latency_ms=t.latency_ms,
        total_tokens=t.total_tokens, total_cost_usd=t.total_cost_usd, started_at=t.started_at,
    )


@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    project_id: str,
    actor: str | None = Query(None, description="Filter by user name (e.g. 'System', 'Unknown user')"),
    source: str | None = Query(None, description="Filter by origin (playground|api|embed|channel_*|…)"),
    status: str | None = Query(None, description="'error' or 'success'/'done'"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    convos = await ConversationService.list(
        session, tenant_id, project_id, actor=actor, source=source, status=status, limit=limit, offset=offset,
    )
    return [ConversationOut.model_validate(c) for c in convos]


@router.get("/facets", response_model=FacetsOut)
async def conversation_facets(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    return await ConversationService.facets(session, tenant_id, project_id)


@router.post("/purge")
async def purge_conversations(
    project_id: str,
    older_than_days: int = Query(..., ge=0, description="Delete traces + spans older than this many days"),
    session: AsyncSession = Depends(get_session),
    _admin=Depends(require_role("admin")),
    tenant_id: str = Depends(current_tenant_id),
):
    """Manual retention control (admin only). Nothing is auto-deleted."""
    removed = await ConversationService.purge_older_than(session, tenant_id, project_id, older_than_days)
    return {"removed": removed}


@router.get("/{thread_id}", response_model=ConversationDetailOut)
async def get_conversation(
    project_id: str,
    thread_id: str,
    session: AsyncSession = Depends(get_session),
    tenant_id: str = Depends(current_tenant_id),
):
    turns = await ConversationService.turns(session, tenant_id, project_id, thread_id)
    if not turns:
        raise HTTPException(404, "Conversation not found")
    return {"conversation": summarize(thread_id, turns), "turns": [_turn(t) for t in turns]}
