"""TriggerService - sync workflow trigger nodes to Trigger rows and decide what fires.

A workflow's executable may contain trigger nodes (webhook_in / schedule / email_in /
app_event). On publish/save we mirror those nodes into `triggers` rows so the dispatcher
(webhook route + scheduler + channels) can route inbound events to runs.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from forge.models import Trigger
from forge.nodes.triggers import TRIGGER_TYPES

#: Scopes a trigger can have. "project" is a team automation everyone sees; "user" is someone's
#: own, listed only for them.
SCOPES = ("project", "user")

#: `Trigger.run_as_user_id` is String(36) - a uuid. A machine principal's id is not one.
_MAX_OWNER_LEN = 36


def owner_id_for(user_id: str | None) -> str | None:
    """The person a trigger runs as, or None when the saver isn't one.

    A workflow can be saved by a machine principal: the static service token (id "service") or a
    scoped API key (id "apikey:<uuid>"). Neither has connected accounts, so neither is a
    meaningful run-as identity - and `apikey:<uuid>` is 43 characters going into a String(36)
    column, which Postgres rejects outright. `_sync_triggers` swallows exceptions so a workflow
    still saves, meaning the failure would surface as webhooks and schedules silently never being
    registered. The dedicated run-as route already refuses these identities; this is the same
    rule applied on the path that stamps them implicitly.
    """
    if not user_id:
        return None
    if user_id.startswith(("apikey:", "service")) or len(user_id) > _MAX_OWNER_LEN:
        return None
    return user_id


def default_scope_for(role: str | None) -> str:
    """Whether a NEW trigger this person creates belongs to the project or to them.

    Admins and owners are configuring the project - a build pipeline, a prod monitor - so what
    they add is the team's. Everyone else is building for themselves (a salesperson's own
    lead-chaser), so theirs stays theirs until they deliberately share it. Either way the choice
    is only a default: both directions are one click on the Triggers screen.
    """
    return "project" if role in ("owner", "admin") else "user"


class TriggerService:
    @staticmethod
    async def sync_from_workflow(session, workflow, *, owner: str | None = None,
                                 scope: str | None = None) -> list[Trigger]:
        """Upsert one Trigger per trigger node in the workflow; drop removed ones.

        `owner` is the editor doing the save. It becomes the trigger's `run_as_user_id` - the
        identity an unattended run acts as - and is only ever stamped on a trigger that doesn't
        have one yet. A colleague editing the workflow must NOT silently take ownership: the
        automation runs on whoever's Gmail/Slack account is connected behind it, and quietly
        repointing that at the last person to touch the canvas would change what the workflow
        can see without anyone deciding to. Reassignment is explicit (Triggers screen).

        `scope` is the same story for ownership: applied only when the trigger is NEW. Editing a
        workflow must never move a team automation into someone's private list, nor publish
        someone's private one to the whole project.

        A save by a machine principal (service token / API key) yields no owner, and a trigger
        with no owner is never personal - "listed only for the person it runs as" hides it from
        everyone when that person doesn't exist.
        """
        owner = owner_id_for(owner)
        if not owner:
            scope = "project"
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
                    config=cfg, enabled=True, run_as_user_id=owner,
                    scope=(scope if scope in SCOPES else "project"),
                )
                session.add(trig)
            else:
                trig.kind = n["type"]
                trig.config = cfg
                if n["type"] == "webhook_in" and not trig.key:
                    trig.key = uuid.uuid4().hex
                # Adopt an owner for a trigger created before this column existed, but never
                # replace one that is already set (see the docstring).
                if not trig.run_as_user_id and owner:
                    trig.run_as_user_id = owner
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
