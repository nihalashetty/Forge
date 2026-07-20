"""Import / export of tools, workflows, components, and agents (PortabilityService).

Covers the guarantees the feature promises: a faithful round-trip (every authored field
survives), fresh ids on import, auto-rename that never overwrites, intra-bundle id remap
(a workflow's subworkflow ref follows the new ids), tool auth-provider resolution against
the target project, runtime-junk stripping, and bundle-type validation.
"""

from __future__ import annotations

import types
import uuid

import httpx
import pytest

from forge.db.base import SessionLocal
from forge.main import create_app
from forge.models import AuthProvider, Component
from forge.services.agents import AgentService
from forge.services.components import ComponentService
from forge.services.portability import PortabilityService
from forge.services.projects import ProjectService
from forge.services.tools import ToolService
from forge.services.versions import VersionService
from forge.services.workflows import WorkflowService

AUTHOR = types.SimpleNamespace(id="u_importer", email="importer@forge.local")


async def _project(session, tenant_id, slug):
    return await ProjectService.create(session, tenant_id, name=slug.title(), slug=slug)


async def test_tool_export_import_roundtrip_strips_runtime_and_clears_missing_auth():
    tenant = "t_tool_rt"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        dst = await _project(session, tenant, "dst")
        ap = AuthProvider(tenant_id=tenant, project_id=src.id, name="bearer", kind="bearer", config={})
        session.add(ap)
        await session.commit()
        cfg = {
            "description": "call the widget API",
            "request": {"method": "GET", "url_template": "https://api.example.com/widgets"},
            "response": {"projection_jmespath": "data"},
            "_last_test": {"status": 200, "raw": "junk"},  # runtime state; must not export
        }
        tool = await ToolService.create(
            session, tenant, src.id, name="widget_api", kind="rest_api", config=cfg, auth_provider_id=ap.id
        )

        bundle = await PortabilityService.export(session, tenant, src.id, "tool", [tool.id])
        assert bundle["type"] == "tool"
        assert bundle["source"]["project_name"] == "Src"
        assert len(bundle["items"]) == 1
        item = bundle["items"][0]
        assert item["name"] == "widget_api"
        assert "_last_test" not in item["config"]  # runtime junk stripped
        assert item["config"]["request"]["url_template"] == "https://api.example.com/widgets"
        assert item["auth_provider_id"] == ap.id

        # Import into a DIFFERENT project that has no such auth provider.
        report = await PortabilityService.import_bundle(session, tenant, dst.id, bundle, author=AUTHOR)
        assert report["imported"] == 1
        assert report["items"][0]["renamed"] is False
        assert any("auth provider" in w for w in report["warnings"])

        imported = await ToolService.list(session, tenant, dst.id)
        assert len(imported) == 1
        it = imported[0]
        assert it.id != tool.id  # fresh id
        assert it.name == "widget_api"
        assert it.config["request"]["url_template"] == "https://api.example.com/widgets"
        assert "_last_test" not in it.config
        assert it.auth_provider_id is None  # cleared: provider not in target project

        # A version-history snapshot was recorded (imported == created + saved).
        versions = await VersionService.list(session, tenant, "tool", it.id)
        assert len(versions) >= 1


async def test_tool_auth_provider_kept_when_present_in_target():
    tenant = "t_tool_ap"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        dst = await _project(session, tenant, "dst")
        # Same provider id must exist in the target for it to survive; simulate a same-project
        # re-import by importing back into src (its provider exists).
        ap = AuthProvider(tenant_id=tenant, project_id=src.id, name="bearer", kind="bearer", config={})
        session.add(ap)
        await session.commit()
        tool = await ToolService.create(
            session, tenant, src.id, name="api", kind="rest_api",
            config={"description": "x", "request": {"method": "GET", "url_template": "https://x.test"}},
            auth_provider_id=ap.id,
        )
        bundle = await PortabilityService.export(session, tenant, src.id, "tool", [tool.id])
        report = await PortabilityService.import_bundle(session, tenant, src.id, bundle, author=AUTHOR)
        assert report["imported"] == 1
        # Re-imported into src: renamed (name clash) but auth kept.
        new_id = report["items"][0]["id"]
        assert report["items"][0]["renamed"] is True
        tools = {t.id: t for t in await ToolService.list(session, tenant, src.id)}
        assert tools[new_id].auth_provider_id == ap.id
        assert not any("auth provider" in w for w in report["warnings"])
        assert dst  # (unused target kept for symmetry)


