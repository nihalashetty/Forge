"""Trigger sync + scheduling + webhook dispatch (end-to-end via services)."""

from __future__ import annotations

from contextlib import aclosing
from datetime import datetime, timedelta

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import Trigger, User, Workflow
from forge.security import create_access_token
from forge.services.dispatch import dispatch_trigger
from forge.services.runs import RunService
from forge.services.triggers import TriggerService


async def _client_for(user_id: str, tenant: str, role: str) -> httpx.AsyncClient:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
    c.headers["Authorization"] = f"Bearer {create_access_token(user_id=user_id, tenant_id=tenant, role=role)}"
    return c


async def _make_user_role(tenant: str, email: str, role: str) -> str:
    async with SessionLocal() as s:
        u = User(tenant_id=tenant, email=email, role=role, status="active")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u.id

_WEBHOOK_WF = {
    "id": "wf_hook", "version": 1,
    "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
    "entry_node": "hook",
    "nodes": [
        {"id": "hook", "type": "webhook_in", "config": {"message_path": "text"}},
        {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Done."}},
        {"id": "end", "type": "end", "config": {}},
    ],
    "edges": [{"source": "hook", "target": "agent"}, {"source": "agent", "target": "end"}],
}


async def _make_wf(tenant="t_trig", project="p_trig") -> Workflow:
    async with SessionLocal() as s:
        wf = Workflow(tenant_id=tenant, project_id=project, name="Hooked", executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        await TriggerService.sync_from_workflow(s, wf)
        return wf


async def test_sync_creates_webhook_trigger_with_key():
    wf = await _make_wf()
    async with SessionLocal() as s:
        trigs = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchall()
    assert len(trigs) == 1
    async with SessionLocal() as s:
        t = await TriggerService.by_key(s, trigs[0].key)
    assert t is not None and t.kind == "webhook_in" and t.key


def test_build_input_message_path_extracts_field():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="hook", kind="webhook_in", config={"message_path": "text"})
    assert TriggerService.build_input(t, {"text": "hello there"}) == {"messages": [{"role": "user", "content": "hello there"}]}


def test_build_input_schedule_uses_config_message():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="s", kind="schedule", config={"message": "tick"})
    assert TriggerService.build_input(t, None)["messages"][0]["content"] == "tick"


def test_is_due_interval():
    t = Trigger(tenant_id="t", project_id="p", workflow_id="w", node_id="s", kind="schedule", config={"every_minutes": 10}, enabled=True)
    assert TriggerService.is_due(t, datetime.utcnow()) is True  # never fired -> due
    t.last_fired_at = datetime.utcnow()
    assert TriggerService.is_due(t, datetime.utcnow()) is False
    t.last_fired_at = datetime.utcnow() - timedelta(minutes=11)
    assert TriggerService.is_due(t, datetime.utcnow()) is True


async def test_dispatch_webhook_runs_workflow():
    wf = await _make_wf("t_d", "p_d")
    async with SessionLocal() as s:
        trig = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchone()
        trigger = await TriggerService.by_key(s, trig.key)
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_trigger(rs, trigger, {"text": "ping"})
    assert result.get("answer") == "Done." and result.get("status") == "done"


# --- who an unattended run acts as ----------------------------------------------------------
#
# Nobody is signed in when a webhook or a schedule fires, but every catalog connector is
# per-user. Without an identity on the trigger, a scheduled workflow has no token to resolve and
# dies at its first tool call - so the trigger carries the person who set it up.

async def test_sync_stamps_the_editor_as_the_run_as_identity():
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_owner", project_id="p_owner", name="Owned",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trig] = await TriggerService.sync_from_workflow(s, wf, owner="user-alice")
    assert trig.run_as_user_id == "user-alice"


async def test_a_colleague_editing_the_workflow_does_not_take_over_the_accounts():
    """The automation runs on whoever's Gmail is connected behind it. Repointing that at the last
    person to touch the canvas would change what the workflow can see without anyone deciding to."""
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_owner2", project_id="p_owner2", name="Owned2",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        await TriggerService.sync_from_workflow(s, wf, owner="user-alice")
        [trig] = await TriggerService.sync_from_workflow(s, wf, owner="user-bob")
    assert trig.run_as_user_id == "user-alice"


async def test_a_trigger_predating_the_column_adopts_the_next_editor():
    """Triggers that already exist have no owner. The next save claims them, so an upgrade
    doesn't leave every existing automation permanently unable to use a connector."""
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_owner3", project_id="p_owner3", name="Owned3",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [before] = await TriggerService.sync_from_workflow(s, wf)  # pre-upgrade shape
        assert before.run_as_user_id is None
        [after] = await TriggerService.sync_from_workflow(s, wf, owner="user-carol")
    assert after.run_as_user_id == "user-carol"


