"""GraphQL tool — a REST POST with a query + variables, then response projection."""

# NO `from __future__ import annotations` here — see rest.py: the runtime-injection
# machinery needs the real ToolRuntime class on inspect.signature, not a string.
import time
from typing import Any

import httpx
from langchain.tools import ToolRuntime

from forge.tools.projection import project_response
from forge.tools.rest import _build_structured_tool, _redirect_info, _tool_return, build_args_schema
from forge.util.http import shared_async_client
from forge.util.ssrf import guarded_request, validate_url


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
    await validate_url(cfg["endpoint"], egress_policy)
    client = client or shared_async_client()
    kw = dict(
        headers=headers or None, params=params or None, cookies=cookies or None,
        json={"query": cfg["query"], "variables": variables}, timeout=cfg.get("timeout_seconds", 30),
    )
    t0 = time.monotonic()
    try:
        if follow:
            # Chase redirects SSRF-safely (each hop re-validated) rather than via httpx.
            r = await guarded_request(client, "POST", cfg["endpoint"], policy=egress_policy, follow_redirects=True, **kw)
        else:
            r = await client.post(cfg["endpoint"], **kw)
        status = r.status_code
        if 300 <= status < 400 and not follow:
            # Redirect we didn't follow: body is typically empty; the target is in `redirect`.
            raw: Any = r.text
        else:
            r.raise_for_status()
            try:
                raw = r.json()
            except Exception:  # noqa: BLE001 - non-JSON body (e.g. a followed redirect to an HTML page)
                raw = r.text
    finally:
        latency = int((time.monotonic() - t0) * 1000)
    return {
        "raw": raw, "projected": project_response(raw, cfg.get("response")), "status": status,
        "latency_ms": latency, "final_url": str(r.url), "redirect": _redirect_info(r, follow),
    }


def build_graphql_tool(cfg: dict, ctx):
    args_schema = build_args_schema(cfg, fields_key="variables")

    # Bare ToolRuntime annotation + None default — see rest.py for why (zero-arg tools).
    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        context = getattr(runtime, "context", None) or {}
        res = await execute_graphql(
            cfg, kwargs, tenant_id=ctx.tenant_id, project_id=ctx.project_id,
            context=context, auth_resolver=ctx.auth_resolver,
            egress_policy=getattr(ctx, "egress_policy", None),
        )
        return _tool_return(res, cfg)

    return _build_structured_tool(_call, cfg, args_schema)
