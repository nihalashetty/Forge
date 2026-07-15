"""RunService - create a run (thread + run rows) and stream its execution over SSE.

The architectural payoff: load the workflow's executable JSON, compile it to a
LangGraph graph (with the app checkpointer + a ForgeTracer), `astream` it, push
SSE frames, then persist the trace/spans and finalize the run row.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select

from forge.config import settings
from forge.db.base import SessionLocal
from forge.db.scoping import set_current_tenant
from forge.engine.compiler import compile_workflow
from forge.models import Run, Thread, Trace, Workflow
from forge.services.runtime import build_compile_context
from forge.tracing.tracer import ForgeTracer
from forge.util.locks import ConcurrencyLimitExceeded, tenant_concurrency, thread_locks
from forge.util.serialize import (
    content_to_text,
    jsonable,
    reset_tool_display_names,
    serialize_stream,
    set_tool_display_names,
)

log = logging.getLogger("forge.runs")

# Settings wanted (config.py is owned by a parallel agent; module constants used for now):
#   FORGE_HITL_APPROVAL_TIMEOUT_SECONDS (int, default 0 = never expire) - how long an
#     interrupted run may wait for a human approval before the reaper fails it + closes the
#     handoff with a fallback channel message (audit C).
#   FORGE_RUN_WALL_CLOCK_TIMEOUT_SECONDS (int, default 0 = unlimited) - hard per-run wall-clock
#     budget for graph execution, checked cooperatively between stream frames (audit H).
HITL_APPROVAL_TIMEOUT_SECONDS = settings.hitl_approval_timeout_seconds
RUN_WALL_CLOCK_TIMEOUT_SECONDS = settings.run_wall_clock_timeout_seconds


class _RunControl:
    """Cooperative + cross-worker cancellation registry (audit H).

    A live run loop calls begin()/end() to register itself; cancel_run() signals it via an Event
    the loop polls between frames AND (best-effort) cancels the driving asyncio task so a run
    wedged inside one long frame (e.g. a hung model call) is still interrupted. Entries exist only
    while a loop is running, so nothing leaks. Each active loop also gets a lightweight watcher
    for its shared DB row: if another worker commits ``status=canceled``, the watcher hard-cancels
    this worker's driving task even when it is blocked inside one long model/tool await."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._watchers: dict[str, asyncio.Task] = {}

    def begin(self, run_id: str, tenant_id: str) -> asyncio.Event:
        ev = asyncio.Event()
        self._events[run_id] = ev
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            self._tasks[run_id] = task
        self._watchers[run_id] = asyncio.create_task(
            self._watch_database(run_id, tenant_id), name=f"forge-cancel-watch:{run_id}",
        )
        return ev

    async def end(self, run_id: str) -> None:
        self._events.pop(run_id, None)
        self._tasks.pop(run_id, None)
        watcher = self._watchers.pop(run_id, None)
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

    async def _watch_database(self, run_id: str, tenant_id: str) -> None:
        """Observe cancellation committed by another API/worker process.

        The run table is already the authoritative cross-worker state and exists in every
        deployment, unlike optional Redis. Polling only while a run is active also gives a hard
        cancellation backstop for a graph currently awaiting a slow provider call.
        """
        while run_id in self._events:
            await asyncio.sleep(0.25)
            try:
                set_current_tenant(tenant_id)
                async with SessionLocal() as session:
                    status = (
                        await session.execute(
                            select(Run.status).where(Run.id == run_id, Run.tenant_id == tenant_id)
                        )
                    ).scalar_one_or_none()
                if status == "canceled":
                    self.request_cancel(run_id, hard=True)
                    return
                if status is None or status in ("done", "error"):
                    return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transient DB errors must not kill execution
                log.debug("run cancel watcher failed for %s", run_id, exc_info=True)

    def is_running(self, run_id: str) -> bool:
        return run_id in self._events

    def request_cancel(self, run_id: str, *, hard: bool = True) -> bool:
        """Signal a running loop to stop. Returns True if a live loop was signaled (else the run
        isn't running in THIS process - the caller should fall back to the DB status)."""
        ev = self._events.get(run_id)
        signaled = ev is not None
        if ev is not None:
            ev.set()
        if hard:
            t = self._tasks.get(run_id)
            if t is not None and not t.done():
                t.cancel()
                signaled = True
        return signaled


run_control = _RunControl()


# --- Durable SSE: decouple run execution from the client connection (finding #12) ----------
# A run's graph is driven by a BACKGROUND task that publishes frames to a per-run broker; the
# SSE endpoints merely SUBSCRIBE. So a client disconnect ends only the subscription - the run
# keeps executing and persisting via the checkpointer, and a client can reattach (same run_id)
# to replay missed frames via Last-Event-ID + follow the rest. In-process (single worker); a
# multi-worker deployment additionally leans on the DB run status + the stale-run reaper.

RUN_BROKER_TTL_SECONDS = 300  # retain a finished run's frames this long for late reconnects


