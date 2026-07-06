"""REST API tool (Doc 2 §10).

`execute_rest` is the standalone core (used by the /test endpoint with a manual
context). `build_rest_tool` wraps it as a StructuredTool whose hidden `runtime`
arg supplies per-user context at agent-run time. Response projection cuts the
payload before it reaches the model - the primary token lever.
"""

# NO `from __future__ import annotations` here - on purpose. LangChain detects the
# injectable `runtime: ToolRuntime` parameter via inspect.signature(fn), which does NOT
# evaluate string annotations; postponed annotations make the runtime arg invisible, so
# it gets stripped during args_schema validation and _call crashes with
# "missing 1 required positional argument: 'runtime'". Eager annotations (fine on 3.11+)
# keep the real class on the signature for both langgraph injection and langchain_core
# pass-through.
import asyncio
import json as _json
import random
import re
import time
from typing import Any

import httpx
from langchain.tools import ToolRuntime
from pydantic import Field, create_model

from forge.auth_providers.templates import render_template
from forge.config import settings
from forge.tools.projection import cap_payload, project_response
from forge.util.http import shared_async_client
from forge.util.ratelimit import rate_limiter
from forge.util.ssrf import guarded_request, validate_url

_PY = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}

# --- reliability: response cache + retry classification (Doc tool config) ----------
_RESP_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_key(name: str, method: str, url: str, params: dict | None, body: Any) -> str:
    return "|".join([
        name, method, url,
        _json.dumps(params or {}, sort_keys=True, default=str),
        _json.dumps(body or {}, sort_keys=True, default=str),
    ])


def _cache_get(key: str, ttl: float) -> Any:
    hit = _RESP_CACHE.get(key)
    if hit and (time.monotonic() - hit[0]) <= ttl:
        return hit[1]
    return None


def _cache_put(key: str, value: Any) -> None:
    if len(_RESP_CACHE) > 5000:
        _RESP_CACHE.clear()
    _RESP_CACHE[key] = (time.monotonic(), value)


def _retry_types(names: list[str]) -> tuple[type[BaseException], ...]:
    """Map retry_on names -> exception types. Empty => retry transient HTTP/network errors."""
    if not names:
        return (httpx.HTTPError, httpx.TransportError, ConnectionError, TimeoutError)
    out: list[type[BaseException]] = []
    for n in names:
        k = str(n).strip().lower()
        if k in ("http_error", "http", "httpx"):
            out.append(httpx.HTTPError)
        elif k in ("timeout", "timeout_error"):
            out += [httpx.TimeoutException, TimeoutError]
        elif k in ("connection", "connection_error"):
            out += [httpx.ConnectError, httpx.TransportError, ConnectionError]
        elif k == "exception":
            out.append(Exception)
        elif k == "value_error":
            out.append(ValueError)
        elif k == "key_error":
            out.append(KeyError)
        elif k == "runtime_error":
            out.append(RuntimeError)
    return tuple(out) or (Exception,)


def build_args_schema(cfg: dict, fields_key: str = "fields"):
    """Pydantic args_schema from llm_visible request fields only."""
    req = cfg.get("request", cfg)
    props: dict[str, Any] = {}
    for f in req.get(fields_key, []) or []:
        if not f.get("llm_visible", True):
            continue
        typ = _PY.get(f.get("type", "string"), str)
        default = f.get("default", ... if f.get("required") else None)
        props[f["path"]] = (typ, Field(default=default, description=f.get("description", "")))
    return create_model(f"{cfg.get('name', 'tool')}_args", **props)


def build_args_schema_from_jsonschema(schema: dict, name: str = "tool_args"):
    """Pydantic args_schema from a JSON-Schema object ({properties, required}).

    Used by code/sql/mcp tools whose LLM-visible args are described by a JSON Schema
    rather than the REST `fields` list."""
    props_in = (schema or {}).get("properties", {}) or {}
    required = set((schema or {}).get("required", []) or [])
    props: dict[str, Any] = {}
    for key, spec in props_in.items():
        typ = _PY.get((spec or {}).get("type", "string"), str)
        default = ... if key in required else (spec or {}).get("default", None)
        props[key] = (typ, Field(default=default, description=(spec or {}).get("description", "")))
    return create_model(name, **props)


