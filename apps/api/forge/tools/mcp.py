"""MCP tool kind - consume tools from an external MCP server.

Wiring the `mcp` kind unlocks the whole MCP connector ecosystem (GitHub, Slack,
Postgres, Stripe, filesystem, …) without hand-writing each integration. An `McpClient`
row describes the server (http/sse/stdio transport); a tool's config names the
`remote_tool_name` to expose and any `inject_context` keys to fill from the per-user
runtime context (so the model never sets secrets like user_id/api_key).

A server may be authenticated two ways, and they compose:

* `headers_ref`      - a static header secret (an API key / PAT). Simple, shared, no refresh.
* `auth_provider_id` - a full Auth Provider. This is what reaches an OAuth-protected remote
  MCP server: tokens refresh automatically, and a PER-USER provider resolves a different
  credential for each end user, so an agent acts as the calling user rather than through one
  shared workspace token. Provider headers are applied per REQUEST (via httpx.Auth) rather
  than baked into the connection, so a token that rotates mid-session is picked up without
  reconnecting.

MCP discovery is async, so MCP tools are loaded by `load_mcp_tools` from the runtime
assembler (not the sync `materialize_tool` path).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from langchain.tools import ToolRuntime
from sqlalchemy import select

from forge.db.base import SessionLocal
from forge.models import AuthProvider, McpClient
from forge.secrets.store import SecretStore

log = logging.getLogger("forge.mcp")


@dataclass
class _Cached:
    """One pooled MCP connection, plus the lock that serializes (re)building it.

    The lock lives HERE rather than in a side registry keyed by the same string. The cache is
    capped at `_CACHE_MAX`; a separate registry is not, so keying one by a per-user cache key
    would grow a lock per (server, person) for the life of the process - reintroducing, in the
    lock table, exactly the unbounded growth the cap exists to prevent.

    `created` drives expiry (age of the connection); `used` drives eviction (last time anyone
    asked for it). Keeping them apart is what makes eviction least-RECENTLY-USED: a single
    timestamp refreshed on use would never expire a hot connection, and one that is not
    refreshed evicts the busiest connection first.
    """

    created: float
    used: float
    client: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


# Cache MultiServerMCPClient instances per cache key with a TTL so a dead connection or
# an edited server config is eventually re-established without a process restart (audit F12).
# The key is `mcp_client_id` for a shared server, and `mcp_client_id::<auth-dims-hash>` when the
# attached auth provider resolves a DIFFERENT credential per caller - without that suffix one
# caller's authenticated session would be handed to the next caller of the same server.
# `invalidate_client` drops every entry for a server (called when the McpClient row changes);
# `close_all` is called on shutdown.
_CLIENT_CACHE: dict[str, _Cached] = {}
_CACHE_TTL = 300.0  # seconds
# Hard ceiling on live connections. The key carries a per-caller suffix and every catalog MCP
# connector is per-user, so the cache grows with DISTINCT PEOPLE, not distinct servers - a
# project where 500 users each connect Notion would otherwise pin 500 transports for the life
# of the process. The TTL alone doesn't bound it: it only replaces an entry when that same key
# is asked for again, so an idle user's connection is never revisited and never released.
_CACHE_MAX = 64
# A connection leaving the cache is NOT torn down on the spot. `_client_and_tools` hands the
# client back to its caller and `load_mcp_tool` binds agent tools to it, so a client can still
# be in use long after the cache stops tracking it - closing it there aborts a call that is
# already in flight. Retired clients wait out this grace period and are then closed by the
# reaper below.
_CLOSE_GRACE = 30.0
_RETIRED: list[tuple[float, Any]] = []
# How often the reaper wakes. Short relative to `_CLOSE_GRACE` so a transport is closed near its
# deadline rather than a whole interval past it.
_REAP_INTERVAL = 10.0
#: The single background reaper task, or None when nothing is waiting to be closed.
_REAPER: asyncio.Task | None = None


async def _aclose(client: Any) -> None:
    aclose = getattr(client, "aclose", None)
    if aclose is not None:
        with contextlib.suppress(Exception):
            await aclose()


def _retire(client: Any, now: float) -> None:
    """Hand a client over for closing once its grace period expires."""
    if client is not None:
        _RETIRED.append((now + _CLOSE_GRACE, client))
        _ensure_reaper()


def _ensure_reaper() -> None:
    """Make sure the background reaper is running.

    Retirement used to be drained only by `_evict`, which runs only inside a cache BUILD. So a
    shedding round that retired ten transports was closed promptly only if traffic happened to
    miss the cache again: once it settled into a steady state where every call hit a live entry,
    those transports stayed open until some key expired or the process shut down - far past the
    30s grace they were given.

    ONE long-lived task, not a task per retirement. Per-retirement deferral is what the earlier
    implementation did, and `spawn` REJECTS (and closes) its coroutine at an in-flight ceiling,
    which stranded the transport with nothing holding a reference to it.
    """
    global _REAPER
    if _REAPER is not None and not _REAPER.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Retired with no running loop. Nothing is holding the transport open in that case
        # either, and the next retirement inside a loop starts the reaper.
        _REAPER = None
        return
    _REAPER = loop.create_task(_reap_loop())


async def _reap_loop() -> None:
    """Close retired transports near their deadline - off the request path, and off the per-key
    build lock that `_evict` runs under.

    Exits once the queue drains so an idle process isn't holding a timer open; `_retire` starts
    it again. `close_all` cancels it and force-drains whatever is left.
    """
    while _RETIRED:
        await asyncio.sleep(_REAP_INTERVAL)
        await _reap(time.monotonic())


async def _reap(now: float, *, force: bool = False) -> None:
    """Close retired clients whose grace period has passed (all of them when `force`).

    Called by `_reap_loop` on a timer and by `close_all` at shutdown - NOT from the eviction
    path, which must not do network I/O while holding a build lock.
    """
    if not _RETIRED:
        return
    due = [c for deadline, c in _RETIRED if force or now >= deadline]
    _RETIRED[:] = [] if force else [(d, c) for d, c in _RETIRED if now < d]
    for client in due:
        await _aclose(client)


def _evictable() -> list[str]:
    """Keys eligible for eviction: built, and not currently being rebuilt.

    Skipping a locked entry matters - evicting one mid-build would detach the entry its builder
    is about to write into, producing a live client that the cache no longer tracks and that
    nothing will ever close.
    """
    return [k for k, e in _CLIENT_CACHE.items() if e.client is not None and not e.lock.locked()]


def _evict(now: float) -> None:
    """Release connections nobody is coming back for: anything past its TTL, then the
    least-recently-USED entries once the cache is over its ceiling.

    Deliberately SYNCHRONOUS. It runs while `_client_and_tools` holds the entry's build lock, so
    anything awaited here blocks a concurrent caller for that same key - and closing a transport
    is an unbounded network await. Eviction is bookkeeping: it pops entries and hands the clients
    to `_retire`, and the background reaper closes them. That also keeps the grace period
    meaningful, since shedding a connection must never abort an in-flight call.
    """
    shed = 0
    for key in [k for k in _evictable() if now - _CLIENT_CACHE[k].created > _CACHE_TTL]:
        _retire(_CLIENT_CACHE.pop(key).client, now)
        shed += 1
    while len(_CLIENT_CACHE) > _CACHE_MAX:
        candidates = _evictable()
        if not candidates:
            break  # everything left is mid-build; the next build will trim instead
        oldest = min(candidates, key=lambda k: _CLIENT_CACHE[k].used)
        _retire(_CLIENT_CACHE.pop(oldest).client, now)
        shed += 1
    if shed:
        log.debug("mcp cache: retired %d idle connection(s)", shed)


async def invalidate_client(client_id: str) -> None:
    """Drop every cached connection for a server so the next run reconnects with the latest
    config - including all per-caller variants, which share the `<client_id>::` prefix.

    Closes what it drops IMMEDIATELY, without the retirement grace: this runs when the server's
    row was edited or deleted, so the old connection points at configuration that no longer
    exists and finishing an in-flight call on it is not something to protect.
    """
    for key in [k for k in _CLIENT_CACHE if k == client_id or k.startswith(client_id + "::")]:
        entry = _CLIENT_CACHE.pop(key, None)
        if entry is not None:
            await _aclose(entry.client)


async def close_all() -> None:
    """Best-effort close of every cached MCP client (transports/subprocesses) on shutdown."""
    global _REAPER
    if _REAPER is not None:
        _REAPER.cancel()
        with contextlib.suppress(BaseException):
            await _REAPER
        _REAPER = None
    for entry in list(_CLIENT_CACHE.values()):
        await _aclose(entry.client)
    _CLIENT_CACHE.clear()
    # `force` ignores the grace period: the process is going away, so there is no in-flight call
    # left to protect and an unclosed transport would just leak into shutdown.
    await _reap(time.monotonic(), force=True)


class McpUnavailable(RuntimeError):
    pass


def _require_adapters():
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:  # pragma: no cover - optional extra
        raise McpUnavailable(
            "mcp tools need `langchain-mcp-adapters` (pip install -e '.[mcp]')."
        ) from e
    return MultiServerMCPClient


async def _validate_mcp_url(url: str | None) -> None:
    """Screen an external MCP server URL through the SSRF egress guard before connecting.
    REST/GraphQL/SQL tools already do this; the MCP client URL was handed straight to the
    transport, so a project editor could point it at 169.254.169.254 or an internal service
    (and any secret headers would be sent there). Enforce the same default-deny here."""
    from forge.util.ssrf import EgressPolicy, validate_url

    if not url:
        raise McpUnavailable("MCP server URL is required for http/sse transport")
    await validate_url(url, EgressPolicy.from_settings())


async def _load_provider(tenant_id: str, provider_id: str) -> AuthProvider | None:
    async with SessionLocal() as session:
        return (await session.execute(
            select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.id == provider_id)
        )).scalar_one_or_none()


class _ProviderAuth(httpx.Auth):
    """Attach an Auth Provider's resolved headers to every request to an MCP server.

    Resolution happens per REQUEST (the resolver has its own TTL cache, so this is a dict
    lookup in the common case). That is what lets a rotated OAuth token take effect without
    tearing down the MCP session, and what makes a per-user provider resolve the CALLER's
    credential rather than whichever one happened to be live when the session opened.

    On a 401/403 the cached auth is invalidated and the request is retried exactly once - the
    same invalidate-on-401 contract the REST tool follows. One retry, not a loop: if a freshly
    minted token is also rejected, the credential is wrong and retrying would only amplify a
    failing call into several.
    """

    requires_response_body = False

    def __init__(self, resolver, *, tenant_id: str, project_id: str, provider_id: str, context: dict) -> None:
        self._resolver = resolver
        self._tenant_id = tenant_id
        self._project_id = project_id
        self._provider_id = provider_id
        self._context = context or {}

    async def _apply(self, request: httpx.Request, *, force: bool) -> None:
        resolved = await self._resolver.resolve(
            tenant_id=self._tenant_id, project_id=self._project_id,
            provider_id=self._provider_id, context=self._context, force=force,
        )
        for k, v in resolved.headers.items():
            request.headers[k] = v
        if resolved.cookies:
            jar = "; ".join(f"{k}={v}" for k, v in resolved.cookies.items())
            existing = request.headers.get("Cookie")
            request.headers["Cookie"] = f"{existing}; {jar}" if existing else jar
        if resolved.params:
            request.url = request.url.copy_merge_params(resolved.params)

    async def async_auth_flow(self, request: httpx.Request):
        # Snapshot the whole pre-auth request shape so the retry re-applies onto a CLEAN one.
        # Params are MERGED and the cookie jar is CONCATENATED, so applying twice to the same
        # mutated request would send `?api_key=X&api_key=X` and `Cookie: s=1; s=1`, which some
        # servers reject outright - turning a recoverable 401 into a hard failure. Headers do
        # overwrite, but only the ones the SECOND resolve returns: restoring them wholesale
        # means a provider that changed shape between the attempts (a renamed header_name)
        # can't leave its first-attempt header behind alongside the new one.
        original_url = request.url
        original_headers = request.headers.copy()
        await self._apply(request, force=False)
        response = yield request
        if response.status_code in (401, 403):
            request.url = original_url
            request.headers = original_headers
            await self._apply(request, force=True)
            yield request


def auth_cache_dims(cfg: dict) -> list[str]:
    """The run-context keys that make one caller's resolved credential different from another's.

    This MUST stay a superset of what `AuthResolver.resolve` varies its own cache on, because the
    connection pooled here OUTLIVES the request that built it: the `_ProviderAuth` attached to a
    pooled client captures the context of whoever opened it, and every later caller sharing the
    cache key is authenticated through that snapshot. Any dimension the resolver treats as
    caller-specific and this does not is a credential handed to the wrong person.

    Two such dimensions today:
      * per_user_context_keys - a stored per-user connection (end_user_id).
      * token_ctx_key         - an INLINE token forwarded on the run (X-Forge-Context), with a
                                deployment-wide fallback. The resolver added this to its key for
                                precisely this reason; omitting it here would defeat that.
    """
    from forge.config import settings

    dims = list(cfg.get("per_user_context_keys") or [])
    ctx_key = cfg.get("token_ctx_key") or settings.default_token_ctx_key
    if ctx_key and ctx_key not in dims:
        dims.append(ctx_key)
    return dims


async def _auth_for(client_row: McpClient, tenant_id: str, project_id: str, context: dict | None):
    """(httpx.Auth | None, cache-key suffix) for a server's attached auth provider."""
    if not getattr(client_row, "auth_provider_id", None):
        return None, ""
    provider = await _load_provider(tenant_id, client_row.auth_provider_id)
    if provider is None:
        log.warning("MCP server %s references missing auth provider %s", client_row.name, client_row.auth_provider_id)
        return None, ""
    from forge.auth_providers.resolver import AuthResolver

    context = context or {}
    # Only a provider that resolves a DIFFERENT credential per caller needs a per-caller
    # connection; a genuinely shared one keeps a single pooled session for the whole project
    # (which is the overwhelmingly common case).
    dims = auth_cache_dims(provider.config or {})
    suffix = ""
    if dims:
        fingerprint = "|".join(f"{k}={context.get(k)}" for k in sorted(dims))
        suffix = "::" + hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
    auth = _ProviderAuth(
        AuthResolver(), tenant_id=tenant_id, project_id=project_id,
        provider_id=provider.id, context=context,
    )
    return auth, suffix


