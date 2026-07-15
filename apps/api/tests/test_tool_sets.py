"""Tool Sets: service CRUD + membership, agent toolset->tools resolution, and the REST API."""

from __future__ import annotations

import uuid

import httpx

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import Project, Tool, User
from forge.services.runtime import build_compile_context
from forge.services.tool_sets import ToolSetService
from forge.services.tools import ToolService


async def _seed(tenant: str, slug: str) -> tuple[str, str, str]:
    async with SessionLocal() as s:
        proj = Project(tenant_id=tenant, name="TS Proj", slug=slug, config={})
        s.add(proj)
        await s.flush()
        t1 = Tool(tenant_id=tenant, project_id=proj.id, name="alpha", kind="builtin",
                  config={"builtin": "calculator", "description": "a"})
        t2 = Tool(tenant_id=tenant, project_id=proj.id, name="beta", kind="builtin",
                  config={"builtin": "current_time", "description": "b"})
        s.add_all([t1, t2])
        await s.commit()
        for obj in (proj, t1, t2):
            await s.refresh(obj)
        return proj.id, t1.id, t2.id


async def test_tool_set_service_crud_and_membership():
    tenant = "t_ts_svc"
    pid, t1, t2 = await _seed(tenant, "ts-svc")
    async with SessionLocal() as s:
        ts = await ToolSetService.create(s, tenant, pid, name="Billing Tools", description="billing", tool_ids=[t1, t2])
        assert ts.slug == "billing-tools"
        assert set(await ToolSetService.member_ids(s, tenant, ts.id)) == {t1, t2}
        assert set((await ToolSetService.members_map(s, tenant, pid))[ts.id]) == {t1, t2}
        assert set(await ToolSetService.tool_ids_for_sets(s, tenant, pid, [ts.id])) == {t1, t2}

        # unknown / cross-project ids are filtered out of membership
        ts2 = await ToolSetService.create(s, tenant, pid, name="X", tool_ids=[t1, "does-not-exist"])
        assert await ToolSetService.member_ids(s, tenant, ts2.id) == [t1]

        # add / remove membership
        await ToolSetService.remove_member(s, ts, t1)
        assert await ToolSetService.member_ids(s, tenant, ts.id) == [t2]
        await ToolSetService.add_member(s, ts, t1)
        assert set(await ToolSetService.member_ids(s, tenant, ts.id)) == {t1, t2}
        await ToolSetService.add_member(s, ts, t1)  # idempotent (no duplicate row)
        assert len(await ToolSetService.member_ids(s, tenant, ts.id)) == 2

        # rename regenerates a unique slug (collides with ts2's "x")
        ts = await ToolSetService.update(s, ts, name="X")
        assert ts.slug == "x-2"

        # update can replace membership wholesale
        ts = await ToolSetService.update(s, ts, tool_ids=[t2])
        assert await ToolSetService.member_ids(s, tenant, ts.id) == [t2]

        # delete removes the set and its membership rows
        set_id = ts.id
        await ToolSetService.delete(s, ts)
        assert await ToolSetService.get(s, tenant, set_id) is None
        assert set_id not in await ToolSetService.members_map(s, tenant, pid)


async def test_tool_deletion_removes_membership():
    tenant = "t_ts_del"
    pid, t1, t2 = await _seed(tenant, "ts-del")
    async with SessionLocal() as s:
        ts = await ToolSetService.create(s, tenant, pid, name="S", tool_ids=[t1, t2])
        tool = await ToolService.get(s, tenant, t1)
        await ToolService.delete(s, tool)  # deleting a tool must drop its membership rows
        assert await ToolSetService.member_ids(s, tenant, ts.id) == [t2]


async def test_build_compile_context_resolves_toolset_to_member_tools():
    tenant = "t_ts_ctx"
    pid, t1, t2 = await _seed(tenant, "ts-ctx")
    async with SessionLocal() as s:
        set_id = (await ToolSetService.create(s, tenant, pid, name="Set A", tool_ids=[t1, t2])).id
    async with SessionLocal() as s:
        ctx = await build_compile_context(s, tenant_id=tenant, project_id=pid)
    # membership is loaded onto the compile context
    assert set(ctx.toolset_members.get(set_id, [])) == {t1, t2}
    # an agent granted only the set resolves to the set's member tool ids...
    assert set(ctx.resolve_tool_ids([], [set_id])) == {t1, t2}
    # ...and to the materialized tools (both builtins compiled into the registry)
    assert len(ctx.tools_for(ctx.resolve_tool_ids([], [set_id]))) == 2