def _render_url(template: str, values: dict) -> str:
    missing: list[str] = []

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        val = values.get(key)
        if val is None:
            missing.append(key)
            return m.group(0)
        return str(val)

    url = re.sub(r"\{([a-zA-Z0-9_]+)\}", _sub, template)
    if missing:
        raise ValueError(f"missing required path parameter(s): {', '.join(missing)}")
    return url


def _collect(fields: list[dict], values: dict, where: str) -> dict:
    # `values` is already seeded with every field's (ctx-rendered) default by execute_rest, so
    # we read only from it. We deliberately do NOT fall back to the raw `f["default"]`: a default
    # that referenced a missing {{ctx.*}} key resolved to None and was dropped, and resurrecting
    # the raw value here would send the literal template string (e.g. "{{ctx.csrf}}").
    out: dict[str, Any] = {}
    for f in fields:
        if f.get("in") == where:
            name = f["path"]
            if values.get(name) is not None:
                out[name] = values[name]
    return out


def _build_body(req: dict, fields: list[dict], values: dict, context: dict | None):
    """Request body. A free-form `body_template` takes precedence and is interpolated with two
    namespaces - `{{input.*}}` (the validated tool args + defaults) and `{{ctx.*}}` (run
    context: per-run injected values + end_user) - so an arbitrary JSON shape can carry both
    model-decided inputs and server-injected credentials. It is parsed as JSON when possible,
    else sent as raw content (form-encoded / plain text). With no body_template, the body is
    assembled from `in: body` fields. Returns a dict/list, a str, or None."""
    tmpl = req.get("body_template")
    if tmpl:
        rendered = render_template(tmpl, {"input": values, "ctx": context or {}})
        if isinstance(rendered, (dict, list)):
            return rendered
        if isinstance(rendered, str):
            s = rendered.strip()
            if not s:
                return None
            try:
                return _json.loads(s)
            except ValueError:
                return s  # non-JSON body (e.g. application/x-www-form-urlencoded) -> raw content
        return rendered
    return _collect(fields, values, "body") or None


def _redirect_info(r: httpx.Response, followed: bool) -> dict | None:
    """Summarize redirect activity on a response for the model, or None if there was none.

    Two shapes:
    - followed: the redirect was chased SSRF-safely. `final_url` is the resolved target
      and `chain` lists the hop URLs; the response body IS the target's content.
    - a 3xx that was NOT followed: `location` is the target URL the API pointed at - the
      single most actionable thing for the agent (call a fetch tool on it, or the user
      can enable Follow redirects). Without this, a bare 3xx looks like an empty response.
    """
    history = list(r.history or [])
    is_3xx = 300 <= r.status_code < 400
    if not history and not is_3xx:
        return None
    if followed and history:
        return {
            "followed": True,
            "final_url": str(r.url),
            "final_status": r.status_code,
            "chain": [str(h.url) for h in history],
        }
    location = r.headers.get("location")
    return {
        "followed": False,
        "status": r.status_code,
        "requested_url": str(r.url),
        "location": location,
        "note": (
            "The API returned an HTTP redirect that Forge did not follow. 'location' is "
            "the target URL - call a fetch tool on it, or enable 'Follow redirects' on "
            "this tool to fetch it automatically."
            if location else
            "The API returned a redirect status with no Location header."
        ),
    }


def _tool_return(res: dict, cfg: dict) -> Any:
    """Shape an execute_rest/execute_graphql result into the tool observation the model sees.

    Normally just the projected body (an un-projected payload is char-capped so a huge
    response can't blow the model's context). When the API redirected, wrap it as
    {"body": ..., "redirect": {...}} so the model can see and act on the redirect target -
    otherwise a non-followed 3xx would reach the model as an empty body."""
    has_jmespath = bool((cfg.get("response") or {}).get("projection_jmespath"))
    body = res["projected"] if has_jmespath else cap_payload(res["projected"], settings.max_tool_response_chars)
    redirect = res.get("redirect")
    if redirect:
        return {"body": body, "redirect": redirect}
    return body