async def test_auto_rename_never_overwrites_on_repeated_import():
    tenant = "t_rename"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        tool = await ToolService.create(
            session, tenant, src.id, name="lookup", kind="builtin",
            config={"description": "look things up", "builtin": "calculator"},
        )
        bundle = await PortabilityService.export(session, tenant, src.id, "tool", [tool.id])
        r1 = await PortabilityService.import_bundle(session, tenant, src.id, bundle, author=AUTHOR)
        r2 = await PortabilityService.import_bundle(session, tenant, src.id, bundle, author=AUTHOR)
        names = sorted(t.name for t in await ToolService.list(session, tenant, src.id))
        assert names == ["lookup", "lookup_imported", "lookup_imported_2"]
        assert r1["items"][0]["name"] == "lookup_imported"
        assert r2["items"][0]["name"] == "lookup_imported_2"


async def test_component_unique_name_autorenames_without_conflict():
    tenant = "t_comp"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        comp = await ComponentService.create(
            session, tenant, src.id, name="product_card", title="Card",
            html="<div>{{title}}</div>", css=".x{}", props_schema={"type": "object"},
            sample_props={"title": "Hi"}, actions=[{"id": "buy", "label": "Buy"}],
        )
        comp.enabled = False
        await session.commit()

        bundle = await PortabilityService.export(session, tenant, src.id, "component", [comp.id])
        item = bundle["items"][0]
        assert item["html"] == "<div>{{title}}</div>"
        assert item["actions"] == [{"id": "buy", "label": "Buy"}]
        assert item["enabled"] is False

        # Import back into the same project twice: the unique-name constraint must never trip.
        await PortabilityService.import_bundle(session, tenant, src.id, bundle, author=AUTHOR)
        await PortabilityService.import_bundle(session, tenant, src.id, bundle, author=AUTHOR)
        comps = sorted(c.name for c in await ComponentService.list(session, tenant, src.id))
        assert comps == ["product_card", "product_card_imported", "product_card_imported_2"]
        # Round-tripped fields survive (incl. the disabled state).
        imported = [c for c in await ComponentService.list(session, tenant, src.id) if c.name == "product_card_imported"][0]
        assert imported.enabled is False
        assert imported.actions == [{"id": "buy", "label": "Buy"}]


async def test_agent_config_preserved_and_attribution_is_importer():
    tenant = "t_agent"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        dst = await _project(session, tenant, "dst")
        config = {
            "flavor": "agent",
            "model": "openai:gpt-4o-mini",
            "system_prompt": "Be helpful.",
            "tools": ["some_tool_id"],
            "components": ["some_component_id"],
            "middleware": [{"type": "summarization", "enabled": True}],
        }
        agent = await AgentService.create(
            session, tenant, src.id, name="support", config=config,
            created_by="orig_user", created_by_email="orig@forge.local",
        )
        bundle = await PortabilityService.export(session, tenant, src.id, "agent", [agent.id])
        assert bundle["items"][0]["created_by_email"] == "orig@forge.local"

        await PortabilityService.import_bundle(session, tenant, dst.id, bundle, author=AUTHOR)
        imported = (await AgentService.list(session, tenant, dst.id))[0]
        assert imported.config == config  # full config verbatim (nothing dropped)
        assert imported.created_by == "u_importer"
        assert imported.created_by_email == "importer@forge.local"


