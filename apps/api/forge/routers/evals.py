"""Evaluation datasets: CRUD + run (quality + regression) + persisted run history."""

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
    score_mode: str = "contains"  # contains|exact|regex|numeric|json|embedding|judge
    items: list[dict] = []


class RunIn(BaseModel):
    # Publish-time regression gate (finding F2): flag/block when the pass rate drops below the
    # dataset's previous rate, or below an absolute floor. Off by default (a plain quality run).
    regression_gate: bool = False
    min_pass_rate: float | None = None


def _out(d) -> dict:
    return {"id": d.id, "name": d.name, "workflow_id": d.workflow_id, "score_mode": d.score_mode,
            "items": d.items, "n_items": len(d.items or []), "last_pass_rate": d.last_pass_rate}


def _run_out(r) -> dict:
    return {"id": r.id, "created_at": r.created_at, "dataset_id": r.dataset_id, "workflow_id": r.workflow_id,
            "score_mode": r.score_mode, "status": r.status, "total": r.total, "passed": r.passed,
            "pass_rate": r.pass_rate, "prev_pass_rate": r.prev_pass_rate, "regressed": r.regressed,
            "total_tokens": r.total_tokens, "total_cost_usd": r.total_cost_usd, "meta": r.meta}


def _result_out(r) -> dict:
    return {"id": r.id, "item_index": r.item_index, "input": r.input, "expected": r.expected,
            "answer": r.answer, "passed": r.passed, "score": r.score, "status": r.status,
            "reason": r.reason, "checks": r.checks}


def _needs_judge(ds) -> bool:
    """A judge model is needed if the dataset scores by judge, or any item has a judge assertion."""
    if ds.score_mode == "judge":
        return True
    return any((a.get("type") or "").lower() == "judge"
               for item in (ds.items or []) for a in (item.get("assertions") or []))


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


@router.get("/{dataset_id}/runs")
async def list_eval_runs(project_id: str, dataset_id: str, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id)):
    """Persisted eval-run history for a dataset (newest first) - quality trend + regression view."""
    return [_run_out(r) for r in await EvalService.history(session, tenant_id, dataset_id)]


@router.get("/{dataset_id}/runs/{eval_run_id}/results")
async def list_eval_results(project_id: str, dataset_id: str, eval_run_id: str,
                            session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id)):
    """Per-item outcomes for one persisted eval run (for per-example diffing)."""
    return [_result_out(r) for r in await EvalService.results(session, tenant_id, eval_run_id)]


@router.post("/{dataset_id}/run")
async def run_dataset(project_id: str, dataset_id: str, body: RunIn | None = None,
                      session: AsyncSession = Depends(get_session),
                      tenant_id: str = Depends(current_tenant_id),
                      _: CurrentUser = Depends(require_role("editor")),
                      run_service: RunService = Depends(get_run_service)):
    ds = await EvalService.get(session, tenant_id, dataset_id)
    if not ds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "dataset not found")
    body = body or RunIn()
    judge_model = None
    if _needs_judge(ds):
        from forge.engine.models import resolve_model
        from forge.services.runtime import build_compile_context
        ctx = await build_compile_context(session, tenant_id=tenant_id, project_id=project_id)
        # resolve_model returns the offline fake model when the project has no real model;
        # EvalService then reports judge items as "unavailable" rather than grading against it.
        judge_model = resolve_model(ctx.default_model, ctx)
    return await EvalService.run(session, run_service, ds, judge_model=judge_model,
                                 regression_gate=body.regression_gate, min_pass_rate=body.min_pass_rate)