async def execute_rest(
    cfg: dict,
    kwargs: dict,
    *,
    tenant_id: str,
    project_id: str,
    context: dict | None = None,
    auth_resolver=None,
    client: httpx.AsyncClient | None = None,
    stream_writer=None,
    egress_policy=None,
) -> dict:
    """Execute the request and return {raw, projected, status, latency_ms}."""
    req = cfg["request"]
    fields = req.get("fields", []) or []
    ctx_vars = {"ctx": context or {}}
    # Seed request values from field defaults. A string default may reference per-run context
    # via {{ctx.*}} (e.g. a non-llm-visible `CSRFToken` field with default "{{ctx.csrf}}") - it
    # is rendered here so a server-injected value lands in the query/body/header per the field's
    # `in`. A default that references a MISSING ctx key renders to None and is DROPPED (never
    # sent as a literal template). LLM-supplied args (kwargs) are a SEPARATE lane, applied next;
    # they are NEVER templated, so the model can neither inject nor exfiltrate ctx values.
    values: dict[str, Any] = {}
    for f in fields:
        d = f.get("default")
        if d is None:
            continue
        rendered = render_template(d, ctx_vars) if isinstance(d, str) else d
        if rendered is not None:
            values[f["path"]] = rendered
    values.update({k: v for k, v in kwargs.items() if v is not None})

    # {{ctx.*}} is honored in the URL itself too (e.g. a base host, or a ?token= carried in run
    # context); {name} path params are then substituted from `values` as before.
    url_t = render_template(req["url_template"], ctx_vars)
    url = _render_url(url_t if isinstance(url_t, str) else str(url_t), values)
    params = _collect(fields, values, "query")
    body = _build_body(req, fields, values, context)
    # Header lanes in low->high precedence: (1) `in: header` fields (may be LLM-supplied), then
    # (2) config-declared headers templated from ctx. The templated headers are SERVER-
    # authoritative per-run injection (e.g. Cookie / CSRF from {{ctx.*}}) and must not be
    # overridable by an LLM-supplied header of the same name, so they are applied last.
    headers = _collect(fields, values, "header")
    headers.update({h["name"]: render_template(h.get("value", ""), ctx_vars) for h in req.get("headers", []) or []})

    # Cookies from `in: cookie` fields (ctx-templated defaults), plus the auth provider later.
    # Lets a session cookie be injected as {{ctx.jsessionid}} without a hand-written Cookie header.
    cookies: dict[str, str] = _collect(fields, values, "cookie")
    provider_id = cfg.get("auth_provider_id")

    async def apply_auth(force: bool = False) -> None:
        if not (provider_id and auth_resolver):
            return
        auth = await auth_resolver.resolve(
            tenant_id=tenant_id, project_id=project_id, provider_id=provider_id, context=context or {}, force=force,
        )
        headers.update(auth.headers)
        params.update(auth.params)
        cookies.update(auth.cookies)

    # SSRF guard: refuse internal/metadata targets before connecting.
    await validate_url(url, egress_policy)

    name = cfg.get("name", "tool")
    method = req["method"]
    follow_redirects = bool(req.get("follow_redirects", False))

    # Per-tool rate limit (config.rate_limit.per_minute), scoped per tenant.
    rl = (cfg.get("rate_limit") or {}).get("per_minute")
    if rl and not rate_limiter.allow(f"tool:{tenant_id}:{name}", rate=int(rl), per=60):
        raise RuntimeError(f"tool {name!r} exceeded its rate limit ({rl}/min)")

    # Response cache (config.cache.ttl_seconds), idempotent GETs only.
    ttl = (cfg.get("cache") or {}).get("ttl_seconds", 0) or 0
    cache_key = _cache_key(name, method, url, params, body) if (ttl and method.upper() == "GET") else None
    if cache_key:
        cached = _cache_get(cache_key, ttl)
        if cached is not None:
            return cached

    await apply_auth()
    if stream_writer:
        try:
            stream_writer({"tool": cfg.get("name"), "status": "calling", "url": url})
        except Exception:  # noqa: BLE001
            pass

    # Shared client (client construction costs ~470ms on Windows); timeout per request.
    client = client or shared_async_client()
    timeout = cfg.get("timeout_seconds", 30)
    retry_cfg = cfg.get("retry") or {}
    max_retries = int(retry_cfg.get("max_retries", 0) or 0)
    retry_types = _retry_types(retry_cfg.get("retry_on") or [])

    async def _send() -> httpx.Response:
        kw: dict[str, Any] = dict(headers=headers, params=params or None, cookies=cookies or None, timeout=timeout)
        # dict/list -> JSON body; a rendered raw string (e.g. form-encoded body_template) -> sent
        # as-is via content; None -> no body.
        if isinstance(body, (dict, list)):
            kw["json"] = body
        elif isinstance(body, str):
            kw["content"] = body
        if follow_redirects:
            # Chase redirects SSRF-safely - each hop is re-validated. Never enable httpx's
            # own redirect-following, which would connect to a hop without the egress guard.
            return await guarded_request(client, method, url, policy=egress_policy, follow_redirects=True, **kw)
        return await client.request(method, url, **kw)

    async def _once() -> httpx.Response:
        r = await _send()
        if r.status_code in (401, 403) and provider_id and auth_resolver:
            await apply_auth(force=True)
            r = await _send()
        # A 3xx we didn't follow is a capturable result (we surface the target URL to the
        # model via `redirect`), not a failure - only raise on real 4xx/5xx errors. httpx's
        # raise_for_status() otherwise treats a redirect response as an error.
        if not (300 <= r.status_code < 400 and not follow_redirects):
            r.raise_for_status()
        return r

    t0 = time.monotonic()
    attempt = 0
    try:
        while True:
            try:
                r = await _once()
                status = r.status_code
                try:
                    raw: Any = r.json()
                except Exception:  # noqa: BLE001 - non-JSON response
                    raw = r.text
                break
            except Exception as e:  # noqa: BLE001 - retry classification below
                if attempt >= max_retries or not isinstance(e, retry_types):
                    raise
                delay = min(
                    float(retry_cfg.get("max_delay", 60.0)),
                    float(retry_cfg.get("initial_delay", 1.0)) * (float(retry_cfg.get("backoff_factor", 2.0)) ** attempt),
                )
                if retry_cfg.get("jitter", True):
                    delay *= 0.5 + random.random()
                await asyncio.sleep(delay)
                attempt += 1
    finally:
        latency = int((time.monotonic() - t0) * 1000)

    out = {
        "raw": raw,
        "projected": project_response(raw, cfg.get("response")),
        "status": status,
        "latency_ms": latency,
        "final_url": str(r.url),
        "redirect": _redirect_info(r, follow_redirects),
    }
    if cache_key:
        _cache_put(cache_key, out)
    return out


