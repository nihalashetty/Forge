"""EvalService — run a dataset against a workflow and score it.

Scoring modes:
- contains / exact / regex — deterministic, offline, no model spend.
- judge — an LLM grades whether the answer satisfies the expected behavior.

Used for quality dashboards and regression-on-publish (compare pass rate vs the last
run). Generalizes the assistant's one-off `evaluate_build` into a reusable harness.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import select

from forge.models import Dataset
from forge.services.dispatch import dispatch_message
from forge.services.quota import QuotaExceeded, check_run_quota
from forge.services.runs import RunService

log = logging.getLogger("forge.evals")

# A single eval run can fire one billable model run per item; cap it so a huge dataset
# can't kick off thousands of runs from one request (audit F11).
_MAX_EVAL_ITEMS = 1000

_JUDGE_SCHEMA = {
    "title": "ItemJudgement",
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["passed"],
}


def _score_deterministic(mode: str, answer: str, expected: str) -> bool:
    a, e = (answer or "").strip(), (expected or "").strip()
    if mode == "exact":
        return a == e
    if mode == "regex":
        try:
            return re.search(expected or "", answer or "") is not None
        except re.error:
            return False
    # default: contains (case-insensitive)
    return e.lower() in a.lower()


class EvalService:
    @staticmethod
    async def list(session, tenant_id: str, project_id: str) -> list[Dataset]:
        rows = await session.execute(
            select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.project_id == project_id)
        )
        return list(rows.scalars())

    @staticmethod
    async def get(session, tenant_id: str, dataset_id: str) -> Dataset | None:
        return (await session.execute(
            select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.id == dataset_id)
        )).scalar_one_or_none()

    @staticmethod
    async def create(session, tenant_id, project_id, *, name, workflow_id=None, score_mode="contains", items=None) -> Dataset:
        ds = Dataset(tenant_id=tenant_id, project_id=project_id, name=name, workflow_id=workflow_id,
                     score_mode=score_mode, items=items or [])
        session.add(ds)
        await session.commit()
        await session.refresh(ds)
        return ds

    @staticmethod
    async def delete(session, ds: Dataset) -> None:
        await session.delete(ds)
        await session.commit()

    @staticmethod
    async def run(session, run_service: RunService, dataset: Dataset, *, judge_model=None) -> dict:
        """Execute every item against the dataset's workflow and score it."""
        workflow_id = dataset.workflow_id
        if not workflow_id:
            return {"error": "dataset has no workflow bound"}
        # Eval runs are billable model calls — gate the whole batch on the tenant's daily quota.
        try:
            await check_run_quota(session, dataset.tenant_id)
        except QuotaExceeded as e:
            return {"error": e.message}
        all_items = dataset.items or []
        items = all_items[:_MAX_EVAL_ITEMS]
        truncated = len(all_items) > _MAX_EVAL_ITEMS
        results: list[dict] = []
        passed = 0
        for item in items:
            inp = item.get("input", "")
            expected = item.get("expected", "")
            # Isolate each item: one failing run must not abort the whole eval (audit F11).
            try:
                res = await dispatch_message(
                    run_service, tenant_id=dataset.tenant_id, project_id=dataset.project_id,
                    workflow_id=workflow_id, text=inp,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("eval item failed for dataset %s: %s", dataset.id, e)
                results.append({"input": inp, "expected": expected, "answer": "", "passed": False, "reason": f"run failed: {e}"})
                continue
            answer = res.get("answer", "") or ""
            if res.get("error"):
                ok, reason = False, res["error"]
            elif dataset.score_mode == "judge":
                ok, reason = await EvalService._judge(judge_model, inp, expected, answer)
            else:
                ok, reason = _score_deterministic(dataset.score_mode, answer, expected), None
            passed += int(ok)
            results.append({"input": inp, "expected": expected, "answer": answer, "passed": ok, "reason": reason})
        total = len(items)
        rate = (passed / total) if total else 0.0
        dataset.last_pass_rate = rate
        await session.commit()
        summary = {"total": total, "passed": passed, "pass_rate": round(rate, 4)}
        if truncated:
            summary["truncated"] = True
            summary["items_skipped"] = len(all_items) - _MAX_EVAL_ITEMS
        return {"summary": summary, "results": results}

    @staticmethod
    async def _judge(model, inp: str, expected: str, answer: str) -> tuple[bool, str | None]:
        if model is None:
            return (expected.strip().lower() in (answer or "").lower(), "judge model unavailable; fell back to contains")
        prompt = (
            "You are grading an AI assistant's answer. Does the ANSWER satisfy the EXPECTED behavior "
            "for the INPUT? Reply pass/fail.\n"
            f"INPUT: {inp}\nEXPECTED: {expected}\nANSWER: {answer}"
        )
        try:
            res = await model.with_structured_output(_JUDGE_SCHEMA).ainvoke(prompt)
            passed = bool(res.get("passed") if isinstance(res, dict) else getattr(res, "passed", False))
            reason = res.get("reason") if isinstance(res, dict) else getattr(res, "reason", None)
            return passed, reason
        except Exception as e:  # noqa: BLE001
            return (expected.strip().lower() in (answer or "").lower(), f"judge error: {e}")
