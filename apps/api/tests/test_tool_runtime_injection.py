"""Regression: ToolRuntime must be injected into materialized REST/GraphQL tools.

Two historical failure modes (both caused by `from __future__ import annotations`
in forge/tools/rest.py):
1. compile time - NameError("ToolRuntime") when create_agent resolved the string
   annotation against module globals where ToolRuntime wasn't imported.
2. call time - "_call() missing 1 required positional argument: 'runtime'":
   langchain_core's StructuredTool detects injectable params via
   inspect.signature(fn) (raw, unevaluated annotations), so a string annotation
   made the runtime arg invisible and it was stripped during validation.

These tests exercise the real create_agent → ToolNode → StructuredTool path with a
scripted model that actually calls the tool.
"""

import itertools

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from forge.engine.context import CompileContext
from forge.tools import rest

REST_CFG = {
    "name": "get_thing",
    "description": "Fetch a thing.",
    "kind": "rest_api",
    "request": {
        "method": "GET",
        "url_template": "https://api.example.dev/things/{thing_id}",
        "fields": [
            {"path": "thing_id", "type": "string", "in": "path", "required": True, "llm_visible": True},
        ],
    },
    "response": {},
}


@pytest.fixture()
def captured_exec(monkeypatch):
    """Stub execute_rest and capture what the tool coroutine passes through."""
    captured: dict = {}

    async def fake_exec(cfg, kwargs, *, tenant_id, project_id, context=None, auth_resolver=None, stream_writer=None, client=None, egress_policy=None):
        captured["kwargs"] = kwargs
        captured["context"] = context
        captured["tenant_id"] = tenant_id
        return {"raw": {"ok": True}, "projected": {"ok": True}, "status": 200, "latency_ms": 1}

    monkeypatch.setattr(rest, "execute_rest", fake_exec)
    return captured


def test_runtime_param_is_visible_to_langchain():
    """The injected-arg detection must see `runtime` as a real ToolRuntime class."""
    tool = rest.build_rest_tool(REST_CFG, CompileContext(tenant_id="t", project_id="p"))
    assert tool._injected_args_keys == frozenset({"runtime"}), (
        "StructuredTool can't see the runtime param - string annotations strike again "
        "(check for `from __future__ import annotations` in forge/tools/rest.py)"
    )
    # The model-facing schema must NOT advertise runtime as an input.
    assert "runtime" not in (tool.tool_call_schema.model_json_schema().get("properties") or {})


async def test_zero_field_tool_executes_without_injection(captured_exec):
    """Tools with NO llm-visible fields (empty args schema) hit langchain_core's empty-schema
    short-circuit, which drops even injected args - the coroutine must tolerate runtime=None."""
    from langchain.agents import create_agent

    cfg = {
        "name": "get_weather",
        "description": "Fetch current weather.",
        "kind": "rest_api",
        "request": {"method": "GET", "url_template": "https://api.example.dev/weather", "fields": []},
        "response": {},
    }

    class ScriptedModel(GenericFakeChatModel):
        def bind_tools(self, tools=None, **kwargs):  # noqa: ANN001
            return self

    script = itertools.cycle(
        [
            AIMessage(content="", tool_calls=[{"name": "get_weather", "args": {}, "id": "c1", "type": "tool_call"}]),
            AIMessage(content="done"),
        ]
    )
    tool = rest.build_rest_tool(cfg, CompileContext(tenant_id="t", project_id="p"))
    agent = create_agent(model=ScriptedModel(messages=script), tools=[tool])

    out = await agent.ainvoke({"messages": [{"role": "user", "content": "weather?"}]})

    assert captured_exec["kwargs"] == {}
    tool_msgs = [m for m in out["messages"] if getattr(m, "type", "") == "tool"]
    assert tool_msgs and "ok" in str(tool_msgs[-1].content)


async def test_agent_tool_call_injects_runtime(captured_exec):
    """Full loop: agent's model emits a tool call; ToolNode must inject runtime."""
    from langchain.agents import create_agent

    class ScriptedModel(GenericFakeChatModel):
        def bind_tools(self, tools=None, **kwargs):  # noqa: ANN001
            return self

    script = itertools.cycle(
        [
            AIMessage(content="", tool_calls=[{"name": "get_thing", "args": {"thing_id": "42"}, "id": "c1", "type": "tool_call"}]),
            AIMessage(content="done"),
        ]
    )
    tool = rest.build_rest_tool(REST_CFG, CompileContext(tenant_id="t", project_id="p"))
    agent = create_agent(model=ScriptedModel(messages=script), tools=[tool])

    out = await agent.ainvoke({"messages": [{"role": "user", "content": "get thing 42"}]})

    # The tool actually executed (no TypeError about 'runtime') with the model's args.
    assert captured_exec["kwargs"] == {"thing_id": "42"}
    tool_msgs = [m for m in out["messages"] if getattr(m, "type", "") == "tool"]
    assert tool_msgs, "tool result message missing from agent transcript"
    assert "ok" in str(tool_msgs[-1].content)
