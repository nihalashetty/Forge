"""MCP client-side auth: an Auth Provider attached to an external MCP server.

This is what makes an OAuth-protected remote MCP server (mcp.slack.com and friends) reachable.
The properties that matter, and are easy to get wrong:

  * provider headers are applied per REQUEST, so a refreshed token lands without reconnecting,
  * a 401 forces exactly one re-resolve and retry (not a loop),
  * a PER-USER provider gets its OWN pooled connection per end user - the bug where one user's
    authenticated session is handed to the next caller is the whole reason the cache key has a
    user-dims suffix.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from forge.db.base import SessionLocal
from forge.models import McpClient
from forge.services.auth_providers import AuthProviderService
from forge.tools import mcp as mcp_mod


async def _provider(tenant, project, *, per_user: bool, name="srv"):
    async with SessionLocal() as s:
        cfg = {"token_ref": "secret://proj/tok", "header_name": "Authorization", "prefix": "Bearer "}
        if per_user:
            cfg["per_user_context_keys"] = ["end_user_id"]
        ap = await AuthProviderService.create(s, tenant, project, name=name, kind="bearer", config=cfg)
        return ap.id


async def _client(tenant, project, ap_id=None, transport="streamable_http"):
    async with SessionLocal() as s:
        row = McpClient(tenant_id=tenant, project_id=project, name="srv", transport=transport,
                        url="https://mcp.example/mcp", auth_provider_id=ap_id)
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row


class _StubResolver:
    """Stands in for AuthResolver: records each resolve and hands back a token."""

    def __init__(self, token="tok-1"):
        self.calls: list[dict] = []
        self.token = token

    async def resolve(self, *, tenant_id, project_id, provider_id, context=None, force=False):
        self.calls.append({"context": dict(context or {}), "force": force})
        from forge.auth_providers.resolver import ResolvedAuth
        return ResolvedAuth(headers={"Authorization": f"Bearer {self.token}"})


async def test_no_auth_provider_means_no_auth_on_the_connection():
    row = await _client("t_ma_n", "p_ma_n")
    conn = await mcp_mod._connection_for(row, "t_ma_n", "p_ma_n")
    assert "auth" not in conn


async def test_auth_provider_attaches_an_httpx_auth_to_the_connection():
    tenant, project = "t_ma_a", "p_ma_a"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id)
    conn = await mcp_mod._connection_for(row, tenant, project)
    assert isinstance(conn.get("auth"), httpx.Auth)


async def test_stdio_transport_never_gets_http_auth():
    """A provider on a stdio server is a config mistake; half-applying it would be worse."""
    tenant, project = "t_ma_s", "p_ma_s"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id, transport="stdio")
    row.command = "echo"
    from forge.config import settings
    settings.enable_mcp_stdio = True
    try:
        conn = await mcp_mod._connection_for(row, tenant, project)
    finally:
        settings.enable_mcp_stdio = False
    assert "auth" not in conn


async def test_provider_auth_applies_headers_per_request():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")

    flow = auth.async_auth_flow(request)
    sent = await flow.__anext__()
    assert sent.headers["Authorization"] == "Bearer tok-1"

    # A rotated token is picked up on the NEXT request without rebuilding the connection.
    resolver.token = "tok-2"
    request2 = httpx.Request("POST", "https://mcp.example/mcp")
    flow2 = auth.async_auth_flow(request2)
    sent2 = await flow2.__anext__()
    assert sent2.headers["Authorization"] == "Bearer tok-2"


async def test_401_forces_one_refresh_and_retries_once():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")

    flow = auth.async_auth_flow(request)
    await flow.__anext__()
    resolver.token = "fresh"
    retried = await flow.asend(httpx.Response(401, request=request))
    assert retried.headers["Authorization"] == "Bearer fresh"
    assert resolver.calls[-1]["force"] is True, "the retry must bypass the resolver's cache"

    # Exactly one retry: a second 401 ends the flow rather than looping.
    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(401, request=request))


async def test_success_does_not_re_resolve():
    resolver = _StubResolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p", provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example/mcp")
    flow = auth.async_auth_flow(request)
    await flow.__anext__()
    with pytest.raises(StopAsyncIteration):
        await flow.asend(httpx.Response(200, request=request))
    assert len(resolver.calls) == 1


@pytest.fixture
def no_default_ctx_key(monkeypatch):
    """`FORGE_DEFAULT_TOKEN_CTX_KEY` is a deployment-wide setting read from the developer's own
    .env, and it legitimately makes EVERY provider caller-specific. Pin it off so these tests
    assert the shared-pooling case rather than whatever the local machine is configured for."""
    from forge.config import settings

    monkeypatch.setattr(settings, "default_token_ctx_key", "")


async def test_shared_provider_pools_one_connection_for_everyone(no_default_ctx_key):
    tenant, project = "t_ma_sh", "p_ma_sh"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id)
    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u2"})
    assert s1 == s2 == ""


async def test_an_inline_run_token_gets_its_own_pooled_connection(no_default_ctx_key):
    """A provider can take its token INLINE from the run context (token_ctx_key) instead of from
    a stored per-user connection. The resolver varies its own cache on that key; if the MCP cache
    key ignores it, the first caller's connection - carrying the first caller's token in its
    attached httpx.Auth - is pooled and handed to everyone else for the whole TTL."""
    tenant, project = "t_ma_inline", "p_ma_inline"
    async with SessionLocal() as s:
        ap = await AuthProviderService.create(
            s, tenant, project, name="inline", kind="bearer",
            config={"token_ref": "secret://proj/tok", "token_ctx_key": "user_token"},
        )
        ap_id = ap.id
    row = await _client(tenant, project, ap_id)

    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"user_token": "tok-alice"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"user_token": "tok-bob"})
    _c, s3 = await mcp_mod._auth_for(row, tenant, project, {"user_token": "tok-alice"})

    assert s1 and s1 != s2, "two callers' inline tokens must not share one pooled connection"
    assert s1 == s3, "the same inline token should reuse its own pooled connection"


async def test_the_deployment_wide_inline_token_key_also_splits_the_pool(monkeypatch):
    """token_ctx_key has a deployment-wide fallback, and the resolver honours it. So must this."""
    from forge.config import settings

    monkeypatch.setattr(settings, "default_token_ctx_key", "user_token")
    tenant, project = "t_ma_dflt", "p_ma_dflt"
    ap_id = await _provider(tenant, project, per_user=False)
    row = await _client(tenant, project, ap_id)

    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"user_token": "tok-alice"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"user_token": "tok-bob"})
    assert s1 and s1 != s2


def test_auth_cache_dims_covers_everything_the_resolver_varies_on():
    """A regression guard with teeth: the resolver builds its cache key from
    per_user_context_keys + token_ctx_key. If a future dimension is added there and not here,
    the pooled MCP connection starts serving one caller's credential to another."""
    from forge.config import settings

    cfg = {"per_user_context_keys": ["end_user_id"], "token_ctx_key": "user_token"}
    dims = set(mcp_mod.auth_cache_dims(cfg))

    per_user = cfg.get("per_user_context_keys", [])
    effective_ctx_key = cfg.get("token_ctx_key") or settings.default_token_ctx_key
    resolver_dims = set([*per_user, effective_ctx_key] if effective_ctx_key else per_user)

    assert resolver_dims <= dims, f"MCP pooling ignores caller dimension(s) {resolver_dims - dims}"


