"""DispatchService — turn an inbound trigger event into a run and execute it.

Shared by the webhook route, the scheduler, the email poller, and chat channels.
"""

from __future__ import annotations

import logging
from datetime import datetime

from forge.db.base import SessionLocal
from forge.models import Trigger
from forge.services.runs import RunService
from forge.services.triggers import TriggerService

log = logging.getLogger("forge.dispatch")


async def dispatch_trigger(run_service: RunService, trigger: Trigger, payload) -> dict:
    """Create a run for `trigger` from `payload` and run it to completion."""
    run_input = TriggerService.build_input(trigger, payload)
    async with SessionLocal() as s:
        run = await run_service.create_run(
            s, tenant_id=trigger.tenant_id, project_id=trigger.project_id,
            workflow_id=trigger.workflow_id, input=run_input,
        )
        run_id = run.id
        # stamp last_fired_at on the trigger
        t = await s.get(Trigger, trigger.id)
        if t is not None:
            t.last_fired_at = datetime.utcnow()
            await s.commit()
    result = await run_service.run_to_completion(run_id=run_id, tenant_id=trigger.tenant_id)
    return result


async def dispatch_message(
    run_service: RunService, *, tenant_id: str, project_id: str, workflow_id: str,
    text: str, thread_id: str | None = None,
) -> dict:
    """Run `workflow_id` with a single user `text` (used by channels: email/teams).
    Pass `thread_id` to continue a conversation (the checkpointer holds history)."""
    run_input = {"messages": [{"role": "user", "content": text or ""}]}
    async with SessionLocal() as s:
        run = await run_service.create_run(
            s, tenant_id=tenant_id, project_id=project_id, workflow_id=workflow_id,
            input=run_input, thread_id=thread_id,
        )
        run_id, run_thread_id = run.id, run.thread_id
    result = await run_service.run_to_completion(run_id=run_id, tenant_id=tenant_id)
    result["thread_id"] = run_thread_id
    result["workflow_id"] = workflow_id
    return result


async def run_due_schedules(run_service: RunService) -> int:
    """Fire every schedule trigger that is due. Returns how many fired."""
    async with SessionLocal() as s:
        due = await TriggerService.due_schedule_triggers(s)
    fired = 0
    for trig in due:
        try:
            await dispatch_trigger(run_service, trig, None)
            fired += 1
        except Exception:  # noqa: BLE001 - one bad schedule must not stop the rest
            log.exception("schedule trigger %s failed", trig.id)
    return fired


async def run_due_app_events(run_service: RunService) -> int:
    """Poll every due app_event trigger; dispatch a run per NEW item. Returns total fired."""
    from sqlalchemy import select

    from forge.models import Trigger

    async with SessionLocal() as s:
        rows = list((await s.execute(
            select(Trigger).where(Trigger.kind == "app_event", Trigger.enabled.is_(True))
        )).scalars())
    fired = 0
    for trig in rows:
        if not TriggerService.is_due(trig):
            continue
        try:
            fired += await _poll_app_event(run_service, trig)
        except Exception:  # noqa: BLE001 - one bad source must not stop the rest
            log.exception("app_event trigger %s failed", trig.id)
    return fired


async def _poll_app_event(run_service: RunService, trig) -> int:
    import json

    import jmespath

    from forge.models import Trigger
    from forge.util.http import shared_async_client
    from forge.util.ssrf import validate_url

    cfg = trig.config or {}
    url = cfg.get("poll_url")
    if not url:
        return 0
    await validate_url(url)
    method = (cfg.get("method") or "GET").upper()
    r = await shared_async_client().request(method, url, timeout=20, follow_redirects=True)
    r.raise_for_status()
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        data = r.text

    items = jmespath.search(cfg["items_path"], data) if cfg.get("items_path") else data
    if items is None:
        items = []
    if not isinstance(items, list):
        items = [items]

    dedupe = cfg.get("dedupe_key")

    def _key(item) -> str:
        if dedupe:
            return str(jmespath.search(dedupe, item))
        return json.dumps(item, sort_keys=True, default=str)

    seen = set((trig.meta or {}).get("seen", []))
    first_poll = trig.last_fired_at is None
    new_seen = list((trig.meta or {}).get("seen", []))
    new_items = []
    for item in items:
        k = _key(item)
        if k in seen:
            continue
        seen.add(k)
        new_seen.append(k)
        new_items.append(item)
    new_seen = new_seen[-1000:]  # cap dedupe memory

    count = 0
    # First poll only baselines the seen-set (don't replay history).
    if not first_poll:
        for item in new_items:
            text = jmespath.search(cfg["message_path"], item) if cfg.get("message_path") else item
            text = text if isinstance(text, str) else json.dumps(text, default=str)
            await dispatch_message(
                run_service, tenant_id=trig.tenant_id, project_id=trig.project_id,
                workflow_id=trig.workflow_id, text=str(text),
            )
            count += 1

    from datetime import datetime
    async with SessionLocal() as s:
        t = await s.get(Trigger, trig.id)
        if t is not None:
            t.meta = {**(t.meta or {}), "seen": new_seen}
            t.last_fired_at = datetime.utcnow()
            await s.commit()
    return count
