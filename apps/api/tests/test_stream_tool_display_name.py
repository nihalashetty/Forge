"""Human-readable tool names in the run stream.

A tool's underscore identifier (e.g. `get_quote_pricing_grid_data`) is what the model
calls, but end-user chat surfaces (the Westcon quoting bot, embeds) should show a friendly
label. The run stream binds a name->label map for the turn, and `jsonable` relabels every
serialized tool_call with a `display_name` (falling back to the identifier when unmapped),
while leaving the model-facing `name` untouched.
"""

from __future__ import annotations

import httpx
import pytest
from langchain_core.messages import AIMessage

from forge.tools.rest import execute_rest
from forge.util.serialize import (
    jsonable,
    reset_tool_display_names,
    set_tool_display_names,
)


def _ai_with_calls(*names: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": {}, "id": f"call_{i}"} for i, n in enumerate(names)],
    )


def test_display_name_maps_when_bound():
    token = set_tool_display_names({"get_quote_pricing_grid_data": "Quote pricing grid"})
    try:
        out = jsonable(_ai_with_calls("get_quote_pricing_grid_data"))
    finally:
        reset_tool_display_names(token)
    call = out["tool_calls"][0]
    # Model-facing name is unchanged; display_name carries the human label.
    assert call["name"] == "get_quote_pricing_grid_data"
    assert call["display_name"] == "Quote pricing grid"


def test_display_name_falls_back_to_identifier_when_unmapped():
    # Bound map that doesn't cover this tool -> display_name == the identifier.
    token = set_tool_display_names({"other_tool": "Other"})
    try:
        out = jsonable(_ai_with_calls("get_quote_totals_data"))
    finally:
        reset_tool_display_names(token)
    call = out["tool_calls"][0]
    assert call["name"] == "get_quote_totals_data"
    assert call["display_name"] == "get_quote_totals_data"


def test_display_name_falls_back_with_no_map_bound():
    # No map set for this context (e.g. resume/non-stream paths) -> identifier passthrough.
    out = jsonable(_ai_with_calls("get_quote_project_overview_data"))
    call = out["tool_calls"][0]
    assert call["display_name"] == "get_quote_project_overview_data"


def test_no_tool_calls_stays_none():
    out = jsonable(AIMessage(content="hello"))
    assert out["tool_calls"] is None


# --- the LIVE "calling" + terminal "done"/"error" custom frames (REST tool) ---------------
# The tool emits a "calling" frame before the request (label the spinner) and a paired terminal
# frame when it ENDS (clear the spinner): "done" on success, "error" on failure. Both carry the
# same tool id + display_name (config.display_name, else the identifier) so a client can pair them.

def _rest_cfg(**extra) -> dict:
    return {
        "name": "get_quote_totals_data",
        "kind": "rest_api",
        "request": {"method": "GET", "url_template": "https://api.acme.dev/totals", "fields": []},
        **extra,
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_client() -> httpx.AsyncClient:
    return _client(lambda r: httpx.Response(200, json={"ok": True}))


async def _run(cfg: dict, client: httpx.AsyncClient) -> list[dict]:
    frames: list[dict] = []
    try:
        await execute_rest(
            cfg, {}, tenant_id="t", project_id="p", context={}, client=client, stream_writer=frames.append,
        )
    finally:
        await client.aclose()
    return frames


async def test_calling_frame_includes_display_name():
    frames = await _run(_rest_cfg(display_name="Quote totals"), _ok_client())
    calling = [f for f in frames if f.get("status") == "calling"]
    assert calling, "expected a 'calling' custom frame"
    # model-facing id under `tool`; human label under `display_name`
    assert calling[0]["tool"] == "get_quote_totals_data"
    assert calling[0]["display_name"] == "Quote totals"


async def test_calling_frame_display_name_falls_back_to_identifier():
    frames = await _run(_rest_cfg(), _ok_client())
    calling = [f for f in frames if f.get("status") == "calling"]
    assert calling and calling[0]["display_name"] == "get_quote_totals_data"


async def test_terminal_done_frame_on_success():
    frames = await _run(_rest_cfg(display_name="Quote totals"), _ok_client())
    statuses = [f.get("status") for f in frames]
    # calling first, then a terminal done - so a client can clear the spinner.
    assert statuses == ["calling", "done"]
    done = frames[-1]
    assert done["tool"] == "get_quote_totals_data"
    assert done["display_name"] == "Quote totals"
    assert done["status_code"] == 200
    assert "latency_ms" in done


async def test_terminal_error_frame_on_failure():
    # A 500 raises out of execute_rest, but the spinner must still be cleared via an "error" frame.
    client = _client(lambda r: httpx.Response(500, json={"detail": "boom"}))
    frames: list[dict] = []
    with pytest.raises(httpx.HTTPStatusError):
        await execute_rest(
            _rest_cfg(display_name="Quote totals"), {},
            tenant_id="t", project_id="p", context={}, client=client, stream_writer=frames.append,
        )
    await client.aclose()
    statuses = [f.get("status") for f in frames]
    assert statuses == ["calling", "error"]
    assert frames[-1]["display_name"] == "Quote totals"
    assert frames[-1]["status_code"] == 500