class _RunBroker:
    """Ordered, replayable, multi-subscriber event buffer for one run episode. Frames get a
    monotonic seq (the SSE event id); subscribers replay from a Last-Event-ID then follow live."""

    def __init__(self) -> None:
        self.seq = 0
        self.events: list[tuple[int, dict]] = []
        self.done = asyncio.Event()
        self._subs: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def publish(self, frame: dict) -> None:
        async with self._lock:
            self.seq += 1
            item = (self.seq, frame)
            self.events.append(item)
            for q in self._subs:
                q.put_nowait(item)

    async def finish(self) -> None:
        async with self._lock:
            if self.done.is_set():
                return
            self.done.set()
            for q in self._subs:
                q.put_nowait((None, None))  # unblock live subscribers

    async def subscribe(self, last_event_id: int = 0) -> AsyncIterator[tuple[int, dict]]:
        """Replay buffered frames with seq > last_event_id, then follow live frames until finish.
        The queue is registered BEFORE the buffer snapshot so a frame published in the gap is
        queued (never lost); `last_yielded` dedups the buffer/queue overlap."""
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subs.add(q)
            buffered = list(self.events)
            finished = self.done.is_set()
        last_yielded = last_event_id
        try:
            for seq, frame in buffered:
                if seq > last_yielded:
                    yield seq, frame
                    last_yielded = seq
            if finished:
                return
            while True:
                seq, frame = await q.get()
                if seq is None:  # finish sentinel
                    return
                if seq > last_yielded:
                    yield seq, frame
                    last_yielded = seq
        finally:
            async with self._lock:
                self._subs.discard(q)


class _RunStreams:
    """Process-wide registry of run brokers + their detached executor tasks."""

    def __init__(self) -> None:
        self._brokers: dict[str, _RunBroker] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    def get(self, run_id: str) -> _RunBroker | None:
        return self._brokers.get(run_id)

    async def ensure(self, run_id: str, *, start: bool, factory) -> tuple[_RunBroker | None, bool]:
        """Return (broker, started_now). If a LIVE (unfinished) executor already owns this run,
        reattach to it. Else if `start`, launch a fresh executor+broker episode (initial run or a
        resume). Else return the finished broker (for replay) or None (nothing in this process)."""
        async with self._lock:
            existing = self._brokers.get(run_id)
            if existing is not None and not existing.done.is_set():
                return existing, False
            if not start:
                return existing, False
            broker = _RunBroker()
            self._brokers[run_id] = broker
            task = asyncio.create_task(factory(broker))
            self._tasks[run_id] = task
            task.add_done_callback(lambda _t, rid=run_id, b=broker: self._on_done(rid, b))
            return broker, True

    def _on_done(self, run_id: str, broker: _RunBroker) -> None:
        # Drop the task ref; GC this exact broker after a grace window (identity-checked so a
        # later episode's broker under the same run_id is never evicted by this timer).
        self._tasks.pop(run_id, None)

        def _gc() -> None:
            if self._brokers.get(run_id) is broker:
                self._brokers.pop(run_id, None)

        try:
            asyncio.get_running_loop().call_later(RUN_BROKER_TTL_SECONDS, _gc)
        except RuntimeError:  # no running loop (shouldn't happen from a task callback)
            _gc()


run_streams = _RunStreams()


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
    return _last_message_text(values, ("ai", "assistant"))


def _last_user_text(values: dict) -> str:
    """This turn's user message - the last human/user message. Reads `Run.input`, whose
    messages are plain dicts keyed by `role` (from the request body); also tolerates the
    jsonable `type` key used in persisted state."""
    return _last_message_text(values, ("human", "user"))


def _last_message_text(values: dict, kinds: tuple[str, ...]) -> str:
    msgs = (values or {}).get("messages") or []
    for m in reversed(msgs):
        # Live BaseMessage -> `.type`; a request-body / jsonable dict -> `role` or `type`.
        mtype = getattr(m, "type", None) or (m.get("role") or m.get("type") if isinstance(m, dict) else None)
        if mtype in kinds:
            content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
            text = content_to_text(content)
            if text.strip():
                return text
    return ""


# Sources that are Forge-internal (operator-driven), shown as "System" in the Traces view.
_SYSTEM_SOURCES = ("playground", "test", "assistant")


def _actor_label(source: str, end_user: dict | None) -> str:
    """The user-facing name a conversation is grouped/filtered by: 'System' for internal runs,
    else the end user's name (display_name -> email -> id), else 'Unknown user'."""
    if source in _SYSTEM_SOURCES:
        return "System"
    eu = end_user or {}
    return eu.get("display_name") or eu.get("email") or eu.get("id") or "Unknown user"


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


def _internal_message_nodes(nodes: list) -> set[str]:
    """Ids of nodes whose streamed model tokens are INTERNAL, not the chat answer.

    A classifier emits a routing label and a structured `llm` emits a structured_response -
    neither enters the `messages` state, yet both still stream tokens over the messages
    channel and would otherwise land in the answer bubble. Answer-producing nodes
    (agent/deep_agent/retrieval/unstructured llm) are absent, so they stream normally.
    Filtering by node id keeps this backend-owned - clients don't guess from node names.
    """
    out: set[str] = set()
    for n in nodes or []:
        if not isinstance(n, dict) or not n.get("id"):
            continue  # a null id would match tokens with no `langgraph_node` and drop answers
        ntype = n.get("type")
        structured_llm = ntype == "llm" and ((n.get("config") or {}).get("response_format") or {}).get("mode") == "structured"
        if ntype in ("classifier", "router", "start", "end") or structured_llm:
            out.add(n["id"])
    return out