def build_rest_tool(cfg: dict, ctx):
    args_schema = build_args_schema(cfg)

    # `runtime` must be annotated with the BARE ToolRuntime class (not Optional[...]) so the
    # injection machinery detects it. The None default matters: for tools with NO llm-visible
    # fields, langchain_core's _to_args_and_kwargs short-circuits empty-schema tools to
    # `(), {}` - dropping even the injected runtime - so zero-arg tools run uninjected.
    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        # Server-side entitlement gate (Feature 3b): deny independently of the LLM if the
        # run's end_user lacks the entitlements this tool declares (config.required_entitlements).
        required = cfg.get("required_entitlements") or []
        if required and getattr(ctx, "has_entitlements", None) and not ctx.has_entitlements(required):
            return f"Not permitted: this action requires {required}, which the current user is not entitled to."
        # Templating context for the outbound call ({{ctx.*}} in url/headers/query/body).
        # Three lanes, kept distinct on purpose:
        #   - ctx.run_context: ephemeral per-run values a server-side caller injected on the
        #     EXECUTION request (e.g. a per-user session cookie / CSRF token). Never persisted,
        #     never in the prompt, never an LLM arg.
        #   - runtime.context: LangGraph runtime context, if any.
        #   - end_user: the run's identity - authoritative, so it can't be shadowed by the above.
        context = {
            **(getattr(ctx, "run_context", None) or {}),
            **(getattr(runtime, "context", None) or {}),
            "end_user": getattr(ctx, "end_user", None),
        }
        sw = getattr(runtime, "stream_writer", None)
        res = await execute_rest(
            cfg, kwargs, tenant_id=ctx.tenant_id, project_id=ctx.project_id,
            context=context, auth_resolver=ctx.auth_resolver, stream_writer=sw,
            egress_policy=getattr(ctx, "egress_policy", None),
        )
        return _tool_return(res, cfg)

    return _build_structured_tool(_call, cfg, args_schema)


def _build_structured_tool(coroutine, cfg: dict, args_schema):
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(
        coroutine=coroutine, name=cfg["name"], description=cfg.get("description", ""), args_schema=args_schema,
    )
