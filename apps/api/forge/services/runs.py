"""RunService — create a run (thread + run rows) and stream its execution over SSE.

The architectural payoff: load the workflow's executable JSON, compile it to a
LangGraph graph (with the app checkpointer + a ForgeTracer), `astream` it, push
SSE frames, then persist the trace/spans and finalize the run row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from sqlalchemy import select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.engine.compiler import compile_workflow
from forge.models import Run, Thread, Trace, Workflow
from forge.services.runtime import build_compile_context
from forge.tracing.tracer import ForgeTracer
from forge.util.serialize import content_to_text, jsonable, serialize_stream


def _last_ai_text(values: dict) -> str:
    """Final assistant text from run state — the last AI/assistant message's content."""
    msgs = (values or {}).get("messages") or []
    for m in reversed(msgs):
        mtype = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
        if mtype in ("ai", "assistant"):
            content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
            text = content_to_text(content)
            if text.strip():
                return text
    return ""


def _debug_nodes(definition: dict, tracer: ForgeTracer) -> dict[str, dict]:
    """Roll trace spans up to workflow node ids for one-time canvas debugging."""
    node_ids = {n.get("id") for n in (definition.get("nodes") or []) if isinstance(n, dict)}
    spans = tracer.ordered()
    by_id = {s.id: s for s in spans}
    out: dict[str, dict] = {}

    def owner(span) -> str | None:
        cur = span
        while cur is not None:
            if cur.name in node_ids:
                return cur.name
            cur = by_id.get(cur.parent_id or "")
        return None

    for span in spans:
        node_id = owner(span)
        if not node_id:
            continue
        bucket = out.setdefault(node_id, {"tokens": 0, "cost_usd": 0.0, "spans": []})
        tokens = (span.input_tokens or 0) + (span.output_tokens or 0)
        bucket["tokens"] += tokens
        bucket["cost_usd"] += span.cost_usd or 0.0
        if tokens or span.cost_usd:
            bucket["spans"].append({
                "name": span.name,
                "kind": span.kind,
                "model": span.model,
                "tokens": tokens,
                "cost_usd": round(span.cost_usd or 0.0, 6),
            })

    for value in out.values():
        value["cost_usd"] = round(value["cost_usd"], 6)
    return out