async def test_per_user_provider_gets_a_distinct_connection_per_end_user():
    tenant, project = "t_ma_pu", "p_ma_pu"
    ap_id = await _provider(tenant, project, per_user=True)
    row = await _client(tenant, project, ap_id)
    _a, s1 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    _b, s2 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u2"})
    _c, s3 = await mcp_mod._auth_for(row, tenant, project, {"end_user_id": "u1"})
    assert s1 and s2 and s1 != s2, "two end users must not share one authenticated MCP session"
    assert s1 == s3, "the same end user must reuse their own pooled session"


async def test_missing_provider_degrades_to_no_auth_rather_than_failing():
    tenant, project = "t_ma_m", "p_ma_m"
    row = await _client(tenant, project, "does-not-exist")
    auth, suffix = await mcp_mod._auth_for(row, tenant, project, {})
    assert auth is None and suffix == ""


class _FakeClient:
    """Stands in for a MultiServerMCPClient so we can see whether it gets closed."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _seed(key: str, client, *, created: float, used: float | None = None) -> None:
    mcp_mod._CLIENT_CACHE[key] = mcp_mod._Cached(
        created=created, used=used if used is not None else created, client=client
    )


async def _reset_cache() -> None:
    mcp_mod._CLIENT_CACHE.clear()
    mcp_mod._RETIRED.clear()
    # Retiring a client starts the background reaper. Leaving it sleeping on a loop that pytest
    # is about to close produces a "Task was destroyed but it is pending" warning and lets one
    # test's reaper drain the next test's queue.
    if mcp_mod._REAPER is not None:
        mcp_mod._REAPER.cancel()
        with contextlib.suppress(BaseException):
            await mcp_mod._REAPER
        mcp_mod._REAPER = None


@pytest.fixture(autouse=True)
async def _clean_cache():
    """The cache, the retirement queue and the reaper task are module globals; a test that
    leaves entries in any of them changes what the next test's eviction sees."""
    await _reset_cache()
    yield
    await _reset_cache()


