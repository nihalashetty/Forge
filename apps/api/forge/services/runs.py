"""RunService - create a run (thread + run rows) and stream its execution over SSE.

The architectural payoff: load the workflow's executable JSON, compile it to a
LangGraph graph (with the app checkpointer + a ForgeTracer), `astream` it, push
SSE frames, then persist the trace/spans and finalize the run row.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.engine.compiler import compile_workflow
from forge.models import Run, Thread, Trace, Workflow
from forge.services.runtime import build_compile_context
from forge.tracing.tracer import ForgeTracer
from forge.util.locks import ConcurrencyLimitExceeded, tenant_concurrency, thread_locks
from forge.util.serialize import content_to_text, jsonable, serialize_stream

log = logging.getLogger("forge.runs")


def _client_error(public: bool, run_id: str, detail: str) -> str:
    """On the public/embed surface, hide internal error detail from the browser end user
    (hostnames, secret-resolution failures, stack messages) and log it server-side keyed by
    run id; operators see the detail in the dashboard (audit S10)."""
    if public:
        log.error("run %s error (detail hidden from public client): %s", run_id, detail)
        return "Something went wrong while processing your request. Please try again."
    return detail


def _last_ai_text(values: dict) -> str:
    """Final assistant text from run state - the last AI/assistant message's content."""
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
        thread_id: str | None = None, end_user: dict | None = None,
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
            # Accept either handle we have ever handed a caller: the DB Thread.id (returned by
            # create + the `ready` SSE frame) OR the composite LangGraph id `{tenant}:{uuid}`
            # (once echoed in the `run` SSE frame). A bare uuid and a tenant-prefixed id never
            # collide, so matching both lets a caller continue a conversation whichever one it
            # kept. Without this a mismatched handle silently starts a FRESH thread every turn,
            # so the checkpointer holds no prior turns => the agent "forgets" the conversation.
            thread = (
                await session.execute(
                    select(Thread).where(
                        Thread.tenant_id == tenant_id, Thread.workflow_id == wf.id,
                        or_(Thread.id == thread_id, Thread.lg_thread_id == thread_id),
                    )
                )
            ).scalar_one_or_none()
            # Identity guard (audit S3): never let a caller attach to a thread that is bound
            # to a DIFFERENT end-user identity. Without this an anonymous embed caller who
            # learns a thread_id could continue a verified user's conversation and inherit
            # their bound identity/entitlements. On mismatch we start a fresh thread instead.
            if thread is not None:
                bound = (thread.meta or {}).get("end_user") or {}
                bound_id = str(bound.get("id") or "")
                incoming_id = str((end_user or {}).get("id") or "")
                if bound_id and bound_id != incoming_id:
                    log.warning(
                        "thread %s is bound to a different identity; starting a fresh thread",
                        thread_id,
                    )
                    thread = None
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

        # Bind the end user (identity) to the thread so the whole conversation acts for them.
        if end_user:
            thread.user_external_id = str(end_user.get("id") or "") or thread.user_external_id
            thread.meta = {**(thread.meta or {}), "end_user": end_user}

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

    async def stream(
        self, *, run_id: str, tenant_id: str, project_id: str | None = None, public: bool = False,
        run_context: dict | None = None, resume: bool = False, resume_value: Any = None,
    ) -> AsyncIterator[dict]:
        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                # Scope by project so a publishable embed key can't stream another
                # project's runs within the same tenant (audit S1).
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                yield {"event": "error", "data": {"message": "run not found"}}
                return
            # HITL resume: only an interrupted run can continue. Resuming a done/running run
            # would re-invoke a finished thread (undefined) or race a live one (mirrors resume()).
            if resume and run.status != "interrupted":
                yield {"event": "error", "data": {"message": f"run is not awaiting input (status={run.status})", "status": run.status}}
                return
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()

            node_ids = {n.get("id") for n in (wf.executable or {}).get("nodes", []) if isinstance(n, dict)}
            tracer = ForgeTracer()
            config: dict[str, Any] = {
                "configurable": {"thread_id": thread.lg_thread_id},
                "callbacks": [tracer],
            }
            # finalized => we reached a terminal state (done/interrupt/error) and persisted it,
            # so the `finally` must NOT also mark the run canceled. It stays False if the
            # client disconnects mid-stream (CancelledError/GeneratorExit propagate through).
            finalized = False
            # Serialize runs sharing this LangGraph thread so concurrent turns can't interleave
            # checkpoint writes (audit F7); bound concurrent runs per tenant (backpressure).
            tlock = await thread_locks.acquire_cm(thread.lg_thread_id)
            try:
                async with tenant_concurrency.slot(tenant_id, settings.max_concurrent_runs_per_tenant), tlock:
                    run.status = "running"
                    # A resumed run keeps its original start time; a fresh run stamps now.
                    if not resume:
                        run.started_at = datetime.utcnow()
                    await session.commit()

                    ctx = await build_compile_context(
                        session, tenant_id=tenant_id, project_id=run.project_id,
                        checkpointer=self.checkpointer, store=self.store,
                        end_user=(thread.meta or {}).get("end_user"),
                        run_context=run_context,
                    )
                    try:
                        graph = compile_workflow(wf.executable, ctx)
                    except Exception as e:  # noqa: BLE001 - compile failure -> error frame
                        log.warning("compile error for run %s: %s", run.id, e)
                        await self._finalize_error(session, run, tracer, f"compile error: {e}")
                        finalized = True
                        yield {"event": "error", "data": {"message": _client_error(public, run.id, f"compile error: {e}")}}
                        return

                    # Expose the caller-facing DB thread id here (same handle as the create
                    # response + `ready` frame) - NOT thread.lg_thread_id. The LangGraph id is
                    # an internal checkpointer key (and embeds the tenant id); a caller that
                    # echoed it back could never match Thread.id, so its conversation reset
                    # every turn. Keeping every thread_id in the stream identical avoids that.
                    yield {"event": "run", "data": {"run_id": run.id, "thread_id": run.thread_id}}

                    # subgraphs=True is required for token-by-token streaming: agent nodes
                    # compile to nested subgraphs, and without it the inner LLM tokens never
                    # reach this stream (the whole answer arrives as a single chunk). With it,
                    # each item is (namespace, mode, chunk); namespace () == the top-level graph.
                    # `tasks` (not `debug`) supplies node start/error events: start chunks
                    # carry `triggers`, finish chunks carry `error`/`result`.
                    # Resume a HITL interrupt with Command(resume=...); a fresh run feeds its input.
                    from langgraph.types import Command

                    driver = Command(resume=resume_value) if resume else run.input
                    async for ns, mode, chunk in graph.astream(
                        driver, config,
                        stream_mode=["tasks", "updates", "messages", "custom"],
                        subgraphs=True,
                        durability=settings.run_durability,
                    ):
                        if mode == "tasks" and isinstance(chunk, dict):
                            name = chunk.get("name")
                            if name in node_ids and "triggers" in chunk:
                                yield {"event": "node_start", "data": {"node": name}}
                            elif name in node_ids and chunk.get("error") is not None:
                                yield {"event": "node_error", "data": {"node": name, "message": _client_error(public, run.id, str(chunk.get("error")))}}
                            continue
                        # Only surface top-level node transitions as run steps; skip the
                        # subgraph-internal "model"/"tools" updates so the steps panel stays clean.
                        if mode == "updates" and ns:
                            continue
                        # In messages mode, only stream the agent's own tokens - never tool-result
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
                    await self._finalize(
                        session, run, tracer, snapshot,
                        status="interrupted" if interrupted else "done",
                    )
                    finalized = True

                    if interrupted:
                        payload = [
                            jsonable(getattr(t, "interrupts", None))
                            for t in getattr(snapshot, "tasks", [])
                            if getattr(t, "interrupts", None)
                        ]
                        yield {"event": "interrupt", "data": payload}
                    else:
                        # The authoritative final answer comes from run state - this covers
                        # answers produced by non-LLM nodes that never stream as message
                        # tokens, so the UI can always render a final bubble.
                        done_data: dict[str, Any] = {
                            "status": run.status,
                            "total_tokens": run.total_tokens,
                            "total_cost_usd": run.total_cost_usd,
                            "answer": _last_ai_text(getattr(snapshot, "values", {}) or {}),
                        }
                        # Run-step cost/debug is operator-only - never expose node names / cost
                        # to an anonymous embed end user (memory: widget-no-operator-data).
                        if not public:
                            done_data["debug"] = {"nodes": _debug_nodes(wf.executable or {}, tracer)}
                        yield {"event": "done", "data": done_data}
            except ConcurrencyLimitExceeded as e:
                # Slot was refused before the run started; it stays queued for the reaper.
                finalized = True
                yield {"event": "error", "data": {"message": e.message}}
            except Exception as e:  # noqa: BLE001 - runtime failure -> error frame + trace
                log.exception("run %s failed", run.id)
                await self._finalize_error(session, run, tracer, str(e))
                finalized = True
                yield {"event": "error", "data": {"message": _client_error(public, run.id, str(e))}}
            finally:
                # Client disconnect / cancellation propagates here without `finalized` set -
                # mark the otherwise-stranded run canceled in a fresh, shielded session so it
                # never sticks at status="running" forever (audit F1).
                if not finalized:
                    await self._mark_unfinished(run_id, tenant_id, status="canceled")

    async def run_to_completion(self, *, run_id: str, tenant_id: str, project_id: str | None = None, run_context: dict | None = None) -> dict:
        """Compile + run a created run to completion without SSE (used by the trigger
        dispatcher: webhook / schedule / email / chat). Returns the final answer."""
        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                return {"error": "run not found"}
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()

            # aget_state needs a checkpointer; fall back to an in-process saver if the
            # service wasn't given one (e.g. a context without the app lifespan).
            checkpointer = self.checkpointer
            if checkpointer is None:
                from langgraph.checkpoint.memory import InMemorySaver

                checkpointer = InMemorySaver()
            tracer = ForgeTracer()
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer]}
            tlock = await thread_locks.acquire_cm(thread.lg_thread_id)
            try:
                async with tenant_concurrency.slot(tenant_id, settings.max_concurrent_runs_per_tenant), tlock:
                    run.status = "running"
                    run.started_at = datetime.utcnow()
                    await session.commit()
                    ctx = await build_compile_context(
                        session, tenant_id=tenant_id, project_id=run.project_id,
                        checkpointer=checkpointer, store=self.store,
                        end_user=(thread.meta or {}).get("end_user"),
                        run_context=run_context,
                    )
                    graph = compile_workflow(wf.executable, ctx)
                    # Drive with the custom stream (not ainvoke) so `component` frames emitted by
                    # widget-tools are captured for channels that consume run_to_completion
                    # (webhook/schedule/email/Teams/evals) - ainvoke discards custom-stream items
                    # entirely (audit H1).
                    components: list = []
                    async for _ns, _mode, _chunk in graph.astream(
                        run.input, config, stream_mode=["custom"], subgraphs=True, durability=settings.run_durability,
                    ):
                        if _mode == "custom" and isinstance(_chunk, dict) and _chunk.get("channel") == "component":
                            components.append(_chunk.get("payload"))
                    snapshot = await graph.aget_state(config)
                    interrupted = bool(getattr(snapshot, "next", ())) and any(
                        getattr(t, "interrupts", None) for t in getattr(snapshot, "tasks", [])
                    )
                    await self._finalize(
                        session, run, tracer, snapshot,
                        status="interrupted" if interrupted else "done",
                    )
                interrupts = [
                    jsonable(getattr(t, "interrupts", None))
                    for t in getattr(snapshot, "tasks", [])
                    if getattr(t, "interrupts", None)
                ] if interrupted else []
                # Strip component-placement markers: this path feeds text-only channels
                # (email/Teams/webhook/schedule/evals) that can't render a widget and would
                # otherwise show the literal [[forge:component:…]] token. The structured
                # `components` list is still returned for richer consumers.
                from forge.tools.components import strip_component_markers

                answer = strip_component_markers(_last_ai_text(getattr(snapshot, "values", {}) or {}))
                # Text-only surfaces (email/Teams/webhook) can't render a widget - make sure a
                # component-only reply still sends something rather than an empty message (H1).
                if not answer.strip() and components:
                    names = ", ".join(str((c or {}).get("name") or "result") for c in components if c)
                    answer = f"Here is the requested information ({names})." if names else "Here is the requested information."
                return {
                    "run_id": run.id, "status": run.status, "interrupted": interrupted,
                    "interrupts": interrupts,
                    "answer": answer,
                    "components": components,
                    "total_tokens": run.total_tokens, "total_cost_usd": run.total_cost_usd,
                }
            except ConcurrencyLimitExceeded as e:
                return {"run_id": run.id, "status": "busy", "error": e.message, "answer": ""}
            except Exception as e:  # noqa: BLE001
                log.exception("run_to_completion %s failed", run.id)
                await self._finalize_error(session, run, tracer, str(e))
                # Error-workflow fallback: a graceful customer-facing reply + optional escalate.
                on_err = (wf.executable or {}).get("on_error") or {}
                fallback = on_err.get("message")
                return {
                    "run_id": run.id, "error": str(e), "status": "error",
                    "answer": fallback or "", "error_handled": bool(fallback),
                    "escalate": bool(on_err.get("escalate")),
                }

    async def resume(self, *, run_id: str, tenant_id: str, value, project_id: str | None = None, run_context: dict | None = None) -> dict:
        """Resume an interrupted run (HITL) with `Command(resume=value)` on its thread."""
        from langgraph.types import Command

        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                return {"error": "run not found"}
            # Only an interrupted run can be resumed. Resuming a done/running run would
            # re-invoke a completed thread (undefined result) or race a live run (F-low).
            if run.status != "interrupted":
                return {"error": f"run is not awaiting input (status={run.status})", "status": run.status}
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()

            tracer = ForgeTracer()
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer]}
            tlock = await thread_locks.acquire_cm(thread.lg_thread_id)
            try:
                async with tenant_concurrency.slot(tenant_id, settings.max_concurrent_runs_per_tenant), tlock:
                    run.status = "running"
                    await session.commit()
                    ctx = await build_compile_context(
                        session, tenant_id=tenant_id, project_id=run.project_id,
                        checkpointer=self.checkpointer, store=self.store,
                        end_user=(thread.meta or {}).get("end_user"),
                        run_context=run_context,
                    )
                    graph = compile_workflow(wf.executable, ctx)
                    out = await graph.ainvoke(Command(resume=value), config)
                    snapshot = await graph.aget_state(config)
                    interrupted = bool(getattr(snapshot, "next", ())) and any(
                        getattr(t, "interrupts", None) for t in getattr(snapshot, "tasks", [])
                    )
                    await self._finalize(
                        session, run, tracer, snapshot,
                        status="interrupted" if interrupted else "done",
                    )
                return {"status": run.status, "messages": jsonable((out or {}).get("messages", [])), "interrupted": interrupted}
            except ConcurrencyLimitExceeded as e:
                return {"error": e.message, "status": "busy"}
            except Exception as e:  # noqa: BLE001
                log.exception("resume %s failed", run.id)
                await self._finalize_error(session, run, tracer, str(e))
                return {"error": str(e)}

    async def _write_trace(self, session, run: Run, tracer: ForgeTracer, *, status: str):
        """Persist a Trace + its Span rows from the tracer. Shared by the success,
        interrupt, AND error paths so a failed run is observable too (audit F5).
        Returns (tokens, cost, spans)."""
        tokens, cost = tracer.totals()
        spans = tracer.ordered()
        started = min((s.start for s in spans), default=0.0)
        latency_ms = int((max((s.end or s.start for s in spans), default=started) - started) * 1000)

        # Identity audit: record which end user the run acted for (read from the thread).
        eu_thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one_or_none()
        eu = (eu_thread.meta or {}).get("end_user") if eu_thread else None
        trace = Trace(
            tenant_id=run.tenant_id,
            project_id=run.project_id,
            workflow_id=run.workflow_id,
            run_id=run.id,
            thread_id=run.thread_id,
            name="run",
            status=status,
            started_at=run.started_at,
            ended_at=datetime.utcnow(),
            latency_ms=latency_ms,
            total_tokens=tokens,
            total_cost_usd=cost,
            meta=({"end_user": eu} if eu else {}),
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
        return tokens, cost, spans

    async def _finalize(self, session, run: Run, tracer: ForgeTracer, snapshot, *, status: str) -> None:
        tokens, cost, spans = await self._write_trace(session, run, tracer, status=status)
        values = getattr(snapshot, "values", {}) or {}
        try:
            run.output = jsonable(values)
        except Exception:  # noqa: BLE001 - never let a non-serializable state strand the run
            log.exception("failed to serialize output for run %s", run.id)
            run.output = {}
        run.status = status
        run.total_tokens = tokens
        run.total_cost_usd = cost
        run.ended_at = datetime.utcnow()
        await session.commit()

        # Export to OpenTelemetry (no-op unless configured).
        from forge.tracing import otel

        otel.export(spans, trace_name="run")

    async def _finalize_error(self, session, run: Run, tracer: ForgeTracer, error: str, snapshot=None) -> None:
        """Error path: persist the partial trace/spans (so failures are observable, F5),
        mark the run errored, and never raise (a finalize failure must not mask the
        original error or strand the run)."""
        run.error = error
        try:
            tokens, cost, spans = await self._write_trace(session, run, tracer, status="error")
            run.total_tokens = tokens
            run.total_cost_usd = cost
            values = getattr(snapshot, "values", {}) or {}
            if values:
                try:
                    run.output = jsonable(values)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            log.exception("failed to persist error trace for run %s", run.id)
            spans = []
        run.status = "error"
        run.ended_at = datetime.utcnow()
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            log.exception("failed to commit error state for run %s", run.id)
        try:
            from forge.tracing import otel

            otel.export(spans or tracer.ordered(), trace_name="run")
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    async def _mark_unfinished(run_id: str, tenant_id: str, *, status: str, error: str | None = None) -> None:
        """Mark a run that never reached a terminal state (client disconnect / cancellation)
        so it can't stick at status='running' forever (audit F1). Uses a FRESH session and
        is shielded from the cancellation that triggered it so the write actually lands."""

        async def _do() -> None:
            async with SessionLocal() as s:
                run = (
                    await s.execute(select(Run).where(Run.tenant_id == tenant_id, Run.id == run_id))
                ).scalar_one_or_none()
                if run is not None and run.status in ("queued", "running"):
                    run.status = status
                    if error:
                        run.error = error
                    run.ended_at = datetime.utcnow()
                    await s.commit()

        try:
            await asyncio.shield(_do())
        except Exception:  # noqa: BLE001
            log.exception("failed to mark run %s as %s", run_id, status)

    @staticmethod
    async def reap_stale_runs(*, queued_max_age_s: int = 900, running_max_age_s: int = 3600) -> int:
        """Mark runs stuck in non-terminal states as error so they never linger forever
        (audit F3): a `queued` run that was created but never streamed/driven, or a
        `running` run whose driver died (crash, missed disconnect, killed worker). Called
        periodically from the app lifespan. Returns the number reaped."""
        now = datetime.utcnow()
        q_cut = now - timedelta(seconds=queued_max_age_s)
        r_cut = now - timedelta(seconds=running_max_age_s)
        reaped = 0
        async with SessionLocal() as s:
            rows = (
                await s.execute(select(Run).where(Run.status.in_(("queued", "running"))))
            ).scalars().all()
            for run in rows:
                if run.status == "queued" and (run.created_at or now) < q_cut:
                    run.status = "error"
                    run.error = "run expired before execution (never started)"
                    run.ended_at = now
                    reaped += 1
                elif run.status == "running" and (run.started_at or run.created_at or now) < r_cut:
                    run.status = "error"
                    run.error = "run exceeded maximum duration and was reaped"
                    run.ended_at = now
                    reaped += 1
            if reaped:
                await s.commit()
        if reaped:
            log.info("reaped %d stale run(s)", reaped)
        return reaped
