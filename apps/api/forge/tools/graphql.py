"""GraphQL tool - a REST POST with a query + variables, then response projection."""

# NO `from __future__ import annotations` here - see rest.py: the runtime-injection
# machinery needs the real ToolRuntime class on inspect.signature, not a string.
import time
from typing import Any

import httpx
from langchain.tools import ToolRuntime

from forge.tools.projection import project_response
from forge.tools.rest import (
    _build_structured_tool,
    _read_body_capped,
    _redirect_info,
    _tool_return,
    build_args_schema,
)
from forge.util.http import select_client
from forge.util.ssrf import EgressPolicy, guarded_request, validate_url


class GraphQLToolError(RuntimeError):
    """A GraphQL response that transported fine (HTTP 200) but carried application errors."""


async def execute_graphql(
    cfg: dict, kwargs: dict, *, tenant_id: str, project_id: str, context: dict | None = None,
    auth_resolver=None, client: httpx.AsyncClient | None = None, stream_writer=None,
    egress_policy=None,
) -> dict:
    variables = {k: v for k, v in kwargs.items() if v is not None}
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    cookies: dict[str, str] = {}
    if cfg.get("auth_provider_id") and auth_resolver:
        auth = await auth_resolver.resolve(
            tenant_id=tenant_id, project_id=project_id, provider_id=cfg["auth_provider_id"], context=context or {},
        )
        headers.update(auth.headers)
        params.update(auth.params)
        cookies.update(auth.cookies)

    follow = bool(cfg.get("follow_redirects", False))
    policy = egress_policy or EgressPolicy.from_settings()
    await validate_url(cfg["endpoint"], policy)
    # `tls_skip_verify` disables cert verification only for a host on the egress allow_private_hosts
    # list (select_client enforces the gate); guarded_request re-applies it per redirect hop. An
    # explicit `client` (tests) always wins.
    skip_verify = bool(cfg.get("tls_skip_verify"))
    # A GraphQL request may name the operation to run when the document defines several
    # (operationName); include it only when configured.
    payload: dict[str, Any] = {"query": cfg["query"], "variables": variables}
    if cfg.get("operation_name"):
        payload["operationName"] = cfg["operation_name"]
    kw = dict(
        headers=headers or None, params=params or None, cookies=cookies or None,
        json=payload, timeout=cfg.get("timeout_seconds", 30),
    )
    t0 = time.monotonic()
    try:
        if follow:
            # Chase redirects SSRF-safely (each hop re-validated AND its client re-selected) rather than via httpx.
            r = await guarded_request(
                client, "POST", cfg["endpoint"], policy=policy, skip_verify=skip_verify, follow_redirects=True, **kw
            )
        else:
            r = await select_client(cfg["endpoint"], skip_verify=skip_verify, policy=policy, override=client).post(
                cfg["endpoint"], **kw
            )
        status = r.status_code
        if 300 <= status < 400 and not follow:
            # Redirect we didn't follow: body is typically empty; the target is in `redirect`.
            raw: Any = r.text
        else:
            r.raise_for_status()
            raw = _read_body_capped(r)  # shared size-guarded JSON/text read (rest.py)
    finally:
        latency = int((time.monotonic() - t0) * 1000)
    # GraphQL signals failure in-band: HTTP 200 with a non-empty `errors` array. A response that
    # produced ONLY errors (data null/absent) is a failure, not success - surfacing the empty body
    # as a normal result would silently hide the error from the model/operator. A PARTIAL result
    # (data present alongside errors) is valid GraphQL and passes through unchanged.
    if isinstance(raw, dict) and raw.get("errors") and raw.get("data") is None:
        messages = "; ".join(
            str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in raw["errors"][:5]
        )
        raise GraphQLToolError(f"GraphQL query returned errors: {messages}")
    return {
        "raw": raw, "projected": project_response(raw, cfg.get("response")), "status": status,
        "latency_ms": latency, "final_url": str(r.url), "redirect": _redirect_info(r, follow),
    }


def build_graphql_tool(cfg: dict, ctx):
    args_schema = build_args_schema(cfg, fields_key="variables")

    # Bare ToolRuntime annotation + None default - see rest.py for why (zero-arg tools).
    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        # Same three-lane context as the REST tool (kept in sync): per-run injected context,
        # LangGraph runtime context, then the authoritative end_user identity. Reaches the auth
        # resolver (per_user_context_keys / csrf_session) for on-behalf-of GraphQL calls.
        context = {
            **(getattr(ctx, "run_context", None) or {}),
            **(getattr(runtime, "context", None) or {}),
            "end_user": getattr(ctx, "end_user", None),
        }
        res = await execute_graphql(
            cfg, kwargs, tenant_id=ctx.tenant_id, project_id=ctx.project_id,
            context=context, auth_resolver=ctx.auth_resolver,
            egress_policy=getattr(ctx, "egress_policy", None),
        )
        return _tool_return(res, cfg)

    return _build_structured_tool(_call, cfg, args_schema)