def _recursion_limit(executable: dict | None) -> int:
    """LangGraph superstep budget for a run. LangGraph defaults to 25, which a Loop node
    (each iteration spends ~3 supersteps: loop -> router -> body -> back) exceeds after only
    a few iterations, raising GraphRecursionError mid-run - so the Loop node effectively broke
    past ~8 iterations on every non-assistant run path. Derive a budget from graph size plus
    each loop's max_iter, floored by settings.graph_recursion_limit, so large workflows scale
    automatically and small ones keep safe headroom."""
    ex = executable or {}
    nodes = [n for n in (ex.get("nodes") or []) if isinstance(n, dict)]
    loop_iters = sum(
        int((n.get("config") or {}).get("max_iter", 10) or 10)
        for n in nodes
        if n.get("type") == "loop"
    )
    computed = (len(nodes) + 1) * 2 + loop_iters * 3 + 10
    return max(int(settings.graph_recursion_limit or 0), computed)


class RunService:
    def __init__(self, checkpointer: Any = None, store: Any = None) -> None:
        self.checkpointer = checkpointer
        self.store = store

    async def create_run(
        self, session, *, tenant_id: str, project_id: str, workflow_id: str, input: dict,
        thread_id: str | None = None, end_user: dict | None = None, source: str = "playground",
    ) -> Run:
        set_current_tenant(tenant_id)  # bind tenant for the Postgres RLS GUC (no-op on SQLite)
        # Enforce the tenant's daily spend ceiling on EVERY run-creation path - webhook / email /
        # Email / app_event / MCP / playground - not just the embed widget (audit M2). create_run
        # is the single run factory, so this is the universal floor. The embed path additionally
        # wraps this in run_admission for atomic burst-safety. No-op unless a quota is configured.
        from forge.services.quota import check_run_quota
        await check_run_quota(session, tenant_id)
        # Project governance: monthly USD cap + allowed_models allow-list (no-op unless the
        # project configures them). Raises BudgetExceeded / ModelNotAllowed, mapped to HTTP by
        # the admission routers. Model is validated per-node at publish; admission checks spend.
        from forge.services.budget import enforce_project_budget
        await enforce_project_budget(session, tenant_id, project_id)

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
            source=source,
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run

    async def stream(
        self, *, run_id: str, tenant_id: str, project_id: str | None = None, public: bool = False,
        run_context: dict | None = None, resume: bool = False, resume_value: Any = None,
        last_event_id: int = 0,
    ) -> AsyncIterator[dict]:
        """SSE SUBSCRIBER: ensure a detached executor is driving this run, then relay its frames
        (each tagged with a monotonic `id`). A client disconnect ends only this subscription -
        the executor keeps running (finding #12). Reconnecting with the same run_id + a
        Last-Event-ID replays missed frames then follows the rest; a run that finished during the
        gap is reconstructed from the persisted trace so the caller still gets the final answer."""
        set_current_tenant(tenant_id)  # bind tenant for the Postgres RLS GUC (no-op on SQLite)
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
            status = run.status

        # A fresh streaming episode starts on a queued run (initial) or a resume (interrupted ->
        # running); otherwise we only reattach. ensure() serializes so a double-connect can't
        # start two executors - the second reattaches to the first's broker.
        start = resume or status == "queued"
        broker, _started = await run_streams.ensure(
            run_id, start=start,
            factory=lambda b: self._execute(
                run_id=run_id, tenant_id=tenant_id, project_id=project_id, public=public,
                run_context=run_context, resume=resume, resume_value=resume_value, broker=b,
            ),
        )
        if broker is not None:
            async for seq, frame in broker.subscribe(last_event_id):
                yield {"id": str(seq), "event": frame["event"], "data": frame["data"]}
            return
        # No broker in this process (terminal run past its retention window, or a run whose
        # executor lived in a since-gone process): rebuild the terminal frame from the DB.
        async for frame in self._reattach_from_db(run_id, tenant_id, project_id, public):
            yield frame

    async def _reattach_from_db(
        self, run_id: str, tenant_id: str, project_id: str | None, public: bool,
    ) -> AsyncIterator[dict]:
        """Fallback reattach: synthesize a single terminal frame from the persisted run + its
        latest trace, so a late reconnect (after the in-memory broker was GC'd) still resolves
        with the final answer instead of hanging."""
        set_current_tenant(tenant_id)
        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                yield {"event": "error", "data": {"message": "run not found"}}
                return
            trace = (await session.execute(
                select(Trace).where(Trace.run_id == run_id, Trace.tenant_id == tenant_id)
                .order_by(Trace.created_at.desc())
            )).scalars().first()
            answer = (trace.ai_response if trace else "") or ""
        if run.status == "done":
            data: dict[str, Any] = {"status": "done", "answer": answer}
            if not public:
                data["total_tokens"] = run.total_tokens
                data["total_cost_usd"] = run.total_cost_usd
            yield {"event": "done", "data": data}
        elif run.status == "canceled":
            yield {"event": "done", "data": {"status": "canceled", "answer": answer, "canceled": True}}
        elif run.status == "error":
            yield {"event": "error", "data": {"message": _client_error(public, run_id, run.error or "run failed")}}
        elif run.status == "interrupted":
            # The interrupt payload isn't replayable from the DB; signal that input is still awaited.
            yield {"event": "interrupt", "data": []}
        else:
            yield {"event": "error", "data": {"message": "run is not streaming on this server", "status": run.status}}

    async def _execute(
        self, *, run_id: str, tenant_id: str, project_id: str | None, public: bool,
        run_context: dict | None, resume: bool, resume_value: Any, broker: _RunBroker,
    ) -> None:
        """Detached executor entrypoint. Drives the episode, then FINISHES the broker only after
        the DB session has fully closed - so a subscriber that returns on the finish sentinel can
        never race the session teardown (avoids cross-loop close errors under the test harness)."""
        try:
            await self._drive(
                run_id=run_id, tenant_id=tenant_id, project_id=project_id, public=public,
                run_context=run_context, resume=resume, resume_value=resume_value, broker=broker,
            )
        finally:
            await broker.finish()

    async def _drive(
        self, *, run_id: str, tenant_id: str, project_id: str | None, public: bool,
        run_context: dict | None, resume: bool, resume_value: Any, broker: _RunBroker,
    ) -> None:
        """Drive the run's graph to a terminal state, PUBLISHING each frame to `broker` (rather
        than yielding). Runs (via `_execute`) as a detached background task so a client disconnect
        never stops it; only a hard cancel, a real error, or normal completion ends it."""
        set_current_tenant(tenant_id)  # bind tenant for the Postgres RLS GUC (no-op on SQLite)
        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                await broker.publish({"event": "error", "data": {"message": "run not found"}})
                return
            wf = (await session.execute(select(Workflow).where(Workflow.id == run.workflow_id))).scalar_one()
            thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one()

            wf_nodes = (wf.executable or {}).get("nodes", [])
            node_ids = {n.get("id") for n in wf_nodes if isinstance(n, dict)}
            # Node ids whose streamed model tokens are internal (routing label / structured
            # response) and must not reach the chat bubble - suppressed below in messages mode.
            suppressed_message_nodes = _internal_message_nodes(wf_nodes)
            tracer = ForgeTracer()
            config: dict[str, Any] = {
                "configurable": {"thread_id": thread.lg_thread_id},
                "callbacks": [tracer],
                "recursion_limit": _recursion_limit(wf.executable),
            }
            # finalized => we reached a terminal state (done/interrupt/error) and persisted it,
            # so the `finally` must NOT also mark the run canceled. It stays False if the
            # client disconnects mid-stream (CancelledError/GeneratorExit propagate through).
            finalized = False
            # Bind the tool name->label map for this stream so serialized tool_calls carry a
            # human-readable display_name (reset in `finally`); set after ctx is built below.
            display_token = None
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
                    display_token = set_tool_display_names(ctx.tool_display_names)
                    try:
                        graph = compile_workflow(wf.executable, ctx)
                    except Exception as e:  # noqa: BLE001 - compile failure -> error frame
                        log.warning("compile error for run %s: %s", run.id, e)
                        await self._finalize_error(session, run, tracer, f"compile error: {e}")
                        finalized = True
                        await broker.publish({"event": "error", "data": {"message": _client_error(public, run.id, f"compile error: {e}")}})
                        return

                    # Expose the caller-facing DB thread id here (same handle as the create
                    # response + `ready` frame) - NOT thread.lg_thread_id. The LangGraph id is
                    # an internal checkpointer key (and embeds the tenant id); a caller that
                    # echoed it back could never match Thread.id, so its conversation reset
                    # every turn. Keeping every thread_id in the stream identical avoids that.
                    await broker.publish({"event": "run", "data": {"run_id": run.id, "thread_id": run.thread_id}})

                    # subgraphs=True is required for token-by-token streaming: agent nodes
                    # compile to nested subgraphs, and without it the inner LLM tokens never
                    # reach this stream (the whole answer arrives as a single chunk). With it,
                    # each item is (namespace, mode, chunk); namespace () == the top-level graph.
                    # `tasks` (not `debug`) supplies node start/error events: start chunks
                    # carry `triggers`, finish chunks carry `error`/`result`.
                    # Resume a HITL interrupt with Command(resume=...); a fresh run feeds its input.
                    from langgraph.types import Command

                    # Cooperative cancellation + wall-clock timeout (audit H): register this loop
                    # so a cancel request can signal it, and poll the flag / deadline between
                    # frames. A hard asyncio task-cancel (from request_cancel) is the backstop for
                    # a run wedged inside a single long frame - it propagates to the `finally`,
                    # which marks the run canceled.
                    cancel_ev = run_control.begin(run.id, tenant_id)
                    wall_clock = RUN_WALL_CLOCK_TIMEOUT_SECONDS
                    started_monotonic = time.monotonic()
                    canceled = timed_out = False
                    driver = Command(resume=resume_value) if resume else run.input
                    async for ns, mode, chunk in graph.astream(
                        driver, config,
                        stream_mode=["tasks", "updates", "messages", "custom"],
                        subgraphs=True,
                        durability=settings.run_durability,
                    ):
                        if cancel_ev.is_set():
                            canceled = True
                            break
                        if wall_clock and (time.monotonic() - started_monotonic) > wall_clock:
                            timed_out = True
                            break
                        if mode == "tasks" and isinstance(chunk, dict):
                            # node_start / node_error expose internal workflow node names (and error
                            # detail) - operator-only, never to an anonymous embed end user (H5).
                            if public:
                                continue
                            name = chunk.get("name")
                            if name in node_ids and "triggers" in chunk:
                                await broker.publish({"event": "node_start", "data": {"node": name}})
                            elif name in node_ids and chunk.get("error") is not None:
                                await broker.publish({"event": "node_error", "data": {"node": name, "message": _client_error(public, run.id, str(chunk.get("error")))}})
                            continue
                        # "updates" frames carry internal node names + intermediate node state
                        # (retrieved knowledge, tool results); never send them to the public embed
                        # surface (H5). For operators, still skip subgraph-internal updates.
                        if mode == "updates" and (public or ns):
                            continue
                        # In messages mode, only stream the agent's own answer tokens - never
                        # tool-result / human-message content, nor a classifier/structured node's
                        # internal tokens (both would otherwise leak into the chat bubble).
                        if mode == "messages":
                            msg = chunk[0] if isinstance(chunk, (list, tuple)) and chunk else chunk
                            if getattr(msg, "type", "") not in ("ai", "AIMessageChunk"):
                                continue
                            meta = chunk[1] if isinstance(chunk, (list, tuple)) and len(chunk) == 2 else {}
                            if (meta or {}).get("langgraph_node") in suppressed_message_nodes:
                                continue
                        data = serialize_stream(mode, chunk)
                        # The internal node name rides along on message frames; strip it on the
                        # public embed surface so node topology isn't exposed to end users (H5).
                        if public and mode == "messages" and isinstance(data, dict):
                            data.pop("node", None)
                        await broker.publish({"event": mode, "data": data})

                    snapshot = await graph.aget_state(config)
                    # Cooperative stop hit between frames: finalize with the right terminal state
                    # and emit a closing frame, then stop (the tenant slot frees as we exit).
                    if canceled or timed_out:
                        if canceled:
                            await self._finalize(session, run, tracer, snapshot, status="canceled")
                            finalized = True
                            await broker.publish({"event": "done", "data": {
                                "status": "canceled",
                                "answer": _last_ai_text(getattr(snapshot, "values", {}) or {}),
                                "canceled": True,
                            }})
                        else:
                            detail = f"run exceeded the wall-clock timeout ({wall_clock}s)"
                            await self._finalize_error(session, run, tracer, detail, snapshot=snapshot)
                            finalized = True
                            await broker.publish({"event": "error", "data": {"message": _client_error(public, run.id, detail)}})
                        return
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
                        await broker.publish({"event": "interrupt", "data": payload})
                    else:
                        # The authoritative final answer comes from run state - this covers
                        # answers produced by non-LLM nodes that never stream as message
                        # tokens, so the UI can always render a final bubble.
                        done_data: dict[str, Any] = {
                            "status": run.status,
                            "answer": _last_ai_text(getattr(snapshot, "values", {}) or {}),
                        }
                        # Token counts, cost, run-step debug and node names are operator-only - never
                        # expose them to an anonymous embed end user (audit H5 / memory:
                        # widget-no-operator-data). Operators see all of it in the dashboard.
                        if not public:
                            done_data["total_tokens"] = run.total_tokens
                            done_data["total_cost_usd"] = run.total_cost_usd
                            done_data["debug"] = {"nodes": _debug_nodes(wf.executable or {}, tracer)}
                        await broker.publish({"event": "done", "data": done_data})
            except ConcurrencyLimitExceeded as e:
                # Slot was refused before the run started; it stays queued for the reaper.
                finalized = True
                await broker.publish({"event": "error", "data": {"message": e.message}})
            except Exception as e:  # noqa: BLE001 - runtime failure -> error frame + trace
                log.exception("run %s failed", run.id)
                await self._finalize_error(session, run, tracer, str(e))
                finalized = True
                # Graceful fallback: the workflow's on_error.message reached ONLY the text-channel
                # path (run_to_completion) before; the streaming surface (Playground / embed / API)
                # got a raw error. Deliver the configured fallback here too so end users on the
                # surfaces they actually see get the operator's message instead of a stack error.
                on_err = (wf.executable or {}).get("on_error") or {}
                fallback = on_err.get("message")
                if fallback:
                    done_err: dict[str, Any] = {"status": "error", "answer": fallback, "error_handled": True}
                    if not public:
                        done_err["escalate"] = bool(on_err.get("escalate"))
                    await broker.publish({"event": "done", "data": done_err})
                else:
                    await broker.publish({"event": "error", "data": {"message": _client_error(public, run.id, str(e))}})
            finally:
                await run_control.end(run_id)
                if display_token is not None:
                    reset_tool_display_names(display_token)
                # A HARD task-cancel (operator cancel of a run wedged in one long frame) lands here
                # with finalized=False - mark the otherwise-stranded run canceled in a fresh,
                # shielded session so it never sticks at status="running" (audit F1). NOTE: a mere
                # client disconnect no longer reaches here (the executor is detached from the SSE
                # subscription - finding #12), so a disconnect leaves the run RUNNING. The broker
                # is finished by `_execute` AFTER this session closes.
                if not finalized:
                    await self._mark_unfinished(run_id, tenant_id, status="canceled")

    async def run_to_completion(self, *, run_id: str, tenant_id: str, project_id: str | None = None, run_context: dict | None = None) -> dict:
        """Compile + run a created run to completion without SSE (used by the trigger
        dispatcher: webhook / schedule / email / chat). Returns the final answer."""
        set_current_tenant(tenant_id)  # bind tenant for the Postgres RLS GUC (no-op on SQLite)
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
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer], "recursion_limit": _recursion_limit(wf.executable)}
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
                    # (webhook/schedule/email/evals) - ainvoke discards custom-stream items
                    # entirely (audit H1).
                    components: list = []
                    # Cooperative cancel + wall-clock timeout on the non-SSE path too (audit H).
                    cancel_ev = run_control.begin(run.id, tenant_id)
                    wall_clock = RUN_WALL_CLOCK_TIMEOUT_SECONDS
                    started_monotonic = time.monotonic()
                    canceled = timed_out = False
                    async for _ns, _mode, _chunk in graph.astream(
                        run.input, config, stream_mode=["custom"], subgraphs=True, durability=settings.run_durability,
                    ):
                        if cancel_ev.is_set():
                            canceled = True
                            break
                        if wall_clock and (time.monotonic() - started_monotonic) > wall_clock:
                            timed_out = True
                            break
                        if _mode == "custom" and isinstance(_chunk, dict) and _chunk.get("channel") == "component":
                            components.append(_chunk.get("payload"))
                    snapshot = await graph.aget_state(config)
                    if canceled or timed_out:
                        detail = "run canceled by operator" if canceled else f"run exceeded the wall-clock timeout ({wall_clock}s)"
                        if canceled:
                            await self._finalize(session, run, tracer, snapshot, status="canceled")
                        else:
                            await self._finalize_error(session, run, tracer, detail, snapshot=snapshot)
                        return {"run_id": run.id, "status": run.status, "interrupted": False,
                                "answer": "", "components": components,
                                "canceled": canceled, "error": None if canceled else detail}
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
                # (email/webhook/schedule/evals) that can't render a widget and would
                # otherwise show the literal [[forge:component:…]] token. The structured
                # `components` list is still returned for richer consumers.
                from forge.tools.components import strip_component_markers

                answer = strip_component_markers(_last_ai_text(getattr(snapshot, "values", {}) or {}))
                # Text-only surfaces (email/webhook) can't render a widget - make sure a
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
            finally:
                await run_control.end(run_id)

    async def resume(self, *, run_id: str, tenant_id: str, value, project_id: str | None = None, run_context: dict | None = None, public: bool = False) -> dict:
        """Resume an interrupted run (HITL) with `Command(resume=value)` on its thread."""
        from langgraph.types import Command

        set_current_tenant(tenant_id)  # bind tenant for the Postgres RLS GUC (no-op on SQLite)
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
            config = {"configurable": {"thread_id": thread.lg_thread_id}, "callbacks": [tracer], "recursion_limit": _recursion_limit(wf.executable)}
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
                # Interrupt payloads for a RE-interrupt (chained HITL), so the handoff reply path
                # can open a fresh handoff for the next step + surface the next ack (audit B).
                interrupts = [
                    jsonable(getattr(t, "interrupts", None))
                    for t in getattr(snapshot, "tasks", [])
                    if getattr(t, "interrupts", None)
                ] if interrupted else []
                if public:
                    # Public embed: expose ONLY the final assistant message, not the full
                    # transcript (which includes tool-result / internal messages) (audit H5).
                    # Keep the single-element `messages` shape the widget already consumes.
                    answer = _last_ai_text(out or {})
                    pub_msgs = [{"type": "ai", "content": answer}] if answer else []
                    return {"status": run.status, "messages": pub_msgs, "interrupted": interrupted, "interrupts": interrupts}
                return {"status": run.status, "messages": jsonable((out or {}).get("messages", [])), "interrupted": interrupted, "interrupts": interrupts}
            except ConcurrencyLimitExceeded as e:
                return {"error": e.message, "status": "busy"}
            except Exception as e:  # noqa: BLE001
                log.exception("resume %s failed", run.id)
                await self._finalize_error(session, run, tracer, str(e))
                # Redact internal error detail on the public embed surface (audit M6); the stream
                # path already does this via _client_error, the resume path did not.
                return {"error": _client_error(public, run.id, str(e))}

    async def cancel_run(self, *, run_id: str, tenant_id: str, project_id: str | None = None) -> dict:
        """Cooperatively cancel a run (audit H): mark it canceled, signal any live loop in this
        process to stop (+ hard task-cancel backstop), free the tenant-concurrency slot (it frees
        as the signaled loop exits), and close any open handoff. A terminal run is a no-op."""
        set_current_tenant(tenant_id)
        async with SessionLocal() as session:
            where = [Run.tenant_id == tenant_id, Run.id == run_id]
            if project_id is not None:
                where.append(Run.project_id == project_id)
            run = (await session.execute(select(Run).where(*where))).scalar_one_or_none()
            if run is None:
                return {"ok": False, "error": "run not found", "status": None}
            if run.status in ("done", "error", "canceled"):
                return {"ok": False, "status": run.status, "detail": f"run already {run.status}"}
            prev = run.status
            # Signal a live loop first so its own finalize converges on 'canceled'. For a queued /
            # interrupted run (no live loop) the DB write below is the authoritative stop.
            signaled = run_control.request_cancel(run_id, hard=True)
            run.status = "canceled"
            run.error = run.error or "run canceled by operator"
            run.ended_at = datetime.utcnow()
            await session.commit()
            # Close any open handoff for this run (an interrupted run may have one waiting).
            try:
                from forge.services.handoff import HandoffService

                await HandoffService.close_for_run(session, run_id, tenant_id, reason="run canceled")
            except Exception:  # noqa: BLE001 - handoff close is best-effort
                log.debug("cancel_run: handoff close failed for %s", run_id, exc_info=True)
            return {"ok": True, "status": "canceled", "previous_status": prev, "signaled": signaled}

    async def _write_trace(self, session, run: Run, tracer: ForgeTracer, *, status: str, values: dict | None = None):
        """Persist a Trace + its Span rows from the tracer. Shared by the success,
        interrupt, AND error paths so a failed run is observable too (audit F5).
        `values` is the final LangGraph state (for the AI-response transcript).
        Returns (tokens, cost, spans)."""
        tokens, cost = tracer.totals()
        spans = tracer.ordered()
        started = min((s.start for s in spans), default=0.0)
        latency_ms = int((max((s.end or s.start for s in spans), default=started) - started) * 1000)

        # Identity audit: record which end user the run acted for (read from the thread).
        eu_thread = (await session.execute(select(Thread).where(Thread.id == run.thread_id))).scalar_one_or_none()
        eu = (eu_thread.meta or {}).get("end_user") if eu_thread else None
        # Conversation view: denormalize the turn's actor + transcript onto the Trace so the
        # Traces screen is a single grouped Trace query (no Run join at read time).
        source = getattr(run, "source", None) or "playground"
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
            source=source,
            actor=_actor_label(source, eu),
            end_user_id=(eu or {}).get("id"),
            user_message=_last_user_text(run.input) or None,
            ai_response=_last_ai_text(values or {}) or None,
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
                    input=sr.input,
                    output=sr.output,
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
        values = getattr(snapshot, "values", {}) or {}
        tokens, cost, spans = await self._write_trace(session, run, tracer, status=status, values=values)
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
            values = getattr(snapshot, "values", {}) or {}
            tokens, cost, spans = await self._write_trace(session, run, tracer, status="error", values=values)
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

    async def reap_stale_runs(
        self, *, queued_max_age_s: int = 900, running_max_age_s: int = 3600,
        hitl_timeout_s: int | None = None,
    ) -> int:
        """Mark runs stuck in non-terminal states as error so they never linger forever
        (audit F3): a `queued` run that was created but never streamed/driven, or a
        `running` run whose driver died (crash, missed disconnect, killed worker). Called
        periodically from the app lifespan. Returns the number reaped.

        Also expires `interrupted` runs awaiting a human approval for longer than
        `hitl_timeout_s` (audit C). A Human Input node with ``timeout_default`` resumes its
        durable checkpoint with that decision. Without a configured default (or without a live
        checkpointer), the run fails and its handoff is closed with a fallback channel message."""
        hitl_timeout_s = HITL_APPROVAL_TIMEOUT_SECONDS if hitl_timeout_s is None else hitl_timeout_s
        hitl_on = bool(hitl_timeout_s and hitl_timeout_s > 0)
        now = datetime.utcnow()
        q_cut = now - timedelta(seconds=queued_max_age_s)
        r_cut = now - timedelta(seconds=running_max_age_s)
        hitl_cut = now - timedelta(seconds=hitl_timeout_s) if hitl_on else None
        reaped = 0
        expired_hitl: list[tuple[str, str, str]] = []  # (run_id, tenant_id, workflow_id)
        async with SessionLocal() as s:
            statuses = ["queued", "running"] + (["interrupted"] if hitl_on else [])
            rows = (
                await s.execute(select(Run).where(Run.status.in_(statuses)))
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
                elif run.status == "interrupted" and hitl_cut is not None:
                    # ended_at is stamped at each pause (_finalize), so it's the time the run last
                    # went to await input - the right anchor for the approval deadline.
                    paused_at = run.ended_at or run.started_at or run.created_at or now
                    if paused_at < hitl_cut:
                        expired_hitl.append((run.id, run.tenant_id, run.workflow_id))
            if reaped:
                await s.commit()

        resumed_defaults = 0
        for run_id, tenant_id, workflow_id in expired_hitl:
            if await self._resume_hitl_timeout_default(run_id, tenant_id):
                reaped += 1
                resumed_defaults += 1
                continue
            # No default: fail only if the run is STILL interrupted. A human may have answered
            # between the scan and this write; never overwrite that concurrent completion.
            async with SessionLocal() as s:
                run = (
                    await s.execute(
                        select(Run).where(
                            Run.id == run_id, Run.tenant_id == tenant_id,
                            Run.status == "interrupted",
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    continue
                run.status = "error"
                run.error = f"HITL approval timed out after {hitl_timeout_s}s (no human reply)"
                run.ended_at = now
                await s.commit()
                reaped += 1
                try:
                    await self._notify_hitl_timeout(s, run_id, tenant_id, workflow_id)
                except Exception:  # noqa: BLE001 - one bad notify must not stop the reaper
                    log.exception("HITL timeout notify failed for run %s", run_id)
        if reaped:
            log.info(
                "reaped %d stale run(s) (%d HITL timeouts, %d resumed with defaults)",
                reaped, len(expired_hitl), resumed_defaults,
            )
        return reaped

    async def _resume_hitl_timeout_default(self, run_id: str, tenant_id: str) -> bool:
        """Resume an expired interrupt with its persisted default, if configured."""
        if self.checkpointer is None:
            return False
        try:
            from forge.models import HandoffRequest
            from forge.services.handoff import HandoffService, interrupt_hitl_meta

            set_current_tenant(tenant_id)
            async with SessionLocal() as session:
                run = (
                    await session.execute(
                        select(Run).where(
                            Run.id == run_id, Run.tenant_id == tenant_id,
                            Run.status == "interrupted",
                        )
                    )
                ).scalar_one_or_none()
                if run is None:
                    return False
                wf = await session.get(Workflow, run.workflow_id)
                thread = await session.get(Thread, run.thread_id)
                if wf is None or thread is None:
                    return False
                ctx = await build_compile_context(
                    session, tenant_id=tenant_id, project_id=run.project_id,
                    checkpointer=self.checkpointer, store=self.store,
                    end_user=(thread.meta or {}).get("end_user"), run_context=None,
                )
                graph = compile_workflow(wf.executable, ctx)
                config = {
                    "configurable": {"thread_id": thread.lg_thread_id},
                    "recursion_limit": _recursion_limit(wf.executable),
                }
                snapshot = await graph.aget_state(config)
                interrupts = [
                    jsonable(getattr(task, "interrupts", None))
                    for task in getattr(snapshot, "tasks", [])
                    if getattr(task, "interrupts", None)
                ]
                default = interrupt_hitl_meta(interrupts).get("timeout_default")
                if default is None:
                    return False
                handoff = (
                    await session.execute(
                        select(HandoffRequest).where(
                            HandoffRequest.run_id == run_id,
                            HandoffRequest.tenant_id == tenant_id,
                            HandoffRequest.status == "open",
                        ).order_by(HandoffRequest.created_at)
                    )
                ).scalars().first()
                if handoff is not None:
                    result = await HandoffService.reply(
                        session, self, handoff=handoff,
                        agent_id="system:hitl-timeout", message=str(default),
                    )
                    resume_result = result.get("resume") or {}
                    return bool(resume_result) and not resume_result.get("error")

            result = await self.resume(run_id=run_id, tenant_id=tenant_id, value=default)
            return not result.get("error")
        except Exception:  # noqa: BLE001 - default resume is best-effort; fail branch remains
            log.exception("HITL timeout default resume failed for run %s", run_id)
            return False

    @staticmethod
    async def _notify_hitl_timeout(session, run_id: str, tenant_id: str, workflow_id: str) -> None:
        """On HITL expiry: push the workflow's on_error fallback over the originating channel,
        then close the open handoff(s) for the run (audit C)."""
        from forge.models import Channel, HandoffRequest
        from forge.services.handoff import HandoffService, _channel_reply_context, _deliver

        wf = (await session.execute(select(Workflow).where(Workflow.id == workflow_id))).scalar_one_or_none()
        on_err = ((wf.executable if wf else {}) or {}).get("on_error") or {}
        fallback = on_err.get("message") or (
            "We weren't able to reach a team member in time. Please reply and we'll follow up."
        )
        rows = list((await session.execute(
            select(HandoffRequest).where(
                HandoffRequest.tenant_id == tenant_id, HandoffRequest.run_id == run_id,
                HandoffRequest.status.in_(("open", "answering")),
            )
        )).scalars())
        for h in rows:
            if not h.channel_id:
                continue
            ch = (await session.execute(select(Channel).where(Channel.id == h.channel_id))).scalar_one_or_none()
            reply_ctx = _channel_reply_context(h.reply_context)
            if ch is not None and reply_ctx:
                await _deliver(ch, reply_ctx, fallback)
        await HandoffService.close_for_run(session, run_id, tenant_id, reason="HITL approval timed out")