class RunService:
    def __init__(self, checkpointer: Any = None, store: Any = None) -> None:
        self.checkpointer = checkpointer
        self.store = store

    async def create_run(
        self, session, *, tenant_id: str, project_id: str, workflow_id: str, input: dict,
        thread_id: str | None = None,
    ) -> Run:
        wf = (
            await session.execute(
                select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.id == workflow_id)
            )
        ).scalar_one()

        # Reuse an existing thread when given: the checkpointer already holds the
        # conversation, so callers send ONLY the new message instead of replaying the
        # whole transcript each turn.
        thread = None
        if thread_id:
            thread = (
                await session.execute(
                    select(Thread).where(
                        Thread.tenant_id == tenant_id, Thread.id == thread_id,
                        Thread.workflow_id == wf.id,
                    )
                )
            ).scalar_one_or_none()
        if thread is None:
            thread = Thread(
                tenant_id=tenant_id,
                project_id=project_id,
                workflow_id=wf.id,
                lg_thread_id=f"{tenant_id}:{uuid.uuid4()}",
                title="Playground run",
            )
            session.add(thread)
            await session.flush()

        run = Run(
            tenant_id=tenant_id,
            project_id=project_id,
            workflow_id=wf.id,
            thread_id=thread.id,
            status="queued",
            input=input or {},
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    async def stream(self, *, run_id: str, tenant_id: str) -> AsyncIterator[dict]:
        async with SessionLocal() as session:
            run = (
                await session.execute(
                    select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id)
                )
            ).scalar_one_or_none()
            if run is None:
                yield {"event": "error", "data": {"message": "run not found"}}
                return
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()

            run.status = "running"
            run.started_at = datetime.utcnow()
            await session.commit()

            ctx = await build_compile_context(
                session, tenant_id=tenant_id, project_id=run.project_id,
                checkpointer=self.checkpointer, store=self.store,
            )
            node_ids = {n.get("id") for n in (wf.executable or {}).get("nodes", []) if isinstance(n, dict)}
            tracer = ForgeTracer()
            config = {
                "configurable": {"thread_id": thread.lg_thread_id},
                "callbacks": [tracer],
            }

            try:
                graph = compile_workflow(wf.executable, ctx)
            except Exception as e:  # noqa: BLE001 - compile failure -> error frame
                run.status = "error"
                run.error = f"compile error: {e}"
                run.ended_at = datetime.utcnow()
                await session.commit()
                yield {"event": "error", "data": {"message": run.error}}
                return

            yield {"event": "run", "data": {"run_id": run.id, "thread_id": thread.lg_thread_id}}

            try:
                # subgraphs=True is required for token-by-token streaming: agent nodes
                # compile to nested subgraphs, and without it the inner LLM tokens never
                # reach this stream (the whole answer arrives as a single chunk). With it,
                # each item is (namespace, mode, chunk); namespace () == the top-level graph.
                # `tasks` (not `debug`) supplies node start/error events: start chunks
                # carry `triggers`, finish chunks carry `error`/`result`.
                async for ns, mode, chunk in graph.astream(
                    run.input, config,
                    stream_mode=["tasks", "updates", "messages", "custom"],
                    subgraphs=True,
                    durability=settings.run_durability,
                ):
                    if mode == "tasks" and isinstance(chunk, dict):
                        name = chunk.get("name")
                        if name in node_ids and "triggers" in chunk:
                            yield {"event": "node_start", "data": {"node": name}}
                        elif name in node_ids and chunk.get("error") is not None:
                            yield {"event": "node_error", "data": {"node": name, "message": str(chunk.get("error"))}}
                        continue
                    # Only surface top-level node transitions as run steps; skip the
                    # subgraph-internal "model"/"tools" updates so the steps panel stays clean.
                    if mode == "updates" and ns:
                        continue
                    # In messages mode, only stream the agent's own tokens — never tool-result
                    # or human-message content (which would otherwise leak into the chat bubble).
                    if mode == "messages":
                        msg = chunk[0] if isinstance(chunk, (list, tuple)) and chunk else chunk
                        if getattr(msg, "type", "") not in ("ai", "AIMessageChunk"):
                            continue
                    yield {"event": mode, "data": serialize_stream(mode, chunk)}

                snapshot = await graph.aget_state(config)
                interrupted = bool(getattr(snapshot, "next", ())) and any(
                    getattr(t, "interrupts", None) for t in getattr(snapshot, "tasks", [])
                )
                await self._finalize(session, run, tracer, snapshot, interrupted)

                if interrupted:
                    payload = [
                        jsonable(getattr(t, "interrupts", None))
                        for t in getattr(snapshot, "tasks", [])
                        if getattr(t, "interrupts", None)
                    ]
                    yield {"event": "interrupt", "data": payload}
                else:
                    # The authoritative final answer comes from run state — this covers
                    # answers produced by non-LLM nodes (qa_lookup deflection) that never
                    # stream as message tokens, so the UI can always render a final bubble.
                    yield {
                        "event": "done",
                        "data": {
                            "status": run.status,
                            "total_tokens": run.total_tokens,
                            "total_cost_usd": run.total_cost_usd,
                            "answer": _last_ai_text(getattr(snapshot, "values", {}) or {}),
                            "debug": {"nodes": _debug_nodes(wf.executable or {}, tracer)},
                        },
                    }
            except Exception as e:  # noqa: BLE001 - runtime failure -> error frame
                run.status = "error"
                run.error = str(e)
                run.ended_at = datetime.utcnow()
                await session.commit()
                yield {"event": "error", "data": {"message": str(e)}}

    async def run_to_completion(self, *, run_id: str, tenant_id: str) -> dict:
        """Compile + run a created run to completion without SSE (used by the trigger
        dispatcher: webhook / schedule / email / chat). Returns the final answer."""
        async with SessionLocal() as session:
            run = (
                await session.execute(select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id))
            ).scalar_one_or_none()
            if run is None:
                return {"error": "run not found"}
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()
            run.status = "running"
            run.started_at = datetime.utcnow()
            await session.commit()

            # aget_state needs a checkpointer; fall back to an in-process saver if the
            # service wasn't given one (e.g. a context without the app lifespan).
            checkpointer = self.checkpointer
            if checkpointer is None:
                from langgraph.checkpoint.memory import InMemorySaver

                checkpointer = InMemorySaver()
            ctx = await build_compile_context(
                session, tenant_id=tenant_id, project_id=run.project_id,
                checkpointer=checkpointer, store=self.store,
            )
            tracer = ForgeTracer()
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer]}
            try:
                graph = compile_workflow(wf.executable, ctx)
                await graph.ainvoke(run.input, config, durability=settings.run_durability)
                snapshot = await graph.aget_state(config)
                interrupted = bool(getattr(snapshot, "next", ())) and any(
                    getattr(t, "interrupts", None) for t in getattr(snapshot, "tasks", [])
                )
                await self._finalize(session, run, tracer, snapshot, interrupted)
                interrupts = [
                    jsonable(getattr(t, "interrupts", None))
                    for t in getattr(snapshot, "tasks", [])
                    if getattr(t, "interrupts", None)
                ] if interrupted else []
                return {
                    "run_id": run.id, "status": run.status, "interrupted": interrupted,
                    "interrupts": interrupts,
                    "answer": _last_ai_text(getattr(snapshot, "values", {}) or {}),
                    "total_tokens": run.total_tokens, "total_cost_usd": run.total_cost_usd,
                }
            except Exception as e:  # noqa: BLE001
                run.status = "error"
                run.error = str(e)
                run.ended_at = datetime.utcnow()
                await session.commit()
                # Error-workflow fallback: a graceful customer-facing reply + optional escalate.
                on_err = (wf.executable or {}).get("on_error") or {}
                fallback = on_err.get("message")
                return {
                    "run_id": run.id, "error": str(e), "status": "error",
                    "answer": fallback or "", "error_handled": bool(fallback),
                    "escalate": bool(on_err.get("escalate")),
                }

    async def resume(self, *, run_id: str, tenant_id: str, value) -> dict:
        """Resume an interrupted run (HITL) with `Command(resume=value)` on its thread."""
        from langgraph.types import Command

        async with SessionLocal() as session:
            run = (await session.execute(select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id))).scalar_one_or_none()
            if run is None:
                return {"error": "run not found"}
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()
            run.status = "running"
            await session.commit()

            ctx = await build_compile_context(
                session, tenant_id=tenant_id, project_id=run.project_id,
                checkpointer=self.checkpointer, store=self.store,
            )
            tracer = ForgeTracer()
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer]}
            try:
                graph = compile_workflow(wf.executable, ctx)
                out = await graph.ainvoke(Command(resume=value), config)
                snapshot = await graph.aget_state(config)
                interrupted = bool(getattr(snapshot, "next", ())) and any(
                    getattr(t, "interrupts", None) for t in getattr(snapshot, "tasks", [])
                )
                await self._finalize(session, run, tracer, snapshot, interrupted)
                return {"status": run.status, "messages": jsonable((out or {}).get("messages", [])), "interrupted": interrupted}
            except Exception as e:  # noqa: BLE001
                run.status = "error"
                run.error = str(e)
                run.ended_at = datetime.utcnow()
                await session.commit()
                return {"error": str(e)}

    async def _finalize(self, session, run: Run, tracer: ForgeTracer, snapshot, interrupted: bool) -> None:
        tokens, cost = tracer.totals()
        spans = tracer.ordered()
        started = min((s.start for s in spans), default=0.0)
        latency_ms = int((max((s.end or s.start for s in spans), default=started) - started) * 1000)

        trace = Trace(
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            workflow_id=run.workflow_id,
            run_id=run.id,
            thread_id=run.thread_id,
            name="run",
            status="interrupted" if interrupted else "done",
            started_at=run.started_at,
            ended_at=datetime.utcnow(),
            latency_ms=latency_ms,
            total_tokens=tokens,
            total_cost_usd=cost,
        )
        session.add(trace)
        await session.flush()

        from forge.models import Span

        for sr in spans:
            session.add(
                Span(
                    id=sr.id,
                    tenant_id=run.tenant_id,
                    trace_id=trace.id,
                    parent_span_id=sr.parent_id,
                    name=sr.name,
                    kind=sr.kind,
                    latency_ms=sr.latency_ms,
                    model=sr.model,
                    input_tokens=sr.input_tokens,
                    output_tokens=sr.output_tokens,
                    cost_usd=sr.cost_usd,
                    error=sr.error,
                    attributes=sr.attributes,
                )
            )

        values = getattr(snapshot, "values", {}) or {}
        run.output = jsonable(values)
        run.status = "interrupted" if interrupted else "done"
        run.total_tokens = tokens
        run.total_cost_usd = cost
        run.ended_at = datetime.utcnow()
        await session.commit()

        # Export to OpenTelemetry (no-op unless configured).
        from forge.tracing import otel

        otel.export(spans, trace_name="run")
