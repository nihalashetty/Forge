"""Secrets, auth resolver (csrf/bearer), REST tool + projection, and builtin /test validation."""

from __future__ import annotations

import httpx
from sqlalchemy import select

from forge.auth_providers.resolver import AuthResolver
from forge.db.base import SessionLocal
from forge.models import AuditLog, AuthProvider
from forge.secrets.store import SecretStore
from forge.services.tools import ToolService
from forge.tools.rest import build_args_schema, execute_rest

GET_ORDER = {
    "name": "get_order",
    "description": "Fetch an order.",
    "kind": "rest_api",
    "request": {
        "method": "GET",
        "url_template": "https://api.acme.dev/v2/orders/{order_id}",
        "fields": [
            {"path": "order_id", "type": "string", "in": "path", "required": True, "llm_visible": True},
            {"path": "include", "type": "string", "in": "query", "required": False, "llm_visible": False, "default": "totals"},
        ],
        "headers": [{"name": "Accept", "value": "application/json"}],
    },
    "response": {"projection_jmespath": "data.{subtotal: totals.subtotal, total: totals.grand_total, status: status}"},
}


# --- secrets ---
async def test_secret_roundtrip_encrypts_and_decrypts():
    store = SecretStore()
    async with SessionLocal() as s:
        await store.write(s, tenant_id="t_sec", project_id="p_sec", name="creds", value={"u": "a", "p": "b"}, kind="generic")
    got = await store.read_ref(tenant_id="t_sec", project_id="p_sec", ref="secret://proj/creds")
    assert got == {"u": "a", "p": "b"}
    async with SessionLocal() as s:
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == "t_sec",
                    AuditLog.project_id == "p_sec",
                    AuditLog.action == "secret.read",
                    AuditLog.resource_type == "secret",
                    AuditLog.resource_id == "creds",
                )
            )
        ).scalar_one()
    assert audit.meta == {"scheme": "secret"}


# --- REST tool ---
def test_args_schema_excludes_non_llm_visible_fields():
    Args = build_args_schema(GET_ORDER)
    assert set(Args.model_fields) == {"order_id"}  # `include` is hidden from the model