async def test_tool_sets_api_end_to_end():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # register a real user (mutations require a real principal, not the dev fallback)
        reg = await c.post("/v1/auth/register", json={"email": f"u{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        # project + two tools, all via the API (one consistent tenant = the registered workspace)
        pid = (await c.post("/v1/projects", json={"name": "API TS", "slug": "api-ts"})).json()["id"]

        def _mk(name: str, builtin: str) -> dict:
            return {"name": name, "kind": "builtin", "config": {"builtin": builtin, "description": name}}

        t1 = (await c.post(f"/v1/projects/{pid}/tools", json=_mk("aa", "calculator"))).json()["id"]
        t2 = (await c.post(f"/v1/projects/{pid}/tools", json=_mk("bb", "current_time"))).json()["id"]

        # create a set with one member
        r = await c.post(f"/v1/projects/{pid}/tool-sets", json={"name": "Group One", "description": "g1", "tool_ids": [t1]})
        assert r.status_code == 201, r.text
        st = r.json()
        assert st["slug"] == "group-one" and st["tool_ids"] == [t1] and st["description"] == "g1"
        sid = st["id"]

        # list
        r = await c.get(f"/v1/projects/{pid}/tool-sets")
        assert r.status_code == 200 and any(x["id"] == sid for x in r.json())

        # add + remove via the membership endpoints
        assert (await c.post(f"/v1/projects/{pid}/tool-sets/{sid}/tools/{t2}")).status_code == 204
        assert set((await c.get(f"/v1/projects/{pid}/tool-sets/{sid}")).json()["tool_ids"]) == {t1, t2}
        assert (await c.delete(f"/v1/projects/{pid}/tool-sets/{sid}/tools/{t1}")).status_code == 204

        # patch: rename + replace membership
        r = await c.patch(f"/v1/projects/{pid}/tool-sets/{sid}", json={"name": "Renamed", "tool_ids": [t1, t2]})
        assert r.json()["slug"] == "renamed" and set(r.json()["tool_ids"]) == {t1, t2}

        # delete
        assert (await c.delete(f"/v1/projects/{pid}/tool-sets/{sid}")).status_code == 204
        assert (await c.get(f"/v1/projects/{pid}/tool-sets/{sid}")).status_code == 404


async def _seed_mcp_project(tenant: str, slug: str) -> tuple[str, str, str]:
    """Project + two builtin tools; returns (project_id, calc_tool_id, clock_tool_id)."""
    async with SessionLocal() as s:
        proj = Project(tenant_id=tenant, name="MCP TS", slug=slug, config={})
        s.add(proj)
        await s.flush()
        ta = Tool(tenant_id=tenant, project_id=proj.id, name="calc", kind="builtin",
                  config={"builtin": "calculator", "description": "c"})
        tb = Tool(tenant_id=tenant, project_id=proj.id, name="clock", kind="builtin",
                  config={"builtin": "current_time", "description": "t"})
        s.add_all([ta, tb])
        await s.commit()
        for obj in (proj, ta, tb):
            await s.refresh(obj)
        return proj.id, ta.id, tb.id


async def _list_names(c: httpx.AsyncClient, path: str) -> set[str]:
    r = await c.post(path, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"] for t in r.json()["result"]["tools"]}


async def test_mcp_toolset_scoped_exposure():
    tenant = "t_mcp_ts"
    pid, a_id, b_id = await _seed_mcp_project(tenant, "mcp-ts")
    async with SessionLocal() as s:
        await ToolSetService.create(s, tenant, pid, name="Set A", tool_ids=[a_id])
        set_b = await ToolSetService.create(s, tenant, pid, name="Set B", tool_ids=[b_id])
        b_slug, b_set_id = set_b.slug, set_b.id

    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # base endpoint = flat union of every EXPOSED set's enabled tools
        assert await _list_names(c, f"/v1/mcp/{pid}") == {"calc", "clock"}
        # per-set endpoint = just that set's flat list
        assert await _list_names(c, f"/v1/mcp/{pid}/toolset/{b_slug}") == {"clock"}
        assert await _list_names(c, f"/v1/mcp/{pid}/toolset/nope") == set()  # unknown slug => empty
        r = await c.post(f"/v1/mcp/{pid}/toolset/{b_slug}", json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                                                  "params": {"name": "clock", "arguments": {}}})
        assert r.json()["result"]["isError"] is False

        # un-expose Set B -> it drops off both the base surface and its own endpoint
        async with SessionLocal() as s:
            await ToolSetService.update(s, await ToolSetService.get(s, tenant, b_set_id), exposed=False)
        assert await _list_names(c, f"/v1/mcp/{pid}") == {"calc"}
        assert await _list_names(c, f"/v1/mcp/{pid}/toolset/{b_slug}") == set()
        r = await c.post(f"/v1/mcp/{pid}", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                                 "params": {"name": "clock", "arguments": {}}})
        assert "not exposed" in r.json()["error"]["message"]


async def test_mcp_tool_level_exclusion():
    """Everything in an exposed set is published by default; an operator can untick individual
    tools via project.config.mcp_excluded_tools."""
    tenant = "t_mcp_excl"
    pid, a_id, b_id = await _seed_mcp_project(tenant, "mcp-excl")
    async with SessionLocal() as s:
        await ToolSetService.create(s, tenant, pid, name="Set", tool_ids=[a_id, b_id])
        proj = await s.get(Project, pid)
        proj.config = {"mcp_excluded_tools": [b_id]}  # untick 'clock'
        await s.commit()
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert await _list_names(c, f"/v1/mcp/{pid}") == {"calc"}


async def test_mcp_no_toolsets_exposes_nothing():
    tenant = "t_mcp_none"
    pid, a_id, _b = await _seed_mcp_project(tenant, "mcp-none")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # No tool sets => nothing published (there are no loose / "direct" tools).
        assert await _list_names(c, f"/v1/mcp/{pid}") == set()
        # once a tool is placed in an exposed set, it appears
        async with SessionLocal() as s:
            await ToolSetService.create(s, tenant, pid, name="General", tool_ids=[a_id])
        assert await _list_names(c, f"/v1/mcp/{pid}") == {"calc"}


async def test_mcp_session_token_authorizes_as_end_user():
    """A project-scoped Forge session token authenticates an MCP caller AS its end_user
    (the portable per-user identity channel), alongside the shared project key."""
    from forge.security import create_session_token

    tenant = "t_mcp_sess"
    async with SessionLocal() as s:
        proj = Project(tenant_id=tenant, name="Sess", slug="mcp-sess", config={"mcp_api_key": "shared-key"})
        s.add(proj)
        await s.flush()
        s.add(Tool(tenant_id=tenant, project_id=proj.id, name="calc", kind="builtin",
                   config={"builtin": "calculator", "description": "c"}))
        await s.commit()
        await s.refresh(proj)
        pid = proj.id

    good = create_session_token(tenant_id=tenant, project_id=pid, end_user={"id": "u1", "entitlements": ["billing"]})
    wrong_project = create_session_token(tenant_id=tenant, project_id="another", end_user={"id": "u2"})
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # shared key -> authorized (no per-user identity)
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": "Bearer shared-key"}, json=body)).status_code == 200
        # project-scoped session token -> authorized as that end user
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": f"Bearer {good}"}, json=body)).status_code == 200
        # session token scoped to a different project -> rejected
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": f"Bearer {wrong_project}"}, json=body)).status_code == 401
        # no credential -> rejected (a key is configured)
        assert (await c.post(f"/v1/mcp/{pid}", json=body)).status_code == 401


async def test_mcp_personal_access_token_authorizes_as_user():
    """A per-user Personal Access Token (forge_pat_) authenticates an MCP client as that user."""
    from forge.services.apikeys import ApiKeyService

    tenant = "t_mcp_pat"
    async with SessionLocal() as s:
        proj = Project(tenant_id=tenant, name="PAT", slug="mcp-pat", config={"mcp_api_key": "shared-key"})
        s.add(proj)
        await s.flush()
        s.add(Tool(tenant_id=tenant, project_id=proj.id, name="calc", kind="builtin",
                   config={"builtin": "calculator", "description": "c"}))
        user = User(tenant_id=tenant, email="pat-user@example.com", role="editor", status="active")
        s.add(user)
        await s.commit()
        await s.refresh(proj)
        await s.refresh(user)
        pid = proj.id
        _k1, pat = await ApiKeyService.create_personal(s, tenant_id=tenant, user_id=user.id, name="t", project_id=pid)
        key_id = _k1.id
        _k2, pat_other = await ApiKeyService.create_personal(s, tenant_id=tenant, user_id=user.id, name="t2", project_id="another-project")

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        # a project-scoped PAT authorizes
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": f"Bearer {pat}"}, json=body)).status_code == 200
        # a PAT scoped to a different project is rejected here
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": f"Bearer {pat_other}"}, json=body)).status_code == 401
        # once revoked, the PAT no longer authorizes
        async with SessionLocal() as s:
            await ApiKeyService.revoke_personal(s, tenant_id=tenant, user_id=user.id, key_id=key_id)
        assert (await c.post(f"/v1/mcp/{pid}", headers={"Authorization": f"Bearer {pat}"}, json=body)).status_code == 401


async def test_mcp_token_api_crud():
    """The user-facing PAT endpoints mint / list / revoke tokens, and a minted token works on MCP."""
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        reg = await c.post("/v1/auth/register", json={"email": f"u{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
        assert reg.status_code == 201, reg.text
        c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
        pid = (await c.post("/v1/projects", json={"name": "PAT API", "slug": "pat-api"})).json()["id"]
        # lock the MCP surface behind a key so credential checks are meaningful
        await c.patch(f"/v1/projects/{pid}", json={"config": {"mcp_api_key": "k"}})

        r = await c.post(f"/v1/projects/{pid}/mcp-tokens", json={"name": "my token"})
        assert r.status_code == 201, r.text
        tok = r.json()
        assert tok["token"].startswith("forge_pat_") and tok["status"] == "active"

        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        # the freshly minted PAT authenticates against the MCP endpoint (auth is enforced by the key)
        assert (await c.post(f"/v1/mcp/{pid}", json=body)).status_code == 401
        pat_headers = {"Authorization": f"Bearer {tok['token']}"}
        assert (await c.post(f"/v1/mcp/{pid}", headers=pat_headers, json=body)).status_code == 200

        # listed without the plaintext, then revoked
        lst = (await c.get(f"/v1/projects/{pid}/mcp-tokens")).json()
        assert any(t["id"] == tok["id"] and t.get("token") is None for t in lst)
        assert (await c.delete(f"/v1/projects/{pid}/mcp-tokens/{tok['id']}")).status_code == 204
        assert (await c.post(f"/v1/mcp/{pid}", headers=pat_headers, json=body)).status_code == 401


async def test_connector_role_is_mcp_only():
    """A 'connector' user can manage their own MCP tokens but cannot mutate project resources."""
    from forge.security import create_access_token

    tenant = "t_conn_role"
    async with SessionLocal() as s:
        u = User(tenant_id=tenant, email="connector@example.com", role="connector", status="active")
        proj = Project(tenant_id=tenant, name="Conn", slug="conn-p", config={})
        s.add_all([u, proj])
        await s.commit()
        await s.refresh(u)
        await s.refresh(proj)
        uid, pid = u.id, proj.id

    token = create_access_token(user_id=uid, tenant_id=tenant, role="connector")
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {token}"
        # cannot create/mutate project resources (needs editor+)
        assert (await c.post("/v1/projects", json={"name": "X", "slug": "x-conn"})).status_code == 403
        assert (await c.post(f"/v1/projects/{pid}/tool-sets", json={"name": "S"})).status_code == 403
        # but can mint their own MCP personal access token
        r = await c.post(f"/v1/projects/{pid}/mcp-tokens", json={"name": "my token"})
        assert r.status_code == 201, r.text
        assert r.json()["token"].startswith("forge_pat_")