async def test_dispatch_binds_the_run_to_the_trigger_owner():
    """The payoff: the run carries an identity, so a per-user auth provider resolves THAT
    person's connected credential instead of failing with "no acting user"."""
    from forge.models import Run, Thread

    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_ru", project_id="p_ru", name="RunAs",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trigger] = await TriggerService.sync_from_workflow(s, wf, owner="user-dana")

    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_trigger(rs, trigger, {"text": "ping"})
    assert result.get("status") == "done"

    async with SessionLocal() as s:
        run = await s.get(Run, result["run_id"])
        thread = await s.get(Thread, run.thread_id)
    # The dim AuthResolver.bundle_secret_name hashes for a per-user provider.
    assert (thread.meta or {}).get("end_user") == {"id": "user-dana"}
    assert thread.user_external_id == "user-dana"


async def test_run_as_can_be_claimed_by_yourself_but_assigned_only_by_an_editor():
    """Claiming narrows a trigger to accounts YOU connected, so anyone may. Pointing it at
    someone else means their credentials get used by a workflow they may never have touched."""
    import uuid

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register",
                           json={"email": f"t{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        me = (await c.get("/v1/auth/me")).json()
        tenant, owner_id = me["tenant_id"], me["id"]
        pid = (await c.post("/v1/projects", json={"name": "T", "slug": f"trig-{uuid.uuid4().hex[:8]}"})).json()["id"]

        async with SessionLocal() as s:
            wf = Workflow(tenant_id=tenant, project_id=pid, name="Hooked", executable=_WEBHOOK_WF, status="active")
            s.add(wf)
            await s.commit()
            await s.refresh(wf)
            [trig] = await TriggerService.sync_from_workflow(s, wf)
            tid = trig.id
            other = User(tenant_id=tenant, email=f"o{uuid.uuid4().hex[:8]}@example.com", role="viewer", status="active")
            s.add(other)
            await s.commit()
            await s.refresh(other)
            other_id = other.id

        rows = (await c.get(f"/v1/projects/{pid}/triggers")).json()
        assert rows[0]["run_as_user_id"] is None
        assert rows[0]["run_as_email"] is None

        # An editor may hand it to someone else.
        r = await c.put(f"/v1/projects/{pid}/triggers/{tid}/run-as", json={"user_id": other_id})
        assert r.status_code == 200, r.text
        assert r.json()["run_as_user_id"] == other_id
        # ...but not to a user who isn't in the workspace.
        assert (await c.put(f"/v1/projects/{pid}/triggers/{tid}/run-as",
                            json={"user_id": "nope"})).status_code == 404

    # That viewer can claim it back for themselves without any elevated role.
    token = create_access_token(user_id=other_id, tenant_id=tenant, role="viewer")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as c2:
        c2.headers["Authorization"] = f"Bearer {token}"
        assert (await c2.put(f"/v1/projects/{pid}/triggers/{tid}/run-as", json={})).status_code == 200
        row = (await c2.get(f"/v1/projects/{pid}/triggers")).json()[0]
        assert row["run_as_is_me"] is True and row["run_as_email"]
        # ...but may not push it onto a colleague.
        assert (await c2.put(f"/v1/projects/{pid}/triggers/{tid}/run-as",
                             json={"user_id": owner_id})).status_code == 403


# --- whose automation is it -----------------------------------------------------------------
#
# Independent of run_as: `scope` says who the trigger BELONGS to. A salesperson's lead-chaser is
# theirs; a platform team's prod monitor is the project's.

def test_new_triggers_default_to_the_project_for_admins_and_to_the_person_otherwise():
    from forge.services.triggers import default_scope_for

    assert default_scope_for("owner") == "project"
    assert default_scope_for("admin") == "project"
    assert default_scope_for("editor") == "user"
    assert default_scope_for("viewer") == "user"
    # An unknown/absent role must not accidentally publish something to the whole team.
    assert default_scope_for(None) == "user"


async def test_scope_is_set_on_creation_and_never_flipped_by_an_edit():
    """Editing a workflow must not move a team automation into someone's private list, nor
    publish someone's private one to everybody."""
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_scope", project_id="p_scope", name="Scoped",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trig] = await TriggerService.sync_from_workflow(s, wf, owner="user-alice", scope="user")
        assert trig.scope == "user"
        # An admin later edits the same workflow - the trigger stays Alice's.
        [again] = await TriggerService.sync_from_workflow(s, wf, owner="user-admin", scope="project")
    assert again.scope == "user"


async def test_pre_existing_triggers_stay_visible_to_everyone():
    """The upgrade must not make a team's existing automations vanish from their screen, so a
    trigger synced with no scope is the project's."""
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_scope2", project_id="p_scope2", name="Legacy",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trig] = await TriggerService.sync_from_workflow(s, wf)
    assert trig.scope == "project"


async def test_personal_triggers_are_listed_only_for_their_owner_and_project_admins():
    import uuid

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register",
                           json={"email": f"t{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        me = (await c.get("/v1/auth/me")).json()
        tenant, admin_id = me["tenant_id"], me["id"]
        pid = (await c.post("/v1/projects", json={"name": "T", "slug": f"sc-{uuid.uuid4().hex[:8]}"})).json()["id"]

        async with SessionLocal() as s:
            alice = User(tenant_id=tenant, email=f"alice{uuid.uuid4().hex[:6]}@example.com", role="editor", status="active")
            bob = User(tenant_id=tenant, email=f"bob{uuid.uuid4().hex[:6]}@example.com", role="editor", status="active")
            s.add_all([alice, bob])
            await s.commit()
            await s.refresh(alice)
            await s.refresh(bob)
            alice_id, bob_id = alice.id, bob.id

            wf = Workflow(tenant_id=tenant, project_id=pid, name="Alice's chaser",
                          executable=_WEBHOOK_WF, status="active")
            s.add(wf)
            await s.commit()
            await s.refresh(wf)
            [trig] = await TriggerService.sync_from_workflow(s, wf, owner=alice_id, scope="user")
            tid = trig.id

        # The registering user is the workspace owner -> sees it, flagged as not theirs.
        rows = (await c.get(f"/v1/projects/{pid}/triggers")).json()
        assert [r["id"] for r in rows] == [tid]
        assert rows[0]["scope"] == "user" and rows[0]["visible_via_oversight"] is True

    # Alice sees her own.
    ca = await _client_for(alice_id, tenant, "editor")
    async with aclosing(ca):
        rows = (await ca.get(f"/v1/projects/{pid}/triggers")).json()
        assert [r["id"] for r in rows] == [tid]
        assert rows[0]["run_as_is_me"] is True and rows[0]["visible_via_oversight"] is False

    # Bob, an equal-ranking colleague, does not.
    cb = await _client_for(bob_id, tenant, "editor")
    async with aclosing(cb):
        assert (await cb.get(f"/v1/projects/{pid}/triggers")).json() == []

        # ...and cannot make someone else's trigger personal-to-nobody or grab it.
        r = await cb.put(f"/v1/projects/{pid}/triggers/{tid}/scope", json={"scope": "project"})
        assert r.status_code == 200, "an editor may share a trigger with the project"
        # Now that it's the project's, Bob can see it.
        assert len((await cb.get(f"/v1/projects/{pid}/triggers")).json()) == 1

    # Alice can take it back to personal - it is her account doing the work.
    ca2 = await _client_for(alice_id, tenant, "editor")
    async with aclosing(ca2):
        assert (await ca2.put(f"/v1/projects/{pid}/triggers/{tid}/scope", json={"scope": "user"})).status_code == 200
    cb2 = await _client_for(bob_id, tenant, "editor")
    async with aclosing(cb2):
        assert (await cb2.get(f"/v1/projects/{pid}/triggers")).json() == []
    assert admin_id  # (the owner above)


async def test_a_viewer_cannot_publish_a_trigger_to_the_whole_project():
    """Sharing makes colleagues see and depend on it, so it is an editor decision."""
    import uuid

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register",
                           json={"email": f"t{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        tenant = (await c.get("/v1/auth/me")).json()["tenant_id"]
        pid = (await c.post("/v1/projects", json={"name": "T", "slug": f"sc-{uuid.uuid4().hex[:8]}"})).json()["id"]
        async with SessionLocal() as s:
            wf = Workflow(tenant_id=tenant, project_id=pid, name="V", executable=_WEBHOOK_WF, status="active")
            s.add(wf)
            await s.commit()
            await s.refresh(wf)
            owner = await _make_user_role(tenant, f"o{uuid.uuid4().hex[:6]}@example.com", "editor")
            [trig] = await TriggerService.sync_from_workflow(s, wf, owner=owner, scope="user")
            tid = trig.id
            assert trig.scope == "user"

    viewer_id = await _make_user_role(tenant, f"v{uuid.uuid4().hex[:6]}@example.com", "viewer")
    cv = await _client_for(viewer_id, tenant, "viewer")
    async with aclosing(cv):
        assert (await cv.put(f"/v1/projects/{pid}/triggers/{tid}/scope",
                             json={"scope": "project"})).status_code == 403
        # An unknown scope is rejected outright rather than silently stored.
        assert (await cv.put(f"/v1/projects/{pid}/triggers/{tid}/scope",
                             json={"scope": "everyone"})).status_code == 422


def test_a_machine_principal_is_not_a_run_as_identity():
    """A workflow can be saved by the service token or a scoped API key. Neither has connected
    accounts, and `apikey:<uuid>` is 43 characters going into a String(36) column - which
    Postgres rejects, inside a trigger sync whose exceptions are swallowed. The visible symptom
    would be webhooks and schedules silently never being registered."""
    from forge.services.triggers import owner_id_for

    assert owner_id_for("service") is None
    assert owner_id_for(f"apikey:{'a' * 36}") is None
    assert owner_id_for("x" * 37) is None, "anything too long for the column is not an owner"
    real = "3f8b1c22-9a1e-4f77-8c31-2b6d5e0a7c44"
    assert owner_id_for(real) == real
    assert owner_id_for(None) is None


async def test_a_workflow_saved_by_a_machine_principal_yields_a_shared_trigger():
    """No owner means nobody to be personal TO: "listed only for the person it runs as" hides a
    trigger from everyone when that person doesn't exist. So an unowned trigger stays the
    project's, whatever default scope the caller's role suggested."""
    async with SessionLocal() as s:
        wf = Workflow(tenant_id="t_trig_svc", project_id="p_trig_svc", name="Svc",
                      executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trig] = await TriggerService.sync_from_workflow(
            s, wf, owner=f"apikey:{'b' * 36}", scope="user",
        )
    assert trig.run_as_user_id is None
    assert trig.scope == "project", "an ownerless trigger must not be invisible to everyone"


async def test_an_editor_cannot_make_someone_elses_trigger_personal():
    """"Personal" means "listed only for the person it runs as". An editor doing it to a trigger
    that runs as somebody else removes it from their OWN screen the moment they click, with no
    control left to undo it."""
    import uuid

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register",
                           json={"email": f"t{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        tenant = (await c.get("/v1/auth/me")).json()["tenant_id"]
        pid = (await c.post("/v1/projects", json={"name": "T", "slug": f"mp-{uuid.uuid4().hex[:8]}"})).json()["id"]

    colleague = await _make_user_role(tenant, f"c{uuid.uuid4().hex[:6]}@example.com", "editor")
    editor = await _make_user_role(tenant, f"e{uuid.uuid4().hex[:6]}@example.com", "editor")
    async with SessionLocal() as s:
        wf = Workflow(tenant_id=tenant, project_id=pid, name="P", executable=_WEBHOOK_WF, status="active")
        s.add(wf)
        await s.commit()
        await s.refresh(wf)
        [trig] = await TriggerService.sync_from_workflow(s, wf, owner=colleague, scope="project")
        tid = trig.id

    ce = await _client_for(editor, tenant, "editor")
    async with aclosing(ce):
        r = await ce.put(f"/v1/projects/{pid}/triggers/{tid}/scope", json={"scope": "user"})
        assert r.status_code == 403, "an editor is not the person this trigger runs as"
        # It is still on their screen, which is the point.
        listed = (await ce.get(f"/v1/projects/{pid}/triggers")).json()
        assert any(t["id"] == tid for t in listed)

    # The person it actually runs as may.
    cc = await _client_for(colleague, tenant, "editor")
    async with aclosing(cc):
        assert (await cc.put(f"/v1/projects/{pid}/triggers/{tid}/scope",
                             json={"scope": "user"})).status_code == 200


async def test_a_trigger_with_no_owner_cannot_be_made_personal():
    """Personal to nobody is listed for nobody. Refuse it and say what to do instead."""
    import uuid

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register",
                           json={"email": f"t{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        tenant = (await c.get("/v1/auth/me")).json()["tenant_id"]
        pid = (await c.post("/v1/projects", json={"name": "T", "slug": f"noown-{uuid.uuid4().hex[:8]}"})).json()["id"]
        async with SessionLocal() as s:
            wf = Workflow(tenant_id=tenant, project_id=pid, name="N", executable=_WEBHOOK_WF, status="active")
            s.add(wf)
            await s.commit()
            await s.refresh(wf)
            [trig] = await TriggerService.sync_from_workflow(s, wf)
            tid = trig.id
        assert trig.run_as_user_id is None

        r = await c.put(f"/v1/projects/{pid}/triggers/{tid}/scope", json={"scope": "user"})
        assert r.status_code == 400
        assert "Runs as" in r.json()["detail"]


async def test_an_unowned_trigger_still_runs_for_workflows_that_need_no_identity():
    """Not every workflow touches a per-user connector. One that doesn't must keep firing on a
    trigger nobody has claimed - the identity is a requirement of the tools, not of dispatch."""
    wf = await _make_wf("t_noowner", "p_noowner")
    async with SessionLocal() as s:
        trig = (await s.execute(Trigger.__table__.select().where(Trigger.workflow_id == wf.id))).fetchone()
        trigger = await TriggerService.by_key(s, trig.key)
    assert trigger.run_as_user_id is None
    rs = RunService(checkpointer=InMemorySaver())
    result = await dispatch_trigger(rs, trigger, {"text": "ping"})
    assert result.get("status") == "done"
