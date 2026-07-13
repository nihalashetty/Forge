"""Evaluation datasets: CRUD + run (quality + regression)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from forge.deps import CurrentUser, current_tenant_id, get_run_service, get_session, require_role
from forge.services.evals import EvalService
from forge.services.runs import RunService

router = APIRouter(prefix="/v1/projects/{project_id}/datasets", tags=["evals"])


class DatasetIn(BaseModel):
    name: str
    workflow_id: str | None = None
    score_mode: str = "contains"  # contains|exact|regex|judge
    items: list[dict] = []


def _out(d) -> dict:
    return {"id": d.id, "name": d.name, "workflow_id": d.workflow_id, "score_mode": d.score_mode,
            "items": d.items, "n_items": len(d.items or []), "last_pass_rate": d.last_pass_rate}


@router.get("")
async def list_datasets(project_id: str, session: AsyncSession = Depends(get_session),
                        tenant_id: str = Depends(current_tenant_id)):
    return [_out(d) for d in await EvalService.list(session, tenant_id, project_id)]


@router.post("", status_code=201)
async def create_dataset(project_id: str, body: DatasetIn, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    ds = await EvalService.create(session, tenant_id, project_id, name=body.name, workflow_id=body.workflow_id,
                                  score_mode=body.score_mode, items=body.items)
    return _out(ds)


@router.patch("/{dataset_id}")
async def update_dataset(project_id: str, dataset_id: str, body: DatasetIn, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    ds = await EvalService.get(session, tenant_id, dataset_id)
    if not ds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    ds = await EvalService.update(session, ds, name=body.name, workflow_id=body.workflow_id,
                                  score_mode=body.score_mode, items=body.items)
    return _out(ds)


@router.delete("/{dataset_id}")
async def delete_dataset(project_id: str, dataset_id: str, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    ds = await EvalService.get(session, tenant_id, dataset_id)
    if not ds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    await EvalService.delete(session, ds)
    return {"ok": True}


@router.post("/{dataset_id}/run")
async def run_dataset(project_id: str, dataset_id: str, session: AsyncSession = Depends(get_session),
                      tenant_id: str = Depends(current_tenant_id),
                      _: CurrentUser = Depends(require_role("editor")),
                      run_service: RunService = Depends(get_run_service)):
    ds = await EvalService.get(session, tenant_id, dataset_id)
    if not ds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    judge_model = None
    if ds.score_mode == "judge":
        from forge.engine.models import resolve_model
        from forge.services.runtime import build_compile_context
        ctx = await build_compile_context(session, tenant_id=tenant_id, project_id=project_id)
        judge_model = resolve_model(ctx.default_model, ctx)
    return await EvalService.run(session, run_service, ds, judge_model=judge_model)