async def test_workflow_subworkflow_reference_is_remapped_to_new_ids():
    tenant = "t_wf"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        dst = await _project(session, tenant, "dst")
        child = await WorkflowService.create(
            session, tenant, src.id, name="child",
            executable={"id": "child", "version": 1, "nodes": [], "edges": []},
            canvas={"nodes": [], "edges": []},
        )
        parent = await WorkflowService.create(
            session, tenant, src.id, name="parent",
            executable={
                "id": "parent", "version": 1,
                "nodes": [{"id": "sub_1", "type": "subworkflow", "config": {"workflow_id": child.id}}],
                "edges": [],
            },
            canvas={"nodes": [], "edges": []},
        )

        bundle = await PortabilityService.export(session, tenant, src.id, "workflow", [child.id, parent.id])
        report = await PortabilityService.import_bundle(session, tenant, dst.id, bundle, author=AUTHOR)
        assert report["imported"] == 2

        wfs = {w.name: w for w in await WorkflowService.list(session, tenant, dst.id)}
        new_child, new_parent = wfs["child"], wfs["parent"]
        assert new_child.id != child.id and new_parent.id != parent.id
        ref = new_parent.executable["nodes"][0]["config"]["workflow_id"]
        assert ref == new_child.id  # remapped to the freshly-created child, not the old id
        # Imported workflows land as drafts (publish is a separate, governance-checked action).
        assert new_parent.status == "draft"


async def test_import_rejects_wrong_bundle_type():
    tenant = "t_type"
    async with SessionLocal() as session:
        dst = await _project(session, tenant, "dst")
        with pytest.raises(ValueError):
            await PortabilityService.import_bundle(
                session, tenant, dst.id, {"type": "nonsense", "items": []}, author=AUTHOR
            )


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")


async def _owner(c: httpx.AsyncClient) -> dict:
    reg = (await c.post("/v1/auth/register", json={"email": f"u{uuid.uuid4().hex[:8]}@x.com", "password": "supersecret1"})).json()
    return {"Authorization": f"Bearer {reg['access_token']}"}


async def test_http_component_export_import_roundtrip_across_projects():
    """Full HTTP path: create → export → import into another project, over the real routes."""
    async with _client() as c:
        h = await _owner(c)
        src = (await c.post("/v1/projects", json={"name": "Source"}, headers=h)).json()
        dst = (await c.post("/v1/projects", json={"name": "Dest"}, headers=h)).json()
        comp = (await c.post(
            f"/v1/projects/{src['id']}/components",
            json={"name": "price_card", "title": "Price", "html": "<b>{{p}}</b>", "css": ".b{}",
                  "props_schema": {"type": "object"}, "sample_props": {"p": 9}, "actions": []},
            headers=h,
        )).json()

        exp = await c.post(f"/v1/projects/{src['id']}/components/export", json={"ids": [comp["id"]]}, headers=h)
        assert exp.status_code == 200, exp.text
        bundle = exp.json()
        assert bundle["type"] == "component" and len(bundle["items"]) == 1

        imp = await c.post(f"/v1/projects/{dst['id']}/components/import", json=bundle, headers=h)
        assert imp.status_code == 200, imp.text
        report = imp.json()
        assert report["imported"] == 1 and report["type"] == "component"

        listed = (await c.get(f"/v1/projects/{dst['id']}/components", headers=h)).json()
        assert [x["name"] for x in listed] == ["price_card"]
        assert listed[0]["html"] == "<b>{{p}}</b>"


async def test_http_import_wrong_type_returns_422():
    async with _client() as c:
        h = await _owner(c)
        pid = (await c.post("/v1/projects", json={"name": "P"}, headers=h)).json()["id"]
        # An agent bundle posted to the tools import endpoint must be rejected clearly.
        r = await c.post(f"/v1/projects/{pid}/tools/import", json={"type": "agent", "items": []}, headers=h)
        assert r.status_code == 422
        assert "agent" in r.json()["detail"]


async def test_export_ignores_ids_from_other_projects():
    tenant = "t_scope"
    async with SessionLocal() as session:
        src = await _project(session, tenant, "src")
        other = await _project(session, tenant, "other")
        mine = await ToolService.create(session, tenant, src.id, name="mine", kind="builtin", config={})
        theirs = await ToolService.create(session, tenant, other.id, name="theirs", kind="builtin", config={})
        bundle = await PortabilityService.export(session, tenant, src.id, "tool", [mine.id, theirs.id])
        names = [i["name"] for i in bundle["items"]]
        assert names == ["mine"]  # cross-project id silently dropped
        assert Component  # import kept tidy