async def _connection_for(client_row: McpClient, tenant_id: str, project_id: str,
                          context: dict | None = None, auth: httpx.Auth | None = None) -> dict:
    """Build the langchain-mcp-adapters connection dict for a server.

    `auth` lets a caller that has ALREADY resolved the provider hand it in - `_auth_for` does a
    DB read, and computing the cache-key suffix plus building the connection would otherwise
    query the same AuthProvider row twice on the path every agent turn takes.
    """
    from forge.config import settings

    transport = client_row.transport or "streamable_http"
    if transport in ("http", "streamable_http"):
        await _validate_mcp_url(client_row.url)
        conn: dict[str, Any] = {"url": client_row.url, "transport": "streamable_http"}
    elif transport == "sse":
        await _validate_mcp_url(client_row.url)
        conn = {"url": client_row.url, "transport": "sse"}
    elif transport == "stdio":
        # stdio launches a LOCAL PROCESS -> arbitrary command execution on the API host. Gate it
        # behind an explicit deployment flag (default off, so it can't be enabled by any editor in
        # a multi-tenant install) and an optional command allow-list.
        if not settings.enable_mcp_stdio:
            raise McpUnavailable(
                "MCP stdio transport is disabled. It launches a local process (arbitrary command "
                "execution); enable FORGE_ENABLE_MCP_STDIO=true only on a trusted single-tenant install."
            )
        allowed = settings.mcp_stdio_allowed_commands
        if allowed and (client_row.command or "") not in allowed:
            raise McpUnavailable(f"MCP stdio command {client_row.command!r} is not in the allowed list.")
        args = client_row.args or {}
        conn = {"command": client_row.command, "args": args.get("args", []) if isinstance(args, dict) else args, "transport": "stdio"}
    else:
        raise McpUnavailable(f"unsupported MCP transport {transport!r}")
    if client_row.headers_ref:
        try:
            headers = await SecretStore().read_ref(tenant_id=tenant_id, project_id=project_id, ref=client_row.headers_ref)
            if isinstance(headers, dict):
                conn["headers"] = headers
        except Exception:  # noqa: BLE001 - missing headers secret => connect without
            pass
    # stdio has no HTTP layer to attach auth to; a provider on a stdio server is a config
    # mistake rather than something to silently half-apply.
    if transport != "stdio":
        if auth is None:
            auth, _suffix = await _auth_for(client_row, tenant_id, project_id, context)
        if auth is not None:
            conn["auth"] = auth
    return conn


