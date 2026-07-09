"""application/x-www-form-urlencoded request bodies (generic form-encoded POST support).

`request.body_encoding = "form"` routes a structured body through httpx's `data=`, which
URL-encodes every value (spaces, =, &, newlines, unicode), emits list values as repeated
keys, and sets the Content-Type. This is generic to any form-encoded endpoint; the tests
use a classic authenticated form post (quoteNum + a multi-line productCodePost + a
ctx-injected CSRFToken) as a representative shape, not a hardcoded API.
"""

import json
from urllib.parse import parse_qs, parse_qsl

import httpx

from forge.tools.rest import build_args_schema, execute_rest
from forge.util.ssrf import EgressPolicy

# Permissive policy: the capturing client short-circuits the network, and block_private=False
# keeps the SSRF guard from doing a real DNS lookup on the example host.
_POLICY = EgressPolicy(block_private=False)


def _capturing_client(sink: dict) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        sink["request"] = request
        return httpx.Response(200, json={"ok": True})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _form_cfg(fields, **req_extra) -> dict:
    return {
        "name": "form_post",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/quote/products/addBOM",
            "body_encoding": "form",
            "fields": fields,
            "headers": [],
            **req_extra,
        },
    }


async def test_form_encoding_urlencodes_multiline_and_special_chars():
    """Headline case: a multi-line productCodePost value with `=`, spaces and a newline is
    URL-encoded correctly and round-trips through a form parser - and the Content-Type is set
    for the caller (no manual header)."""
    sink: dict = {}
    cfg = _form_cfg([
        {"path": "quoteNum", "type": "string", "in": "body", "llm_visible": True},
        {"path": "copyPasteOption", "type": "string", "in": "body", "llm_visible": True},
        {"path": "productCodePost", "type": "string", "in": "body", "llm_visible": True},
    ])
    multiline = "GLC-TE= 2\nFPR-9= 5"
    async with _capturing_client(sink) as client:
        await execute_rest(
            cfg,
            {"quoteNum": "00015858", "copyPasteOption": "P-Q", "productCodePost": multiline},
            tenant_id="t", project_id="p", client=client, egress_policy=_POLICY,
        )
    req = sink["request"]
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    parsed = parse_qs(req.content.decode(), keep_blank_values=True)
    assert parsed["quoteNum"] == ["00015858"]
    assert parsed["copyPasteOption"] == ["P-Q"]
    assert parsed["productCodePost"] == [multiline]  # newline, `=`, space survived encode->decode
    # The wire body must actually be percent-encoded, not literal, or a strict server misparses.
    assert b"\n" not in req.content and b" " not in req.content


async def test_form_encoding_includes_empty_field_and_ctx_injected_secret():
    """An empty field is sent as `key=` (present, blank), and a hidden {{ctx.*}} in:body field
    is injected into the encoded body without ever being an LLM arg."""
    sink: dict = {}
    cfg = _form_cfg([
        {"path": "resellerDecimalPoints", "type": "string", "in": "body", "llm_visible": True, "default": ""},
        {"path": "CSRFToken", "type": "string", "in": "body", "llm_visible": False, "default": "{{ctx.csrf}}"},
    ])
    assert "CSRFToken" not in build_args_schema(cfg).model_fields  # server-injected, not an LLM arg
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p",
                           context={"csrf": "ffbe7ee3-29ff"}, client=client, egress_policy=_POLICY)
    parsed = parse_qs(sink["request"].content.decode(), keep_blank_values=True)
    assert parsed["resellerDecimalPoints"] == [""]  # empty value present on the wire
    assert parsed["CSRFToken"] == ["ffbe7ee3-29ff"]


async def test_form_encoding_list_value_becomes_repeated_keys():
    """Generic repeated-key support: a list value serializes as `k=a&k=b` (not JSON)."""
    sink: dict = {}
    cfg = _form_cfg([{"path": "productCodePost", "type": "array", "in": "body", "llm_visible": True}])
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {"productCodePost": ["GLC-TE= 2", "FPR-9= 5"]},
                           tenant_id="t", project_id="p", client=client, egress_policy=_POLICY)
    pairs = parse_qsl(sink["request"].content.decode(), keep_blank_values=True)
    assert pairs == [("productCodePost", "GLC-TE= 2"), ("productCodePost", "FPR-9= 5")]


async def test_form_encoding_inferred_from_content_type_header():
    """With no explicit body_encoding, a declared urlencoded Content-Type + structured body
    infers form encoding."""
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/x",
            "fields": [{"path": "a", "type": "string", "in": "body", "llm_visible": True}],
            "headers": [{"name": "Content-Type", "value": "application/x-www-form-urlencoded"}],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {"a": "b c"}, tenant_id="t", project_id="p", client=client, egress_policy=_POLICY)
    assert parse_qs(sink["request"].content.decode())["a"] == ["b c"]


# --- regressions: an unset body_encoding keeps the legacy behavior -------------------------


async def test_default_structured_body_still_json():
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/x",
            "fields": [{"path": "amount", "type": "integer", "in": "body", "llm_visible": True}],
            "headers": [],
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {"amount": 5}, tenant_id="t", project_id="p", client=client, egress_policy=_POLICY)
    assert json.loads(sink["request"].content) == {"amount": 5}
    assert sink["request"].headers["content-type"].startswith("application/json")


async def test_raw_body_template_unchanged():
    sink: dict = {}
    cfg = {
        "name": "x",
        "request": {
            "method": "POST",
            "url_template": "https://portal.example.dev/x",
            "fields": [],
            "headers": [],
            "body_template": "CSRFToken={{ctx.csrf}}&scope=all",
        },
    }
    async with _capturing_client(sink) as client:
        await execute_rest(cfg, {}, tenant_id="t", project_id="p", context={"csrf": "C1"},
                           client=client, egress_policy=_POLICY)
    assert sink["request"].content == b"CSRFToken=C1&scope=all"
