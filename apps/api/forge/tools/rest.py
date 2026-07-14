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
import hashlib
import json as _json
import random
import re
import time
from typing import Any
from urllib.parse import quote

import httpx
from langchain.tools import ToolRuntime
from pydantic import Field, create_model

from forge.auth_providers.templates import has_each_directive, render_template, render_value
from forge.config import settings
from forge.tools.projection import cap_payload, project_response
from forge.tracing import tool_io
from forge.util.http import select_client
from forge.util.ratelimit import rate_limiter
from forge.util.ssrf import EgressPolicy, guarded_request, validate_url

_PY = {"string": str, "integer": int, "number": float, "boolean": bool, "object": dict, "array": list}

# Hard ceiling on a response body we will PARSE, guarding against an out-of-memory blowup from a
# pathologically large payload (a non-streaming httpx request has already buffered the bytes, but
# json.loads amplifies them several-fold into Python objects - that is the part that OOMs). Over
# this we skip parsing and return a small truncated marker. Wanted setting: `tool_max_download_bytes`
# (default 50 MB); a module constant until it is added to config.
_MAX_DOWNLOAD_BYTES = 50_000_000

# --- reliability: response cache + retry classification (Doc tool config) ----------
_RESP_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_key(
    name: str, method: str, url: str, params: dict | None, body: Any,
    *, tenant_id: str = "", project_id: str = "", provider_id: str = "", context: dict | None = None,
) -> str:
    # The process-global response cache MUST be partitioned by tenant/project, the auth
    # provider, AND a fingerprint of the per-run context (end_user + injected {{ctx.*}}
    # secrets such as a session cookie / CSRF token). Without this, a per-user-authenticated
    # GET produces an identical key for every caller, so enabling caching would serve one
    # user's private response to another (and could collide across tenants).
    ctx_fp = hashlib.sha256(
        _json.dumps(context or {}, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]
    return "|".join([
        tenant_id, project_id, provider_id, name, method, url,
        _json.dumps(params or {}, sort_keys=True, default=str),
        _json.dumps(body or {}, sort_keys=True, default=str),
        ctx_fp,
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


# Methods safe to retry automatically. POST/PATCH are non-idempotent (a retry can double-create
# / double-apply), so they are retried only when the tool explicitly opts in via
# retry.retry_non_idempotent. GET/HEAD/PUT/DELETE are idempotent by HTTP semantics.
_IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE", "OPTIONS", "TRACE"})


def _retry_types(names: list[str]) -> tuple[type[BaseException], ...]:
    """Map retry_on names -> exception types.

    Empty (the default) => retry only TRANSIENT transport/network failures. It deliberately does
    NOT include httpx.HTTPError, because HTTPStatusError is a subclass of it: with HTTPError in the
    set, raise_for_status()'s 4xx (a permanent client error - bad request, auth, not-found) would
    be retried pointlessly. 5xx is handled separately (see `_should_retry`) so a transient server
    error is still retried without dragging 4xx along. An explicit `retry_on: [http_error]` still
    opts into retrying every HTTP status."""
    if not names:
        return (httpx.TransportError, httpx.TimeoutException, ConnectionError, TimeoutError)
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


def _resolve_retry(cfg: dict) -> tuple[int, dict, tuple[type[BaseException], ...], bool]:
    """Resolve the retry policy: (max_retries, retry_cfg, retry_types, retry_5xx).

    No `retry` block at all (key absent) => no retries (max_retries 0), preserving the historic
    "retry is opt-in" default. When a retry block IS present (even an empty {}), an omitted
    max_retries defaults to 2 to match the schema (common.json RetryPolicy.max_retries default).
    `retry_5xx` is True only for the DEFAULT classification (empty retry_on): a transient 5xx is
    retried, while an explicit retry_on list is honored verbatim (so retry_on:[timeout] never
    drags in 5xx)."""
    retry_cfg = cfg.get("retry")
    if retry_cfg is None:
        return 0, {}, _retry_types([]), True
    retry_cfg = retry_cfg or {}  # tolerate an explicit null/empty block
    mr = retry_cfg.get("max_retries", 2)
    max_retries = int(mr) if mr is not None else 2
    retry_on = retry_cfg.get("retry_on") or []
    return max_retries, retry_cfg, _retry_types(retry_on), not retry_on


def _should_retry(
    e: BaseException, retry_types: tuple[type[BaseException], ...], retry_5xx: bool, method: str, retry_cfg: dict,
) -> bool:
    """Decide whether a failed attempt is retryable.

    Two gates: (1) idempotency - never auto-retry a POST/PATCH unless the tool set
    retry.retry_non_idempotent (a retry could double-create); (2) failure kind - a configured/
    transient exception, or (only for the default classification) a 5xx server error surfaced by
    raise_for_status. A 4xx is never retried by default."""
    if method.upper() not in _IDEMPOTENT_METHODS and not retry_cfg.get("retry_non_idempotent"):
        return False
    if isinstance(e, retry_types):
        return True
    if retry_5xx and isinstance(e, httpx.HTTPStatusError):
        resp = getattr(e, "response", None)
        return resp is not None and resp.status_code >= 500
    return False


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
        # URL-encode as a single path segment: an LLM-supplied value containing '/', '?', '#'
        # or '..' must not be able to alter the URL structure (path traversal / injected query
        # string). safe="" encodes reserved chars including '/'. Query params go through httpx
        # (already encoded); this closes the path-parameter lane.
        return quote(str(val), safe="")

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
        tvars = {"input": values, "ctx": context or {}}
        # A `$each` loop directive needs STRUCTURAL rendering (parse the JSON, then walk it with
        # render_value) so the produced array is always valid JSON with native types - plain string
        # substitution can't build a variable-length array without trailing-comma/quoting bugs.
        # Gate on an ACTUAL parsed `$each` directive (a dict key), NOT a substring of the raw text:
        # a template that merely mentions "$each" inside a string value must keep the exact
        # string-substitution behavior (structural rendering coerces token types differently).
        if "$each" in tmpl:
            try:
                parsed = _json.loads(tmpl)
            except ValueError:
                parsed = None
            if parsed is not None and has_each_directive(parsed):
                return render_value(parsed, tvars, allow_each=True)
        rendered = render_template(tmpl, tvars)
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


def _resolve_body_encoding(req: dict, headers: dict, body: Any) -> str:
    """Decide how the request body is serialized: 'json' | 'form' | 'raw'.

    Explicit `request.body_encoding` wins. Otherwise it is inferred from a declared
    Content-Type header (`application/x-www-form-urlencoded` -> form for a structured body,
    `application/json` -> json), falling back to the legacy default: a dict/list is sent as
    JSON, a string as raw content.

    'form' is the classic HTML form post: it routes a structured body through httpx's `data=`,
    which URL-encodes EVERY value (spaces, `=`, `&`, newlines, unicode) and sends list values as
    repeated keys, and sets the Content-Type for you - so callers no longer hand-encode a
    body_template. This is generic to any x-www-form-urlencoded endpoint, not a specific API."""
    enc = str(req.get("body_encoding") or "").strip().lower()
    if enc in ("json", "form", "multipart", "raw"):
        return enc
    ct = next((str(v) for k, v in (headers or {}).items() if k.lower() == "content-type"), "").lower()
    if "multipart/form-data" in ct and isinstance(body, dict):
        return "multipart"
    if "application/x-www-form-urlencoded" in ct and isinstance(body, dict):
        return "form"
    if "application/json" in ct:
        return "json"
    return "json" if isinstance(body, (dict, list)) else "raw"


def _split_multipart(body: Any) -> tuple[dict, dict]:
    """Split a structured body into (files, data) for httpx multipart encoding.

    A file-shaped value - a [filename, content] / [filename, content, content_type] list/tuple, or
    a {content, filename?, content_type?} dict - becomes a `files=` part; every other value is a
    plain form field in `data=`. If NO value is file-shaped, all fields are sent as text parts so
    httpx still emits multipart/form-data (with `data=` only it would fall back to urlencoded)."""
    files: dict[str, Any] = {}
    data: dict[str, Any] = {}
    if isinstance(body, dict):
        for k, v in body.items():
            if isinstance(v, (list, tuple)) and 2 <= len(v) <= 3:
                files[k] = tuple(v)
            elif isinstance(v, dict) and "content" in v:
                ct = v.get("content_type")
                part = (v.get("filename"), v.get("content"))
                files[k] = (*part, ct) if ct else part
            else:
                data[k] = v
    if not files and data:
        files = {k: (None, "" if val is None else str(val)) for k, val in data.items()}
        data = {}
    return files, data


def _read_body_capped(r: httpx.Response) -> Any:
    """Parse the response body, refusing to parse an over-large payload.

    See `_MAX_DOWNLOAD_BYTES`: this can't stop httpx buffering the bytes (that needs streaming in
    the egress layer, out of scope here), but it stops the far larger parse-amplification OOM by
    not calling json.loads on a giant body - it returns a small truncated marker instead."""
    body = r.content or b""
    if _MAX_DOWNLOAD_BYTES and len(body) > _MAX_DOWNLOAD_BYTES:
        preview = body[:2000].decode(r.encoding or "utf-8", errors="replace")
        return {
            "error": "response_too_large",
            "bytes": len(body),
            "limit": _MAX_DOWNLOAD_BYTES,
            "note": "response exceeded the max download size and was not parsed; add a narrower query or server-side filter",
            "body_preview": preview,
        }
    try:
        return r.json()
    except Exception:  # noqa: BLE001 - non-JSON response
        return r.text


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


def project_observation(res: dict, cfg: dict) -> tuple[Any, Any]:
    """Return (observation, projected) - the object the model sees, before and after projection.

    When the API redirected, the observation wraps the raw body as {"body": ..., "redirect": {...}}
    so the target URL survives even an empty 3xx body; a normal response's observation IS the raw
    body. The configured projection (JMESPath / field list) is applied to that WHOLE observation,
    which is what lets a `redirect.location` expression select just the target URL, `body.items[0]`
    reach a redirect's payload, and a plain `items[0]` still project a normal body unchanged. With
    no projection configured, projected == observation."""
    redirect = res.get("redirect")
    observation = {"body": res.get("raw"), "redirect": redirect} if redirect else res.get("raw")
    return observation, project_response(observation, cfg.get("response"))


def _tool_return(res: dict, cfg: dict) -> Any:
    """Shape an execute_rest/execute_graphql result into the tool observation the model sees.

    Projection is applied to the model observation (see `project_observation`): with no projection
    the model gets the raw body, or - on a redirect - the {"body", "redirect"} envelope carrying the
    target URL; a `redirect.location` projection collapses that envelope to just the URL. A
    result is char-capped so a huge un-projected payload can't blow the model's context. The
    cap is applied to JMESPath output too: a broken/typo'd expression falls back to the full
    raw payload and a broad expression (e.g. `@`) selects everything, so trusting JMESPath to
    be small let an un-projected payload through - always cap as a backstop."""
    _observation, projected = project_observation(res, cfg)
    return cap_payload(projected, settings.max_tool_response_chars)


def _capture_tool_io(
    cfg: dict, kwargs: dict, *, method, url, params, headers, cookies, body, req,
    response: httpx.Response | None, raw, status, latency_ms: int, error: Exception | None,
) -> None:
    """Stash this call's framed request + response into the tool-I/O context var so the
    ForgeTracer can attach it to the tool span. Best-effort: tracing must never break a call."""
    if not settings.trace_tool_io:
        return
    try:
        resp = response
        if resp is None and isinstance(error, httpx.HTTPStatusError):
            resp = error.response  # a 4xx/5xx raised by raise_for_status still carries the response
        body_out = raw
        if body_out is None and resp is not None:
            try:
                body_out = resp.json()
            except Exception:  # noqa: BLE001
                body_out = resp.text
        request = {
            # `args` = exactly what the LLM/agent supplied. Server-injected {{ctx.*}} secrets
            # are a separate lane and never land here, so this is safe to store in full.
            "args": tool_io.clip(kwargs),
            "method": method,
            "url": url,
            "query": tool_io.redact_headers(params),
            "headers": tool_io.redact_headers(headers),
            "cookies": tool_io.redact_headers(cookies, mask_all=True),
            "body": tool_io.clip(body),
            "body_encoding": (_resolve_body_encoding(req, headers, body) if body is not None else None),
        }
        out = {
            "status": (resp.status_code if resp is not None else status),
            "latency_ms": latency_ms,
            "final_url": (str(resp.url) if resp is not None else url),
            "response": tool_io.clip(body_out),
            "error": (str(error) if error is not None else None),
        }
        tool_io.set_tool_io(cfg.get("name", "tool"), request=request, response=out)
    except Exception:  # noqa: BLE001
        pass


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
    # A caller may hand a structured (`array`/`object`) arg as a JSON *string* rather than a
    # native list/dict - the /test panel does this (every field is a textarea string), and some
    # server-to-server callers stringify JSON args too. Coerce it to the type the field declares
    # so a `$each` body template iterates the real list instead of treating the whole string as a
    # single item (which renders every {{row.*}} to null). The LLM lane already supplies native
    # lists/dicts (a non-string is skipped), so this only rescues the string case; an unparseable
    # string is left as-is so the failure stays visible rather than being silently swallowed.
    for f in fields:
        if f.get("type") in ("array", "object"):
            v = values.get(f["path"])
            if isinstance(v, str) and v.strip():
                try:
                    parsed = _json.loads(v)
                except ValueError:
                    parsed = v  # unparseable -> leave as-is so the failure stays visible
                # Only accept a parse that actually yields the declared container type. A scalar
                # string like "5" or "null" parses to int/None; coercing that would silently
                # change the value's type - leave it as the original string instead.
                if isinstance(parsed, (list, dict)):
                    values[f["path"]] = parsed

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
    # A declared header whose value templates to None (a missing {{ctx.*}} key) is DROPPED, same
    # as a field default that renders to None - otherwise httpx rejects the None value with an
    # opaque TypeError, so a header like `X-CSRF-Token: {{ctx.csrf}}` would crash the whole call
    # (not just omit the header) whenever the run didn't inject `csrf`.
    for h in req.get("headers", []) or []:
        hv = render_template(h.get("value", ""), ctx_vars)
        if hv is not None:
            headers[h["name"]] = hv

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

    # SSRF guard: refuse internal/metadata targets before connecting. Resolve the policy once so
    # the same allow-list drives both the guard and the TLS-skip gate below.
    policy = egress_policy or EgressPolicy.from_settings()
    await validate_url(url, policy)

    name = cfg.get("name", "tool")
    # `tool` in stream frames stays the model-facing identifier; `display_name` is the human
    # label (config.display_name, else the identifier). Computed once - used by both the live
    # "calling" frame and the terminal done/error frame so a client can pair and clear a spinner.
    display_label = (cfg.get("display_name") or "").strip() or name
    method = req["method"]
    follow_redirects = bool(req.get("follow_redirects", False))

    # Per-tool rate limit (config.rate_limit.per_minute), scoped per tenant.
    rl = (cfg.get("rate_limit") or {}).get("per_minute")
    if rl and not rate_limiter.allow(f"tool:{tenant_id}:{name}", rate=int(rl), per=60):
        raise RuntimeError(f"tool {name!r} exceeded its rate limit ({rl}/min)")

    # Response cache (config.cache.ttl_seconds), idempotent GETs only.
    ttl = (cfg.get("cache") or {}).get("ttl_seconds", 0) or 0
    cache_key = _cache_key(
        name, method, url, params, body,
        tenant_id=tenant_id, project_id=project_id, provider_id=provider_id or "", context=context,
    ) if (ttl and method.upper() == "GET") else None
    if cache_key:
        cached = _cache_get(cache_key, ttl)
        if cached is not None:
            return cached

    await apply_auth()
    if stream_writer:
        try:
            # The LIVE "calling" signal - emitted before the request runs - so a client can label
            # a spinner without waiting for the tool_calls in the node-completion `updates` frame.
            # Paired with a terminal done/error frame in the `finally` below (same tool + url).
            stream_writer({
                "tool": name,
                "display_name": display_label,
                "status": "calling",
                "url": url,
            })
        except Exception:  # noqa: BLE001
            pass

    # TLS policy: `tls_skip_verify` opts out of certificate verification, honored ONLY for a host on
    # the egress allow_private_hosts list. select_client enforces the gate for the direct request,
    # and guarded_request re-applies it PER redirect hop, so verify-off never carries onto a hop
    # whose host isn't allow-private. An explicit `client` (tests) always wins. (Shared-client
    # construction costs ~470ms on Windows; select_client returns the shared singleton.)
    skip_verify = bool(req.get("tls_skip_verify"))
    timeout = cfg.get("timeout_seconds", settings.tool_request_timeout_seconds)
    max_retries, retry_cfg, retry_types, retry_5xx = _resolve_retry(cfg)

    async def _send() -> httpx.Response:
        kw: dict[str, Any] = dict(headers=headers, params=params or None, cookies=cookies or None, timeout=timeout)
        # Body serialization lane (see _resolve_body_encoding): form -> httpx `data=`
        # (URL-encodes every value + sets the Content-Type), json -> `json=`, raw -> `content=`.
        # None -> no body.
        if body is not None:
            enc = _resolve_body_encoding(req, headers, body)
            if enc == "form":
                if isinstance(body, str):
                    # A pre-encoded form string (e.g. from a body_template): send verbatim, but
                    # make sure the Content-Type is declared so the server parses it as a form.
                    kw["content"] = body
                    headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
                else:
                    kw["data"] = body  # dict / list-of-pairs -> httpx urlencodes + sets Content-Type
            elif enc == "multipart":
                # multipart/form-data via httpx `files=` (+ `data=` for scalar fields). httpx sets
                # the Content-Type with its own boundary, so any pre-declared multipart header is
                # dropped to avoid a boundary mismatch. Falls back to raw for a non-dict body.
                files, data = _split_multipart(body)
                if files or data:
                    headers.pop("Content-Type", None)
                    headers.pop("content-type", None)
                    if files:
                        kw["files"] = files
                    if data:
                        kw["data"] = data
                else:
                    kw["content"] = body if isinstance(body, str) else _json.dumps(body)
            elif enc == "raw":
                kw["content"] = body if isinstance(body, str) else _json.dumps(body)
            else:
                kw["json"] = body
        if follow_redirects:
            # Chase redirects SSRF-safely - each hop is re-validated AND its client re-selected
            # (verify-off only for an allow-private hop). Never enable httpx's own redirect-following,
            # which would connect to a hop without the egress guard.
            return await guarded_request(
                client, method, url, policy=policy, skip_verify=skip_verify, follow_redirects=True,
                max_redirects=settings.tool_max_redirects, **kw
            )
        return await select_client(url, skip_verify=skip_verify, policy=policy, override=client).request(
            method, url, **kw
        )

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
    r: httpx.Response | None = None
    raw: Any = None
    status = None
    err: Exception | None = None
    try:
        while True:
            try:
                r = await _once()
                status = r.status_code
                raw = _read_body_capped(r)
                break
            except Exception as e:  # noqa: BLE001 - retry classification below
                if attempt >= max_retries or not _should_retry(e, retry_types, retry_5xx, method, retry_cfg):
                    raise
                delay = min(
                    float(retry_cfg.get("max_delay", 60.0)),
                    float(retry_cfg.get("initial_delay", 1.0)) * (float(retry_cfg.get("backoff_factor", 2.0)) ** attempt),
                )
                if retry_cfg.get("jitter", True):
                    delay *= 0.5 + random.random()
                await asyncio.sleep(delay)
                attempt += 1
    except Exception as e:  # noqa: BLE001 - capture the failed call for the trace, then propagate
        err = e
        raise
    finally:
        latency = int((time.monotonic() - t0) * 1000)
        # Record the FRAMED request + response for the trace (see forge.tracing.tool_io).
        # Runs on success AND failure so a silent run-time 401/403 (e.g. a {{ctx.*}} cookie
        # that never arrived) is visible - the httpx error still carries the response.
        _capture_tool_io(
            cfg, kwargs, method=method, url=url, params=params, headers=headers, cookies=cookies,
            body=body, req=req, response=r, raw=raw, status=status, latency_ms=latency, error=err,
        )
        # Terminal signal paired with the "calling" frame above: tells the client the tool
        # ENDED so it can clear the spinner. `status` is "done" on success, "error" on failure
        # (treat both as "ended"). Only emitted if a "calling" frame was (we're past that point).
        if stream_writer:
            try:
                # On failure the exception fired before `status` was set - read the HTTP status
                # back off the error's response like _capture_tool_io (None on a transport error).
                code = status
                if code is None and err is not None:
                    code = getattr(getattr(err, "response", None), "status_code", None)
                stream_writer({
                    "tool": name,
                    "display_name": display_label,
                    "status": "error" if err is not None else "done",
                    "url": url,
                    "status_code": code,   # HTTP status; None on a transport-level failure
                    "latency_ms": latency,
                })
            except Exception:  # noqa: BLE001
                pass

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