async def _client_and_tools(client_row: McpClient, tenant_id: str, project_id: str,
                            context: dict | None = None):
    MultiServerMCPClient = _require_adapters()
    now = time.monotonic()
    auth, suffix = await _auth_for(client_row, tenant_id, project_id, context)
    key = client_row.id + suffix
    # Claim the slot before any await, so two concurrent misses cannot each create an entry (and
    # therefore each build a client, one of which is silently replaced and never closed). A plain
    # get-then-set with no await between them is atomic under asyncio; the lock inside the entry
    # is then the same object for both.
    entry = _CLIENT_CACHE.get(key)
    if entry is None:
        entry = _CLIENT_CACHE[key] = _Cached(created=0.0, used=now)

    def _fresh() -> bool:
        return entry.client is not None and (time.monotonic() - entry.created) <= _CACHE_TTL

    if _fresh():
        entry.used = now
    else:
        async with entry.lock:
            now = time.monotonic()
            if _fresh():
                entry.used = now
            else:
                # Retire (don't close) the expired connection: a caller from before the TTL
                # elapsed may still be running against it.
                _retire(entry.client, now)
                entry.client = None
                try:
                    # Reuse the provider we just resolved instead of making _connection_for
                    # re-read it.
                    conn = await _connection_for(client_row, tenant_id, project_id, context, auth=auth)
                    entry.client = MultiServerMCPClient({client_row.name: conn})
                except BaseException:
                    # Don't leave an empty entry parked in the cache: it can never be evicted
                    # (nothing to retire) yet still counts against the ceiling.
                    if _CLIENT_CACHE.get(key) is entry:
                        del _CLIENT_CACHE[key]
                    raise
                entry.created = entry.used = now
                if _CLIENT_CACHE.get(key) is not entry:
                    # `invalidate_client` ran while we were connecting and dropped this entry
                    # (it does not wait for a build - the config it was building against is
                    # already gone). Hand this call the client we just made, but retire it so
                    # the detached transport is still closed rather than orphaned.
                    _retire(entry.client, now)
                else:
                    _evict(now)
    client = entry.client
    tools = await client.get_tools()
    return client, tools