async def test_invalidate_client_drops_and_closes_every_per_user_variant():
    """Popping an entry without closing it strands a socket nothing will ever reclaim.

    This path closes IMMEDIATELY rather than retiring: it runs when the server row was edited or
    deleted, so the connection points at config that no longer exists.
    """
    mine = {k: _FakeClient() for k in ("cid", "cid::abc", "cid::def")}
    other = _FakeClient()
    for k, c in mine.items():
        _seed(k, c, created=0.0)
    _seed("other", other, created=0.0)

    await mcp_mod.invalidate_client("cid")

    assert set(mcp_mod._CLIENT_CACHE) == {"other"}
    assert all(c.closed for c in mine.values()), "dropped connections must be closed"
    assert not other.closed, "an unrelated server's connection must survive"


async def test_cache_is_bounded_so_per_user_keys_cannot_grow_without_limit():
    """The cache key carries a per-caller suffix and every catalog MCP connector is per-user, so
    the cache grows with distinct PEOPLE. Without a ceiling a busy project pins one live
    transport per user for the lifetime of the process."""
    import time

    now = time.monotonic()
    clients = []
    for i in range(mcp_mod._CACHE_MAX + 10):
        c = _FakeClient()
        clients.append(c)
        # Ascending `used` so "least recently used" is unambiguous.
        _seed(f"cid::{i:04d}", c, created=now, used=now + i)

    mcp_mod._evict(now + 1)

    assert len(mcp_mod._CLIENT_CACHE) == mcp_mod._CACHE_MAX, "the ceiling is honoured immediately"
    # Shed connections are NOT closed on the spot - a caller may still be running against one.
    assert not any(c.closed for c in clients), "eviction must not abort an in-flight call"

    await mcp_mod._reap(now + 1 + mcp_mod._CLOSE_GRACE)
    evicted = [c for c in clients if c.closed]
    assert len(evicted) == 10
    assert clients[:10] == evicted, "eviction should drop the least recently used entries first"


async def test_eviction_is_least_recently_used_not_oldest_connection():
    """The hot shared connection is the one built first and used constantly. Evicting by build
    time would shed it before any of the idle per-user connections that displaced it."""
    import time

    now = time.monotonic()
    hot = _FakeClient()
    _seed("shared", hot, created=now, used=now + 10_000)  # built first, used most recently
    idle = []
    for i in range(mcp_mod._CACHE_MAX):
        c = _FakeClient()
        idle.append(c)
        _seed(f"cid::{i:04d}", c, created=now + 1 + i, used=now + i)

    mcp_mod._evict(now + 1)

    assert "shared" in mcp_mod._CLIENT_CACHE, "the busiest connection must not be evicted first"
    assert "cid::0000" not in mcp_mod._CLIENT_CACHE


async def test_expired_entries_are_retired_and_then_closed():
    import time

    stale = _FakeClient()
    fresh = _FakeClient()
    now = time.monotonic()
    _seed("a", stale, created=now - mcp_mod._CACHE_TTL - 1)
    _seed("b", fresh, created=now)

    mcp_mod._evict(now)

    assert set(mcp_mod._CLIENT_CACHE) == {"b"}
    assert not stale.closed, "the grace period lets an in-flight call finish"
    await mcp_mod._reap(now + mcp_mod._CLOSE_GRACE)
    assert stale.closed and not fresh.closed


