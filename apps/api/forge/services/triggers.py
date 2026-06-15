"""TriggerService — sync workflow trigger nodes to Trigger rows and decide what fires.

A workflow's executable may contain trigger nodes (webhook_in / schedule / email_in /
chat_in). On publish/save we mirror those nodes into `triggers` rows so the dispatcher
(webhook route + scheduler + channels) can route inbound events to runs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from forge.models import Trigger
from forge.nodes.triggers import TRIGGER_TYPES


class TriggerService:
    @staticmethod
    async def sync_from_workflow(session, workflow) -> list[Trigger]:
        """Upsert one Trigger per trigger node in the workflow; drop removed ones."""
        ex = workflow.executable or {}
        nodes = [n for n in ex.get("nodes", []) if isinstance(n, dict) and n.get("type") in TRIGGER_TYPES]
        existing = list((await session.execute(
            select(Trigger).where(Trigger.workflow_id == workflow.id)
        )).scalars())
        by_node = {t.node_id: t for t in existing}
        seen: set[str] = set()
        out: list[Trigger] = []
        for n in nodes:
            node_id = n["id"]
            seen.add(node_id)
            cfg = n.get("config", {}) or {}
            trig = by_node.get(node_id)
            if trig is None:
                trig = Trigger(
                    tenant_id=workflow.tenant_id, project_id=workflow.project_id,
                    workflow_id=workflow.id, node_id=node_id, kind=n["type"],
                    key=uuid.uuid4().hex if n["type"] == "webhook_in" else None,
                    config=cfg, enabled=True,
                )
                session.add(trig)
            else:
                trig.kind = n["type"]
                trig.config = cfg
                if n["type"] == "webhook_in" and not trig.key:
                    trig.key = uuid.uuid4().hex
            out.append(trig)
        # Remove triggers whose node no longer exists.
        for t in existing:
            if t.node_id not in seen:
                await session.delete(t)
        await session.commit()
        for t in out:
            await session.refresh(t)
        return out

    @staticmethod
    async def by_key(session, key: str) -> Trigger | None:
        return (await session.execute(
            select(Trigger).where(Trigger.key == key, Trigger.enabled.is_(True))
        )).scalar_one_or_none()

    @staticmethod
    def build_input(trigger: Trigger, payload) -> dict:
        """Map an inbound payload to a run input ({messages:[{role,content}]})."""
        cfg = trigger.config or {}
        if trigger.kind == "schedule":
            text = cfg.get("message") or "Scheduled run."
        else:
            mp = cfg.get("message_path")
            if mp and isinstance(payload, (dict, list)):
                import jmespath
                val = jmespath.search(mp, payload)
                text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False, default=str)
            elif isinstance(payload, str):
                text = payload
            else:
                text = json.dumps(payload, ensure_ascii=False, default=str)
        return {"messages": [{"role": "user", "content": text or ""}]}

    @staticmethod
    def is_due(trigger: Trigger, now: datetime | None = None) -> bool:
        """Whether a schedule/app_event trigger should fire now (interval or cron)."""
        if trigger.kind not in ("schedule", "app_event") or not trigger.enabled:
            return False
        now = now or datetime.utcnow()
        cfg = trigger.config or {}
        last = trigger.last_fired_at
        every = cfg.get("every_minutes") or cfg.get("interval_minutes")
        if every:
            if last is None:
                return True
            return (now - last) >= timedelta(minutes=int(every))
        cron = cfg.get("cron")
        if cron:
            try:
                from croniter import croniter
            except ImportError:
                return False  # cron needs croniter (workers extra); use every_minutes otherwise
            base = last or (now - timedelta(minutes=1))
            nxt = croniter(cron, base).get_next(datetime)
            return nxt <= now
        return False

    @staticmethod
    async def due_schedule_triggers(session, now: datetime | None = None) -> list[Trigger]:
        rows = list((await session.execute(
            select(Trigger).where(Trigger.kind == "schedule", Trigger.enabled.is_(True))
        )).scalars())
        return [t for t in rows if TriggerService.is_due(t, now)]