async def discover_tools(client_row: McpClient, tenant_id: str, project_id: str,
                         context: dict | None = None) -> list[dict]:
    """List the tools an MCP server exposes - [{name, description}].

    Connects fresh (not via the execution cache) so the result always reflects the
    current McpClient config, and drops any stale cached client so the next run
    reconnects with the latest settings. Raises McpUnavailable / connection errors.
    """
    await invalidate_client(client_row.id)
    MultiServerMCPClient = _require_adapters()
    conn = await _connection_for(client_row, tenant_id, project_id, context)
    client = MultiServerMCPClient({client_row.name: conn})
    # This client is deliberately NOT cached, so nothing else will ever close it. Discovery runs
    # on every connect callback and every "Refresh actions" click, so dropping it on return
    # would leak one transport per click.
    try:
        tools = await client.get_tools()
    finally:
        await _aclose(client)
    return [{"name": t.name, "description": (getattr(t, "description", "") or "").strip()} for t in tools]


def describe_mcp_error(e: BaseException) -> str:
    """A message that names what actually failed.

    The MCP client runs its transport inside an anyio task group, so EVERY failure - a 401 from
    the server, a DNS miss, a TLS error - reaches the caller wrapped in an ExceptionGroup whose
    str() is the famously uninformative "unhandled errors in a TaskGroup (1 sub-exception)".
    Printing that in the UI tells a user nothing and tells us nothing either, so unwrap to the
    leaf exceptions and name them.
    """
    leaves: list[str] = []

    def _walk(err: BaseException, depth: int = 0) -> None:
        inner = getattr(err, "exceptions", None)
        if inner and depth < 5:
            for sub in inner:
                _walk(sub, depth + 1)
            return
        text = str(err).strip()
        leaves.append(f"{type(err).__name__}: {text}" if text else type(err).__name__)

    _walk(e)
    # Deduplicate: a task group commonly reports the same underlying error from several tasks.
    unique = list(dict.fromkeys(leaves))
    return "; ".join(unique[:3]) or str(e) or type(e).__name__