async def test_a_connection_being_rebuilt_is_never_evicted():
    """Evicting a locked entry detaches it from the cache while its builder is still writing
    into it - producing a live client nothing tracks and nothing will ever close."""
    import time

    now = time.monotonic()
    building = mcp_mod._Cached(created=0.0, used=now)
    await building.lock.acquire()
    try:
        mcp_mod._CLIENT_CACHE["mid-build"] = building
        for i in range(mcp_mod._CACHE_MAX + 5):
            _seed(f"cid::{i:04d}", _FakeClient(), created=now, used=now + i)

        mcp_mod._evict(now + 1)

        assert mcp_mod._CLIENT_CACHE.get("mid-build") is building
    finally:
        building.lock.release()


async def test_invalidating_a_server_mid_build_does_not_orphan_the_new_connection():
    """`invalidate_client` doesn't wait for an in-flight build, so the builder can finish into an
    entry that is no longer in the cache. Nothing would then hold that transport, and nothing
    would ever close it."""
    import time

    tenant, project = "t_ma_midb", "p_ma_midb"
    row = await _client(tenant, project)
    built = _FakeClient()

    class _Adapters:
        def __call__(self, _servers):
            return built

    async def _connection(*a, **kw):
        # The server row is deleted (and the cache invalidated) while we are connecting.
        await mcp_mod.invalidate_client(row.id)
        return {"url": "https://mcp.example/mcp", "transport": "streamable_http"}

    async def _no_tools():
        return []

    built.get_tools = _no_tools  # type: ignore[attr-defined]
    real_adapters, real_connection = mcp_mod._require_adapters, mcp_mod._connection_for
    mcp_mod._require_adapters = lambda: _Adapters()  # type: ignore[assignment]
    mcp_mod._connection_for = _connection  # type: ignore[assignment]
    try:
        await mcp_mod._client_and_tools(row, tenant, project, {})
    finally:
        # Restore, don't delete: these are the module's own functions, and `del` would remove
        # them outright for every test that runs after this one.
        mcp_mod._require_adapters = real_adapters  # type: ignore[assignment]
        mcp_mod._connection_for = real_connection  # type: ignore[assignment]

    assert row.id not in mcp_mod._CLIENT_CACHE, "the invalidation stands"
    assert not built.closed, "the caller's own call must still be able to finish"
    await mcp_mod._reap(time.monotonic() + mcp_mod._CLOSE_GRACE + 1)
    assert built.closed, "the detached connection must still be closed"


async def test_close_all_drains_retired_connections_too():
    import time

    now = time.monotonic()
    retired = _FakeClient()
    live = _FakeClient()
    mcp_mod._retire(retired, now)
    _seed("a", live, created=now)

    await mcp_mod.close_all()

    assert live.closed and retired.closed, "shutdown must not leave a retired transport open"
    assert not mcp_mod._CLIENT_CACHE and not mcp_mod._RETIRED


async def test_retired_transports_are_closed_without_a_second_cache_miss(monkeypatch):
    """Retirement used to be drained only by `_evict`, which runs only inside a cache BUILD.

    So a burst that pushed the cache over its ceiling retired N transports, and then - if traffic
    settled into a steady state where every call hit a live entry - nothing ever built again and
    those transports stayed open until a key expired or the process shut down, far past the 30s
    grace they were given. Reaping must not depend on someone missing the cache.

    No cache operation of any kind happens after the eviction here: the only thing that can close
    these is the background reaper.
    """
    import time

    monkeypatch.setattr(mcp_mod, "_REAP_INTERVAL", 0.01)
    now = time.monotonic()
    shed = []
    for i in range(mcp_mod._CACHE_MAX + 5):
        c = _FakeClient()
        shed.append(c)
        _seed(f"cid::{i:04d}", c, created=now, used=now + i)

    mcp_mod._evict(now)
    retired = [c for c in shed if c in [client for _, client in mcp_mod._RETIRED]]
    assert len(retired) == 5, "the ceiling should have shed five connections"
    assert not any(c.closed for c in retired), "the grace period lets in-flight calls finish"

    # Nothing touches the cache from here on. Advance past the grace period and let the reaper
    # run on its own.
    monkeypatch.setattr(mcp_mod, "_CLOSE_GRACE", 0.0)
    mcp_mod._RETIRED[:] = [(0.0, c) for _, c in mcp_mod._RETIRED]
    for _ in range(200):
        await asyncio.sleep(0.01)
        if all(c.closed for c in retired):
            break

    assert all(c.closed for c in retired), (
        "retired transports must be closed on a timer, not only when the next build misses"
    )
    assert not mcp_mod._RETIRED


