"""AuthResolver — resolve an Auth Provider to headers/cookies/params for a tool call.

Caches per (provider, per-user-context-hash) with TTL (in-process here; Redis in
prod). Invalidates on 401/403 (handled by the calling tool). Per-user secrets the
widget injects arrive in `context` and are never stored (Doc 2 §11).
"""

from __future__ import annotations

import base64
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select

from forge.auth_providers.extract import extract_value
from forge.auth_providers.templates import render_value
from forge.db.base import SessionLocal
from forge.models import AuthProvider
from forge.secrets.store import SecretStore
from forge.util.http import shared_async_client
from forge.util.ssrf import validate_url


@dataclass
class ResolvedAuth:
    headers: dict[str, str] = field(default_factory=dict)
    cookies: dict[str, str] = field(default_factory=dict)
    params: dict[str, str] = field(default_factory=dict)
    expires_at: float | None = None  # monotonic seconds

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and time.monotonic() >= self.expires_at


class AuthResolver:
    def __init__(self, secrets: SecretStore | None = None, session_factory=SessionLocal) -> None:
        self.secrets = secrets or SecretStore(session_factory)
        self._sf = session_factory
        self._cache: dict[str, ResolvedAuth] = {}

    async def _load(self, tenant_id: str, provider_id: str) -> AuthProvider | None:
        async with self._sf() as session:
            return (
                await session.execute(
                    select(AuthProvider).where(
                        AuthProvider.tenant_id == tenant_id, AuthProvider.id == provider_id
                    )
                )
            ).scalar_one_or_none()

    @staticmethod
    def _key(provider_id: str, context: dict, per_user_keys: list[str]) -> str:
        dims = "|".join(f"{k}={context.get(k)}" for k in sorted(per_user_keys or []))
        return provider_id + "::" + hashlib.sha256(dims.encode()).hexdigest()[:16]

    async def invalidate(self, key: str) -> None:
        self._cache.pop(key, None)

    async def resolve(
        self,
        *,
        tenant_id: str,
        project_id: str,
        provider_id: str,
        context: dict | None = None,
        force: bool = False,
        client: httpx.AsyncClient | None = None,
        provider: AuthProvider | None = None,
    ) -> ResolvedAuth:
        context = context or {}
        provider = provider or await self._load(tenant_id, provider_id)
        if provider is None:
            raise KeyError(f"Auth provider {provider_id!r} not found")
        cfg = provider.config or {}
        per_user = cfg.get("per_user_context_keys", [])
        key = self._key(provider_id, context, per_user)
        if not force and (cached := self._cache.get(key)) and not cached.expired:
            return cached

        async def read(ref: str | None) -> Any:
            if not ref:
                return None
            return await self.secrets.read_ref(tenant_id=tenant_id, project_id=project_id, ref=ref)

        # `credentials_ref` is the primary secret for csrf_session/custom_script, but only a
        # *fallback* for bearer/api_key (and unused for basic/oauth2). A stale or missing
        # fallback must not abort a provider whose own ref (token_ref/value_ref/…) resolves —
        # the per-kind branches below still raise clearly if their primary ref is absent.
        try:
            creds = await read(provider.credentials_ref or cfg.get("credentials_ref"))
        except Exception:  # noqa: BLE001 - absent fallback secret is tolerated
            creds = None
        kind = provider.kind
        resolved = ResolvedAuth()
        default_ttl = cfg.get("cache_ttl_seconds", 1800)

        if kind == "bearer":
            token = await read(cfg.get("token_ref")) or creds
            resolved.headers[cfg.get("header_name", "Authorization")] = (
                cfg.get("prefix", "Bearer ") + str(token)
            )
            resolved.expires_at = None if default_ttl == 0 else time.monotonic() + default_ttl
        elif kind == "api_key":
            value = await read(cfg.get("value_ref")) or creds
            where, name = cfg.get("in", "header"), cfg["name"]
            (resolved.headers if where == "header" else resolved.params)[name] = str(value)
            resolved.expires_at = None
        elif kind == "basic":
            user = await read(cfg.get("username_ref"))
            pw = await read(cfg.get("password_ref"))
            token = base64.b64encode(f"{user}:{pw}".encode()).decode()
            resolved.headers["Authorization"] = "Basic " + token
            resolved.expires_at = None
        elif kind == "oauth2_client_credentials":
            resolved = await self._oauth2(cfg, read, client)
        elif kind == "oauth2_authorization_code":
            resolved = await self._oauth2_auth_code(provider, cfg, read, tenant_id, project_id, client)
        elif kind == "csrf_session":
            resolved = await self._csrf_session(cfg, {"cred": creds, "ctx": context}, client, default_ttl)
        elif kind == "custom_script":  # pragma: no cover - advanced/audited
            raise NotImplementedError("custom_script auth requires the advanced-scripts feature flag.")
        else:
            raise ValueError(f"Unknown auth kind {kind!r}")

        self._cache[key] = resolved
        return resolved

    async def _oauth2(self, cfg: dict, read, client: httpx.AsyncClient | None) -> ResolvedAuth:
        data = {
            "grant_type": "client_credentials",
            "client_id": await read(cfg.get("client_id_ref")),
            "client_secret": await read(cfg.get("client_secret_ref")),
        }
        if cfg.get("scope"):
            data["scope"] = cfg["scope"]
        if cfg.get("audience"):
            data["audience"] = cfg["audience"]
        await validate_url(cfg["token_url"])
        client = client or shared_async_client()
        r = await client.post(cfg["token_url"], data=data, timeout=30)
        r.raise_for_status()
        body = r.json()
        token = body.get("access_token", "")
        ttl = body.get("expires_in", cfg.get("cache_ttl_seconds", 3600))
        return ResolvedAuth(headers={"Authorization": f"Bearer {token}"}, expires_at=time.monotonic() + ttl)

    @staticmethod
    def bundle_secret_name(provider_id: str) -> str:
        return f"oauth_token__{provider_id}"

    async def _store_bundle(self, tenant_id: str, project_id: str, provider_id: str, bundle: dict) -> None:
        async with self._sf() as session:
            await self.secrets.write(
                session, tenant_id=tenant_id, project_id=project_id,
                name=self.bundle_secret_name(provider_id), value=bundle, kind="oauth",
            )

    async def _oauth2_auth_code(
        self, provider, cfg: dict, read, tenant_id: str, project_id: str, client: httpx.AsyncClient | None
    ) -> ResolvedAuth:
        bundle_ref = cfg.get("token_bundle_ref") or f"secret://proj/{self.bundle_secret_name(provider.id)}"
        bundle = await read(bundle_ref)
        if not isinstance(bundle, dict) or not bundle.get("access_token"):
            raise KeyError(f"OAuth provider {provider.id!r} is not connected — run the connect flow first")
        now = time.time()
        expires_at = bundle.get("expires_at")
        if expires_at and now >= (float(expires_at) - 60) and bundle.get("refresh_token"):
            bundle = await self._refresh_oauth(provider, cfg, read, bundle, tenant_id, project_id, client)
            expires_at = bundle.get("expires_at")
        header = cfg.get("header_name", "Authorization")
        prefix = cfg.get("prefix", "Bearer ")
        ttl_left = (float(expires_at) - now) if expires_at else None
        cache_exp = time.monotonic() + max(0.0, ttl_left - 60) if ttl_left and ttl_left > 0 else None
        return ResolvedAuth(headers={header: prefix + str(bundle["access_token"])}, expires_at=cache_exp)

    async def _refresh_oauth(self, provider, cfg: dict, read, bundle: dict, tenant_id, project_id, client) -> dict:
        data = {
            "grant_type": "refresh_token",
            "refresh_token": bundle["refresh_token"],
            "client_id": await read(cfg.get("client_id_ref")),
            "client_secret": await read(cfg.get("client_secret_ref")),
        }
        await validate_url(cfg["token_url"])
        client = client or shared_async_client()
        r = await client.post(cfg["token_url"], data={k: v for k, v in data.items() if v is not None}, timeout=30)
        r.raise_for_status()
        body = r.json()
        new = dict(bundle)
        new["access_token"] = body.get("access_token", bundle["access_token"])
        if body.get("refresh_token"):
            new["refresh_token"] = body["refresh_token"]
        if body.get("expires_in"):
            new["expires_at"] = time.time() + int(body["expires_in"])
        await self._store_bundle(tenant_id, project_id, provider.id, new)
        return new

    async def _csrf_session(self, cfg: dict, vars: dict, client: httpx.AsyncClient | None, default_ttl: int) -> ResolvedAuth:
        fetch = render_value(cfg["token_fetch"], vars)
        await validate_url(fetch["url"])
        client = client or shared_async_client()
        r = await client.request(
            fetch["method"], fetch["url"], headers=fetch.get("headers"), json=fetch.get("body"), timeout=30
        )
        r.raise_for_status()

        extracted: dict[str, Any] = {}
        ttl = None
        for rule in cfg.get("extract", []):
            val = extract_value(r, rule)
            if rule.get("kind") == "ttl":
                ttl = int(val) if val else None
            else:
                extracted[rule["name"]] = val

        out = ResolvedAuth(expires_at=time.monotonic() + (ttl or default_ttl))
        for rule in cfg.get("inject", []):
            value = render_value(rule["value"], {"extracted": extracted})
            where = rule["to"]
            target = {"header": out.headers, "cookie": out.cookies, "query": out.params}[where]
            target[rule["name"]] = str(value)
        return out