async def server_tools(client_row: McpClient, tenant_id: str, project_id: str,
                       context: dict | None = None) -> list:
    """Native LangChain tools a server exposes, minus the ones toggled off (disabled_tools).
    Used to attach a whole MCP server's tools to an agent. `context` carries the run's per-user
    dims so a per-user auth provider resolves THIS caller's credential."""
    _client, tools = await _client_and_tools(client_row, tenant_id, project_id, context)
    disabled = set(getattr(client_row, "disabled_tools", None) or [])
    return [t for t in tools if t.name not in disabled]


def auth_context_from(ctx) -> dict:
    """The per-user dims an MCP auth provider keys on, read off a CompileContext.

    Mirrors the lane order the REST tool uses (tools/rest.py): injected run context first, then
    the run's end_user identity last so it is authoritative and can't be shadowed by a value a
    caller injected."""
    eu = getattr(ctx, "end_user", None)
    return {
        **(getattr(ctx, "run_context", None) or {}),
        "end_user": eu,
        "end_user_id": eu.get("id") if isinstance(eu, dict) else None,
        "end_user_email": eu.get("email") if isinstance(eu, dict) else None,
    }


def _wrap_with_context_injection(tool, inject_keys: list[str]):
    """Wrap an MCP StructuredTool so `inject_keys` are filled from runtime.context
    (per-user secrets the widget/channel supplies) instead of from the model."""
    from langchain_core.tools import StructuredTool

    underlying = tool

    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        context = getattr(runtime, "context", None) or {}
        for k in inject_keys or []:
            if k in context:
                kwargs[k] = context[k]
        return await underlying.ainvoke(kwargs)

    return StructuredTool.from_function(
        coroutine=_call, name=underlying.name, description=underlying.description,
        args_schema=underlying.args_schema,
    )


async def load_mcp_tool(cfg: dict, ctx) -> Any:
    """Resolve a single `mcp`-kind tool config to a runnable tool (async)."""
    async with SessionLocal() as s:
        row = (
            await s.execute(
                select(McpClient).where(
                    McpClient.tenant_id == ctx.tenant_id, McpClient.id == cfg["mcp_client_id"]
                )
            )
        ).scalar_one_or_none()
    if row is None:
        raise McpUnavailable(f"MCP client {cfg.get('mcp_client_id')!r} not found")
    _client, tools = await _client_and_tools(row, ctx.tenant_id, ctx.project_id, auth_context_from(ctx))
    name = cfg["remote_tool_name"]
    match = next((t for t in tools if t.name == name), None)
    if match is None:
        raise McpUnavailable(f"remote tool {name!r} not exposed by MCP server {row.name!r}")
    inject = cfg.get("inject_context") or []
    return _wrap_with_context_injection(match, inject) if inject else match


def build_mcp_tool(cfg: dict, ctx):
    # MCP discovery is async; the runtime assembler calls load_mcp_tool instead.
    raise McpUnavailable("mcp tools are loaded asynchronously via load_mcp_tool (runtime assembler).")