async def test_eviction_never_closes_a_transport_while_holding_the_build_lock():
    """`_evict` runs inside `_client_and_tools`'s per-key build lock. Closing a transport there
    is an unbounded network await performed while a concurrent caller for that same key is
    blocked, which is exactly the cost the retirement queue exists to avoid.

    Enforced structurally rather than by timing: `_evict` is synchronous, so it CANNOT await a
    close no matter how it is later edited.
    """
    import inspect

    assert not inspect.iscoroutinefunction(mcp_mod._evict), (
        "_evict must stay synchronous - an await here happens under the build lock"
    )

    # And it really does only shed, never close.
    import time

    now = time.monotonic()
    stale = _FakeClient()
    _seed("a", stale, created=now - mcp_mod._CACHE_TTL - 1)
    mcp_mod._evict(now)
    assert "a" not in mcp_mod._CLIENT_CACHE and not stale.closed


async def test_the_reaper_stops_itself_once_the_queue_drains(monkeypatch):
    """One long-lived task, and only while there is something to close - an idle process must not
    hold a timer open forever, and a retirement after that must start it again."""
    import time

    monkeypatch.setattr(mcp_mod, "_REAP_INTERVAL", 0.01)
    monkeypatch.setattr(mcp_mod, "_CLOSE_GRACE", 0.0)

    first = _FakeClient()
    mcp_mod._retire(first, time.monotonic())
    assert mcp_mod._REAPER is not None and not mcp_mod._REAPER.done()

    for _ in range(200):
        await asyncio.sleep(0.01)
        if mcp_mod._REAPER.done():
            break
    assert first.closed
    assert mcp_mod._REAPER.done(), "the reaper must exit once nothing is waiting to be closed"

    second = _FakeClient()
    mcp_mod._retire(second, time.monotonic())
    assert not mcp_mod._REAPER.done(), "a later retirement must start a new reaper"
    for _ in range(200):
        await asyncio.sleep(0.01)
        if second.closed:
            break
    assert second.closed


def test_auth_context_from_puts_end_user_last():
    """end_user_id is authoritative: a caller-injected run_context value must not shadow it,
    or one user could resolve another's stored credential."""

    class _Ctx:
        run_context = {"end_user_id": "spoofed", "csrf": "x"}
        end_user = {"id": "real-user", "email": "real@acme.com"}

    out = mcp_mod.auth_context_from(_Ctx())
    assert out["end_user_id"] == "real-user"
    assert out["csrf"] == "x"


async def test_retry_after_401_does_not_double_apply_params_or_cookies():
    """Headers overwrite on re-apply, but params are MERGED and the cookie jar is CONCATENATED.
    Applying twice to the same request sends `?api_key=X&api_key=X` and `Cookie: s=1; s=1`,
    which some servers reject - turning a recoverable 401 into a hard failure."""
    import httpx

    from forge.auth_providers.resolver import ResolvedAuth

    class _Resolver:
        def __init__(self) -> None:
            self.calls = 0

        async def resolve(self, **kw):
            self.calls += 1
            return ResolvedAuth(
                headers={"X-Auth": f"tok{self.calls}"},
                params={"api_key": "K"},
                cookies={"sess": "S"},
            )

    resolver = _Resolver()
    auth = mcp_mod._ProviderAuth(resolver, tenant_id="t", project_id="p",
                                 provider_id="ap", context={})
    request = httpx.Request("POST", "https://mcp.example.com/mcp?keep=1")

    flow = auth.async_auth_flow(request)
    first = await flow.__anext__()
    assert first.url.params.get_list("api_key") == ["K"]
    try:
        second = await flow.asend(httpx.Response(401, request=first))
    except StopAsyncIteration:  # pragma: no cover - the retry is expected to happen
        raise AssertionError("a 401 should have triggered exactly one retry") from None

    assert second.url.params.get_list("api_key") == ["K"], "auth param was merged twice"
    assert second.url.params.get("keep") == "1", "the request's own params must survive"
    assert second.headers["Cookie"] == "sess=S", "cookie jar was concatenated twice"
    assert second.headers["X-Auth"] == "tok2", "the retry must use the freshly forced credential"
    assert resolver.calls == 2