async def test_rest_execute_projects_payload():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        return httpx.Response(200, json={
            "data": {"totals": {"subtotal": 90, "grand_total": 99}, "line_items": [1, 2, 3, 4], "status": "open"},
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    res = await execute_rest(GET_ORDER, {"order_id": "A-1"}, tenant_id="t", project_id="p", context={}, auth_resolver=None, client=client)
    await client.aclose()

    assert "/orders/A-1" in seen["url"] and "include=totals" in seen["url"]
    assert res["projected"] == {"subtotal": 90, "total": 99, "status": "open"}
    assert res["raw"]["data"]["line_items"] == [1, 2, 3, 4]  # raw keeps everything


# --- auth resolver ---
async def test_csrf_session_extract_and_inject():
    ap = AuthProvider(
        id="ap1", tenant_id="t", project_id="p", name="orders", kind="csrf_session",
        config={
            "kind": "csrf_session",
            "token_fetch": {"method": "POST", "url": "https://api.acme.dev/auth/login", "body": {}},
            "extract": [
                {"name": "csrf", "from": "header", "header": "X-CSRF-Token"},
                {"name": "session", "from": "cookie", "cookie": "SESSIONID"},
            ],
            "inject": [
                {"to": "header", "name": "X-CSRF-Token", "value": "{{extracted.csrf}}"},
                {"to": "cookie", "name": "SESSIONID", "value": "{{extracted.session}}"},
            ],
            "cache_ttl_seconds": 1800,
        },
    )

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=[("X-CSRF-Token", "abc123"), ("set-cookie", "SESSIONID=zzz; Path=/")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolved = await AuthResolver().resolve(tenant_id="t", project_id="p", provider_id="ap1", provider=ap, client=client, force=True)
    await client.aclose()
    assert resolved.headers["X-CSRF-Token"] == "abc123"
    assert resolved.cookies["SESSIONID"] == "zzz"


async def test_bearer_static_auth():
    store = SecretStore()
    async with SessionLocal() as s:
        await store.write(s, tenant_id="t", project_id="p", name="tok", value="T0KEN", kind="bearer")
    ap = AuthProvider(id="b1", tenant_id="t", project_id="p", name="b", kind="bearer", config={"kind": "bearer", "token_ref": "secret://proj/tok"})
    resolved = await AuthResolver().resolve(tenant_id="t", project_id="p", provider_id="b1", provider=ap, force=True)
    assert resolved.headers["Authorization"] == "Bearer T0KEN"


# --- builtin tool /test ---
def test_resolve_model_injects_project_provider_key(monkeypatch):
    """A per-project key in ctx.provider_credentials is passed to init_chat_model."""
    import langchain.chat_models as cm

    captured: dict = {}

    def fake_init(model, **kwargs):
        captured["model"] = model
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(cm, "init_chat_model", fake_init)

    from forge.engine.context import CompileContext
    from forge.engine.models import resolve_model

    ctx = CompileContext(tenant_id="t", project_id="p", provider_credentials={"openai": "sk-proj-test"})
    resolve_model("openai:gpt-5.4-mini", ctx)
    assert captured["model"] == "openai:gpt-5.4-mini"
    assert captured.get("api_key") == "sk-proj-test"

    # Google uses google_api_key, not api_key.
    captured.clear()
    ctx2 = CompileContext(tenant_id="t", project_id="p", provider_credentials={"google_genai": "g-key"})
    resolve_model("google_genai:gemini-3.5-flash", ctx2)
    assert captured.get("google_api_key") == "g-key"


async def test_calculator_builtin_test_endpoint_logic():
    res = await ToolService.test("t", "p", {"name": "calc", "kind": "builtin", "builtin": "calculator", "description": "d"}, {"expression": "2*(3+4)"})
    assert res["ok"] is True
    assert res["projected"] == "14"


# --- tools wired into an agent ---
_CALC = {"name": "calculator", "kind": "builtin", "builtin": "calculator", "description": "Evaluate arithmetic."}


async def test_agent_compiles_and_runs_with_bound_tool():
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from forge.engine.compiler import compile_workflow
    from forge.services.runtime import make_runtime_ctx
    from forge.tools.materialize import materialize_tool

    ctx = make_runtime_ctx("t", "p")
    ctx.checkpointer = InMemorySaver()
    ctx.tool_registry = {"tool_calc": materialize_tool(_CALC, ctx)}
    wf = {
        "id": "wf_tool", "version": 1,
        "state": {"messages": {"type": "list[message]", "reducer": "add_messages"}},
        "entry_node": "agent",
        "nodes": [
            {"id": "agent", "type": "agent", "config": {"flavor": "agent", "model": "fake:Done.", "tools": ["tool_calc"]}},
            {"id": "end", "type": "end", "config": {}},
        ],
        "edges": [{"source": "agent", "target": "end"}],
    }
    graph = compile_workflow(wf, ctx)
    out = await graph.ainvoke({"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "t1"}})
    assert out["messages"][-1].content == "Done."


async def test_agent_actually_invokes_tool_full_loop():
    """Scripted fake model emits a tool call → calculator runs → 6*7 = 42 appears."""
    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, HumanMessage

    from forge.services.runtime import make_runtime_ctx
    from forge.tools.materialize import materialize_tool

    class Fake(GenericFakeChatModel):
        def bind_tools(self, tools=None, **kwargs):
            return self

    scripted = iter([
        AIMessage(content="", tool_calls=[{"name": "calculator", "args": {"expression": "6*7"}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="It is 42."),
    ])
    tool = materialize_tool(_CALC, make_runtime_ctx("t", "p"))
    agent = create_agent(model=Fake(messages=scripted), tools=[tool], system_prompt="Use the calculator.")
    out = await agent.ainvoke({"messages": [HumanMessage(content="what is 6*7")]})
    tool_msgs = [m for m in out["messages"] if getattr(m, "type", None) == "tool"]
    assert any("42" in str(m.content) for m in tool_msgs), [m.content for m in out["messages"]]
