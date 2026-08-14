"""Expanding a connector manifest into real Forge rows (and taking them back out again).

The install is deliberately boring: it calls the SAME services the console calls when a person
builds these by hand (SecretStore, AuthProviderService, ToolSetService, ToolService), so an
installed connector is byte-for-byte the kind of row a user could have authored. There is no
"connector runtime" to diverge from the hand-built path.

Everything created is recorded on the ConnectorInstall row, which is what makes `uninstall`
exact - it deletes the ids it created and nothing else, so a tool the user later moved into
another tool set, or an auth provider they repointed, is never collateral damage.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.connectors.manifest import ConnectorManifest, McpBackend, RestBackend
from forge.models import AuthProvider, ConnectorInstall, McpClient, Project, Tool, ToolSet
from forge.secrets.store import SecretStore
from forge.services.auth_providers import AuthProviderService
from forge.services.tool_sets import ToolSetService
from forge.services.tools import ToolService

log = logging.getLogger("forge.connectors")


class InstallError(RuntimeError):
    """The install cannot proceed (missing credentials, name clash, unreachable server)."""


def secret_name(group: str, key: str) -> str:
    """Stable, collision-proof name for a connector's stored credential.

    Namespaced by CREDENTIAL GROUP (which defaults to the connector's slug) so two unrelated
    connectors can both have a `client_secret` without one overwriting the other's - while
    connectors authorised by the same vendor app (all the Google ones) deliberately share.
    """
    return f"connector_{group}_{key}"


#: The one place a catalog connector's vendor credentials may come from. Named in every error
#: message on this path, because "who types the client secret" is the single question an operator
#: has when a connector says it isn't available.
ENV_VAR = "FORGE_CONNECTOR_OAUTH_APPS"


def deployment_app(manifest: ConnectorManifest) -> dict[str, str]:
    """Deployment-wide OAuth app credentials for this manifest's group, if the operator
    configured one (FORGE_CONNECTOR_OAUTH_APPS). Empty dict = none, so each project registers
    its own app."""
    from forge.config import settings

    return dict(settings.connector_oauth_apps.get(manifest.group) or {})


def missing_app_keys(manifest: ConnectorManifest) -> list[str]:
    """Credential keys a CATALOG install needs and this deployment hasn't configured.

    Empty for a connector that needs no vendor app at all: `auth.kind: none`, or an MCP server
    that publishes OAuth metadata, where Forge registers a client on the fly (RFC 7591) the
    first time someone connects.
    """
    if manifest.auth.kind == "none" or manifest.auth.discover:
        return []
    app = deployment_app(manifest)
    return [f.key for f in manifest.auth.setup if f.secret and f.required and not app.get(f.key)]


def env_ready(manifest: ConnectorManifest) -> bool:
    """True when a catalog connector can be connected with one click, right now.

    Catalog connectors never accept typed credentials. The vendor's OAuth app belongs to the
    DEPLOYMENT - it is registered once by whoever runs Forge, in the environment, next to every
    other deployment secret - and each person then signs in with their own account against it.
    A connector whose app isn't configured is unavailable and says so, rather than falling back
    to a form that asks an end user for a client secret they have no business holding.
    """
    return not missing_app_keys(manifest)


def not_configured_message(manifest: ConnectorManifest) -> str:
    """The message an operator needs, not the one the runtime happens to know: what to register,
    which env key it goes under, and what the payoff is."""
    import json as _json

    example = _json.dumps({manifest.group: {k: "…" for k in missing_app_keys(manifest)}})
    return (
        f"{manifest.name} isn't set up on this Forge deployment. Whoever runs it registers one "
        f"{manifest.group} OAuth app and adds it to {ENV_VAR} (e.g. {example}), then restarts the "
        "API. After that everyone signs in with their own account - nothing to paste."
    )


async def group_has_credentials(
    secrets: SecretStore, tenant_id: str, project_id: str, manifest: ConnectorManifest
) -> bool:
    """True when this connector can be installed WITHOUT asking for credentials - either the
    deployment ships a registered app for its group, or a sibling connector already set one up."""
    required = [f for f in manifest.auth.setup if f.secret and f.required]
    if not required:
        return False
    app = deployment_app(manifest)
    if app and all(app.get(f.key) for f in required):
        return True
    for field in required:
        try:
            value = await secrets.read_ref(
                tenant_id=tenant_id, project_id=project_id,
                ref=f"secret://proj/{secret_name(manifest.group, field.key)}",
            )
        except Exception:  # noqa: BLE001 - absent secret means the group isn't set up
            return False
        if not value:
            return False
    return True


def _mcp_tool_allowed(name: str, backend: McpBackend) -> bool:
    """Apply the manifest's allow/deny filter to a discovered remote tool name.

    `allow` (when non-empty) is exact and wins outright; otherwise everything passes except
    `deny` entries, which support a single trailing `*` so a manifest can drop a whole family
    (e.g. "admin_*") without enumerating it."""
    if backend.allow:
        return name in backend.allow
    for pattern in backend.deny:
        if pattern.endswith("*"):
            if name.startswith(pattern[:-1]):
                return False
        elif name == pattern:
            return False
    return True


def _auth_config(manifest: ConnectorManifest, values: dict[str, str], *, per_user: bool) -> dict[str, Any]:
    """Build the AuthProvider.config for this manifest + the installer's supplied values.

    Credential VALUES never land here - only `secret://proj/<name>` refs to what was written to
    the SecretStore, matching how a hand-built provider stores them."""
    auth = manifest.auth
    ref = lambda key: f"secret://proj/{secret_name(manifest.group, key)}"  # noqa: E731 - local alias
    cfg: dict[str, Any] = {"connector_slug": manifest.slug}
    if per_user:
        # The dim every per-user surface already keys on (connections router, MCP PAT identity,
        # widget context). Keeping this exact string is what makes the existing self-service
        # /connections routes work for connectors with no extra wiring.
        cfg["per_user_context_keys"] = ["end_user_id"]

    kind = auth.kind
    if kind == "bearer":
        cfg["token_ref"] = ref("token")
        if auth.header_name:
            cfg["header_name"] = auth.header_name
        if auth.prefix is not None:
            cfg["prefix"] = auth.prefix
    elif kind == "api_key":
        cfg["value_ref"] = ref("api_key")
        cfg["in"] = auth.location
        cfg["name"] = auth.param_name or auth.header_name or "Authorization"
    elif kind == "basic":
        cfg["username_ref"] = ref("username")
        cfg["password_ref"] = ref("password")
    elif kind in ("oauth2_authorization_code", "oauth2_client_credentials"):
        cfg["client_id_ref"] = ref("client_id")
        cfg["client_secret_ref"] = ref("client_secret")
        if auth.token_url:
            cfg["token_url"] = auth.token_url
        if auth.authorize_url:
            cfg["authorize_url"] = auth.authorize_url
        if auth.scopes:
            cfg["scope"] = " ".join(auth.scopes)
        if auth.token_auth != "post":
            cfg["token_auth"] = auth.token_auth
        if auth.authorize_params:
            cfg["authorize_params"] = dict(auth.authorize_params)
        if auth.discover:
            # Endpoints are filled in at connect time from the MCP server's published metadata.
            cfg["oauth_discover"] = True
    # Non-secret setup values (a tenant id, a region, an instance host) are exposed to request
    # templates as {{env.*}}-style config on the provider AND substituted into URLs below.
    extras = {f.key: (values.get(f.key) or f.default or "") for f in auth.setup if not f.secret}
    if extras:
        cfg["connector_vars"] = extras
        # Bake the non-secret values into the OAuth endpoints too: a Microsoft-style
        # authorize/token URL embeds the tenant, so leaving `{setup.tenant}` unsubstituted here
        # would send the connect flow to a literal, non-existent URL.
        for key in ("authorize_url", "token_url"):
            if isinstance(cfg.get(key), str):
                cfg[key] = _substitute(cfg[key], extras)
    return cfg


def _substitute(value: Any, vars: dict[str, str]) -> Any:
    """Replace `{setup.key}` placeholders in manifest URLs with the installer's non-secret values.

    Uses a distinct `{setup.x}` syntax rather than the runtime `{{...}}` templating on purpose:
    these are resolved ONCE at install time and baked into the stored config, so they must not
    look like the per-call templates the REST tool renders at run time."""
    if isinstance(value, str):
        out = value
        for k, v in vars.items():
            out = out.replace("{setup." + k + "}", v)
        return out
    if isinstance(value, dict):
        return {k: _substitute(v, vars) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, vars) for v in value]
    return value


class ConnectorInstaller:
    """Install / uninstall / upgrade one connector in one project."""

    def __init__(self, secrets: SecretStore | None = None) -> None:
        self.secrets = secrets or SecretStore()

    # --- queries ---------------------------------------------------------------------------

    @staticmethod
    async def list_installs(session: AsyncSession, tenant_id: str, project_id: str) -> list[ConnectorInstall]:
        rows = await session.execute(
            select(ConnectorInstall)
            .where(ConnectorInstall.tenant_id == tenant_id, ConnectorInstall.project_id == project_id)
            .order_by(ConnectorInstall.name)
        )
        return list(rows.scalars())

    @staticmethod
    async def get_install(session: AsyncSession, tenant_id: str, project_id: str, slug: str) -> ConnectorInstall | None:
        row = await session.execute(
            select(ConnectorInstall).where(
                ConnectorInstall.tenant_id == tenant_id,
                ConnectorInstall.project_id == project_id,
                ConnectorInstall.slug == slug,
            )
        )
        return row.scalar_one_or_none()

    # --- install ---------------------------------------------------------------------------

    async def install(
        self,
        session: AsyncSession,
        tenant_id: str,
        project_id: str,
        manifest: ConnectorManifest,
        *,
        values: dict[str, str] | None = None,
        auth_mode: str = "shared",
        source: str = "catalog",
    ) -> ConnectorInstall:
        values = dict(values or {})
        existing = await self.get_install(session, tenant_id, project_id, manifest.slug)
        if existing is not None:
            raise InstallError(f"{manifest.name} is already installed in this project")

        # A CATALOG connector is deployment-managed: its vendor app comes from the environment
        # and every account on it is personal. A CUSTOM one is the escape hatch - pasted
        # credentials, and the installer's choice of shared vs per-user - because that is where
        # a service account or an internal API legitimately belongs.
        managed = source == "catalog"
        per_user = self._resolve_per_user(manifest, auth_mode, managed=managed)
        if managed:
            if missing_app_keys(manifest):
                raise InstallError(not_configured_message(manifest))
            # Ignore anything the caller sent. There is no supported way to type a credential
            # into a catalog connector, so accepting one here would create a second, invisible
            # source of truth that `env_ready` would then keep reporting as unconfigured.
            values = {}
        else:
            # A sibling connector in the same credential group may already hold the vendor app's
            # credentials (install Gmail, then Google Calendar reuses the same OAuth client), in
            # which case this install asks for nothing.
            reuse = await group_has_credentials(self.secrets, tenant_id, project_id, manifest)
            if not reuse:
                self._check_required(manifest, values)

        created_secrets: list[str] = []
        auth_provider_id: str | None = None
        tool_set_id: str | None = None
        mcp_client_id: str | None = None
        tool_ids: list[str] = []

        try:
            # 1. Secrets. Skipped for per-user OAuth where the END USER supplies the token, but a
            #    per-user OAuth connector still needs the APP's client id/secret, so those are
            #    written either way when present.
            #
            #    A deployment-registered app (FORGE_CONNECTOR_OAUTH_APPS) fills in anything the
            #    installer didn't type. Seeding the project's own store - rather than teaching
            #    the resolver a second credential source - means every downstream path (resolve,
            #    refresh, rotate, uninstall) stays identical to a hand-pasted credential.
            app = deployment_app(manifest)
            for field in manifest.auth.setup:
                if not field.secret:
                    continue
                val = values.get(field.key, "") or app.get(field.key, "")
                if not val:
                    continue
                name = secret_name(manifest.group, field.key)
                await self.secrets.write(
                    session, tenant_id=tenant_id, project_id=project_id,
                    name=name, value=val, kind="connector",
                )
                created_secrets.append(name)

            # 2. Auth provider (unless the connector needs no credentials at all).
            if manifest.auth.kind != "none":
                cfg = _auth_config(manifest, values, per_user=per_user)
                ap = await AuthProviderService.create(
                    session, tenant_id, project_id,
                    name=manifest.name, kind=manifest.auth.kind, config=cfg,
                )
                auth_provider_id = ap.id

            # 3. Tool set - the folder the user sees, the unit an agent is granted, and the
            #    GitHub-style toolset published over MCP. One per connector.
            ts = await ToolSetService.create(
                session, tenant_id, project_id,
                name=manifest.name,
                description=manifest.toolset.description or manifest.summary,
                icon=manifest.icon,
                exposed=True,
            )
            tool_set_id = ts.id

            # 4. Backend-specific tools.
            # Non-secret values, with the manifest's default filling in for anything the
            # installer left blank - a `{setup.tenant}` placeholder must never render empty and
            # silently produce a malformed URL.
            setup_vars = {
                f.key: (values.get(f.key) or f.default or "")
                for f in manifest.auth.setup if not f.secret
            }
            if isinstance(manifest.backend, RestBackend):
                tool_ids = await self._create_rest_tools(
                    session, tenant_id, project_id, manifest, ts, auth_provider_id, setup_vars
                )
            else:
                mcp_client_id, tool_ids = await self._create_mcp_tools(
                    session, tenant_id, project_id, manifest, ts, auth_provider_id, setup_vars
                )

            # 5. Egress allow-list. A no-op unless the deployment runs a strict allow-list; it
            #    only ever ADDS the connector's own hosts and never relaxes block_private.
            await self._allow_egress(session, tenant_id, project_id, manifest.hosts())

            row = ConnectorInstall(
                tenant_id=tenant_id, project_id=project_id, slug=manifest.slug, name=manifest.name,
                version=manifest.version, source=source, manifest=manifest.model_dump(mode="json"),
                auth_provider_id=auth_provider_id, tool_set_id=tool_set_id, mcp_client_id=mcp_client_id,
                created_tool_ids=tool_ids, created_secret_names=created_secrets,
                auth_mode="per_user" if per_user else "shared",
                status=self._initial_status(manifest, per_user=per_user),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row
        except Exception:
            # A half-installed connector is worse than none: it leaves orphan tools pointing at
            # an auth provider the user can't see the origin of. Roll the created rows back.
            await self._rollback(session, tenant_id, project_id, tool_ids, tool_set_id,
                                 auth_provider_id, mcp_client_id, created_secrets)
            raise

    @staticmethod
    def _resolve_per_user(manifest: ConnectorManifest, auth_mode: str, *, managed: bool = False) -> bool:
        if manifest.auth.kind == "none":
            return False
        if managed:
            # Every catalog connector is a personal connection, full stop. An account belongs to
            # the person who signed in to it, and a project-wide token would mean one person's
            # mailbox, drive or repo access silently becomes everyone's. Sharing one credential
            # across a project is still possible - it is a deliberate act via a custom connector.
            return True
        policy = manifest.auth.per_user
        if policy == "required":
            return True
        if policy == "never":
            return False
        return auth_mode == "per_user"

    @staticmethod
    def _check_required(manifest: ConnectorManifest, values: dict[str, str]) -> None:
        missing = [
            f.label for f in manifest.auth.setup
            if f.required and not (values.get(f.key) or f.default)
        ]
        if missing:
            raise InstallError("Missing required setup values: " + ", ".join(missing))

    @staticmethod
    def _initial_status(manifest: ConnectorManifest, *, per_user: bool) -> str:
        if manifest.auth.kind == "none":
            return "connected"
        if manifest.auth.kind == "oauth2_authorization_code":
            # Nobody has run the browser consent yet - true for a shared credential and for a
            # per-user one alike. On a per-user connector this row-level status only ever means
            # "at least one person has connected"; the status that matters to a given person is
            # computed per caller (see routers/connectors.py), because their colleague having
            # connected their own mailbox says nothing about theirs.
            return "needs_auth"
        return "connected"

    # --- backends --------------------------------------------------------------------------

    async def _create_rest_tools(
        self, session: AsyncSession, tenant_id: str, project_id: str,
        manifest: ConnectorManifest, ts: ToolSet, auth_provider_id: str | None, setup_vars: dict[str, str],
    ) -> list[str]:
        backend = manifest.backend
        assert isinstance(backend, RestBackend)
        ids: list[str] = []
        for action in backend.actions:
            cfg = self._rest_tool_config(manifest, backend, action, setup_vars)
            tool = await ToolService.create(
                session, tenant_id, project_id,
                name=action.name, kind="rest_api", config=cfg, auth_provider_id=auth_provider_id,
            )
            ids.append(tool.id)
            await ToolSetService.add_member(session, ts, tool.id)
        return ids

    async def _create_mcp_tools(
        self, session: AsyncSession, tenant_id: str, project_id: str,
        manifest: ConnectorManifest, ts: ToolSet, auth_provider_id: str | None, setup_vars: dict[str, str],
    ) -> tuple[str, list[str]]:
        backend = manifest.backend
        assert isinstance(backend, McpBackend)
        client = McpClient(
            tenant_id=tenant_id, project_id=project_id, name=manifest.name,
            transport=backend.transport, url=_substitute(backend.url, setup_vars),
            args={}, enabled=True, auth_provider_id=auth_provider_id,
        )
        session.add(client)
        await session.flush()

        # Discovery needs live credentials. Before the OAuth consent has been run there are
        # none, so an empty tool list here is EXPECTED, not an error - the connect flow calls
        # sync_mcp_tools() afterwards to fill the set in. Failing the install because an
        # unauthenticated probe returned nothing would make every OAuth connector uninstallable.
        tool_ids = await self._sync_mcp_tools(session, tenant_id, project_id, manifest, client, ts, best_effort=True)
        return client.id, tool_ids

    async def _sync_mcp_tools(
        self, session: AsyncSession, tenant_id: str, project_id: str, manifest: ConnectorManifest,
        client: McpClient, ts: ToolSet, *, best_effort: bool = False, context: dict | None = None,
    ) -> list[str]:
        """Discover the server's tools and materialize one Tool row per allowed remote tool.

        `context` carries the per-user dims (end_user_id) of whoever's credential should be used
        to ASK. It is not optional in practice for a catalog connector: those are all per-user,
        so without it the resolver looks up a shared token bundle that was never written, the
        request goes out unauthenticated, and the server answers 401 - which is what "0 actions"
        after a successful sign-in looks like.

        Idempotent: re-running after a connect (or after the vendor adds tools) creates only the
        rows that don't exist yet, so tool ids - and therefore every workflow node and agent
        grant that references them - stay stable across a re-sync."""
        from forge.tools.mcp import McpUnavailable, describe_mcp_error, discover_tools

        backend = manifest.backend
        assert isinstance(backend, McpBackend)
        try:
            remote = await discover_tools(client, tenant_id, project_id, context)
        except (McpUnavailable, Exception) as e:  # noqa: BLE001 - unreachable/unauthed server
            detail = describe_mcp_error(e)
            if best_effort:
                log.info("connector %s: tool discovery deferred (%s)", manifest.slug, detail)
                return []
            raise InstallError(f"Could not list tools from {manifest.name}: {detail}") from e

        existing = await session.execute(
            select(Tool).where(Tool.tenant_id == tenant_id, Tool.project_id == project_id, Tool.kind == "mcp")
        )
        have = {
            (t.config or {}).get("remote_tool_name"): t
            for t in existing.scalars()
            if (t.config or {}).get("mcp_client_id") == client.id
        }

        ids: list[str] = []
        for entry in remote:
            name = entry.get("name") or ""
            if not name or not _mcp_tool_allowed(name, backend):
                continue
            found = have.get(name)
            if found is not None:
                ids.append(found.id)
                continue
            tool = await ToolService.create(
                session, tenant_id, project_id, name=name, kind="mcp",
                config={
                    "name": name,
                    "kind": "mcp",
                    "description": entry.get("description", ""),
                    "mcp_client_id": client.id,
                    "remote_tool_name": name,
                    "connector_slug": manifest.slug,
                },
                auth_provider_id=client.auth_provider_id,
            )
            ids.append(tool.id)
            await ToolSetService.add_member(session, ts, tool.id)
        return ids

    async def sync_tools(self, session: AsyncSession, install: ConnectorInstall,
                         *, context: dict | None = None) -> int:
        """Bring an installed connector's actions back in line with its manifest.

        For an MCP connector that means re-asking the server what it exposes (after a successful
        connect, and from the UI as "Refresh actions"). For a REST connector it means re-applying
        the CURRENT catalog manifest to the tool rows - which is how a fix to a bundled connector
        actually reaches a project that already installed it, without uninstalling (and losing
        everyone's sign-in) to pick it up.

        `context` is whose credential to ask with - `{"end_user_id": ...}` for the per-user
        connectors the catalog ships. Callers that have an identity MUST pass it; see
        `_sync_mcp_tools` for what happens when nobody does."""
        from forge.connectors.manifest import parse_manifest

        manifest = parse_manifest(install.manifest or {})
        if not isinstance(manifest.backend, McpBackend):
            return await self._refresh_rest_tools(session, install, manifest)
        if not install.mcp_client_id:
            return len(install.created_tool_ids or [])
        client = (await session.execute(
            select(McpClient).where(McpClient.tenant_id == install.tenant_id, McpClient.id == install.mcp_client_id)
        )).scalar_one_or_none()
        ts = await ToolSetService.get(session, install.tenant_id, install.tool_set_id or "")
        if client is None or ts is None:
            return 0
        ids = await self._sync_mcp_tools(session, install.tenant_id, install.project_id, manifest,
                                         client, ts, context=context)
        merged = list(dict.fromkeys([*(install.created_tool_ids or []), *ids]))
        install.created_tool_ids = merged
        await session.commit()
        return len(ids)

    async def _refresh_rest_tools(self, session: AsyncSession, install: ConnectorInstall,
                                  frozen: ConnectorManifest) -> int:
        """Re-apply a REST connector's manifest to the tool rows it created.

        A catalog connector installed last week holds a COPY of the manifest as it was then. When
        a bundled manifest is corrected - as Gmail's send action was, once it turned out that
        asking a model to base64-encode a MIME message produces an opaque 400 - the fix has to be
        able to reach projects that already installed it. Uninstall/reinstall would work but
        deletes the auth provider, so every person who signed in would have to do it again for a
        change they had nothing to do with.

        Tool IDS are preserved: rows are matched by action name and updated in place, so every
        workflow node and agent grant pointing at them keeps working. Actions the manifest no
        longer declares are LEFT ALONE rather than deleted - a workflow may still reference one,
        and breaking a live graph is a worse failure than carrying a stale tool.
        """
        # The current catalog entry for a bundled connector; the frozen copy for a pasted one,
        # which has no newer version to move to.
        latest = frozen
        if install.source == "catalog":
            from forge.connectors import catalog as catalog_mod

            found = catalog_mod.get_manifest(install.slug)
            if found is not None:
                latest = found
        backend = latest.backend
        if not isinstance(backend, RestBackend):
            return len(install.created_tool_ids or [])

        rows = (await session.execute(
            select(Tool).where(
                Tool.tenant_id == install.tenant_id, Tool.project_id == install.project_id,
                Tool.id.in_(install.created_tool_ids or ["__none__"]),
            )
        )).scalars()
        by_name = {t.name: t for t in rows}

        ts = await ToolSetService.get(session, install.tenant_id, install.tool_set_id or "")
        # The non-secret values THIS install was created with (a Jira site, a custom connector's
        # base_url), kept on the auth provider as `connector_vars`. Rebuilding URLs from the
        # manifest defaults instead would silently repoint every tool at the wrong host - the
        # defaults are what the installer overrode.
        setup_vars = {f.key: (f.default or "") for f in latest.auth.setup if not f.secret}
        if install.auth_provider_id:
            ap = (await session.execute(
                select(AuthProvider).where(
                    AuthProvider.tenant_id == install.tenant_id,
                    AuthProvider.id == install.auth_provider_id,
                )
            )).scalar_one_or_none()
            for key, val in ((ap.config or {}).get("connector_vars") or {}).items() if ap else ():
                if val:
                    setup_vars[key] = val
        ids = list(install.created_tool_ids or [])
        changed = 0
        for action in backend.actions:
            cfg = self._rest_tool_config(latest, backend, action, setup_vars)
            tool = by_name.get(action.name)
            if tool is not None:
                if (tool.config or {}) != cfg:
                    tool.config = cfg
                    changed += 1
                continue
            created = await ToolService.create(
                session, install.tenant_id, install.project_id,
                name=action.name, kind="rest_api", config=cfg,
                auth_provider_id=install.auth_provider_id,
            )
            ids.append(created.id)
            changed += 1
            if ts is not None:
                await ToolSetService.add_member(session, ts, created.id)

        install.created_tool_ids = ids
        install.manifest = latest.model_dump(mode="json")
        install.version = latest.version
        await session.commit()
        log.info("connector %s: refreshed %d action(s) to v%s", install.slug, changed, latest.version)
        return len(ids)

    @staticmethod
    def _rest_tool_config(manifest: ConnectorManifest, backend: RestBackend, action,
                          setup_vars: dict[str, str]) -> dict[str, Any]:
        """The `Tool.config` one REST action expands to. Shared by install and refresh so the two
        paths cannot drift into producing different tools from the same manifest."""
        base = _substitute(backend.base_url, setup_vars)
        request = _substitute(dict(action.request), setup_vars)
        url = str(request.get("url_template", ""))
        # A relative action URL composes with the manifest's base_url; an absolute one wins, so a
        # single connector can reach a second host (e.g. an upload endpoint) when needed.
        if base and not url.startswith(("http://", "https://")):
            request["url_template"] = base.rstrip("/") + "/" + url.lstrip("/")
        cfg: dict[str, Any] = {
            "name": action.name,
            "description": action.description,
            "kind": "rest_api",
            "request": request,
            "connector_slug": manifest.slug,
        }
        if action.display_name:
            cfg["display_name"] = action.display_name
        if action.response:
            cfg["response"] = action.response
        for key in ("timeout_seconds", "retry", "rate_limit", "cache"):
            val = getattr(action, key)
            if val is not None:
                cfg[key] = val
        return cfg

    # --- egress ----------------------------------------------------------------------------

    @staticmethod
    async def _allow_egress(session: AsyncSession, tenant_id: str, project_id: str, hosts: list[str]) -> None:
        if not hosts:
            return
        project = (await session.execute(
            select(Project).where(Project.tenant_id == tenant_id, Project.id == project_id)
        )).scalar_one_or_none()
        if project is None:
            return
        cfg = dict(project.config or {})
        egress = dict(cfg.get("egress") or {})
        allow = list(egress.get("allow_hosts") or [])
        added = [h for h in hosts if h not in allow]
        if not added:
            return
        egress["allow_hosts"] = [*allow, *added]
        cfg["egress"] = egress
        project.config = cfg
        await session.commit()

    # --- uninstall -------------------------------------------------------------------------

    async def uninstall(self, session: AsyncSession, install: ConnectorInstall) -> None:
        """Remove exactly what this install created.

        Tools are deleted through ToolService so their tool-set memberships go with them.
        Agents/workflows that referenced a deleted tool id keep working - the compiler already
        skips ids it can't resolve - so uninstalling breaks a graph's capability, never its run.
        """
        tenant_id = install.tenant_id
        for tool_id in install.created_tool_ids or []:
            tool = await ToolService.get(session, tenant_id, tool_id)
            if tool is not None:
                await ToolService.delete(session, tool)

        if install.mcp_client_id:
            client = (await session.execute(
                select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == install.mcp_client_id)
            )).scalar_one_or_none()
            if client is not None:
                await session.delete(client)
                await session.commit()
                from forge.tools.mcp import invalidate_client
                invalidate_client(install.mcp_client_id)

        if install.tool_set_id:
            ts = await ToolSetService.get(session, tenant_id, install.tool_set_id)
            if ts is not None:
                await ToolSetService.delete(session, ts)

        if install.auth_provider_id:
            ap = (await session.execute(
                select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.id == install.auth_provider_id)
            )).scalar_one_or_none()
            if ap is not None:
                await AuthProviderService.delete(session, ap)

        # Stored credentials are overwritten with an empty value rather than row-deleted, which
        # keeps the secret's audit history intact (the store audits reads at one choke point)
        # while making the credential unusable immediately.
        #
        # EXCEPT when a sibling connector still shares this credential group: Gmail, Calendar,
        # Drive and Sheets are one Google OAuth app, so blanking the client secret on the way
        # out would silently break every other Google connector in the project.
        shared = await self._group_still_in_use(session, install)
        if shared:
            log.info("connector uninstall: keeping %s's shared credentials (still used by %s)",
                     install.slug, shared)
        else:
            # Clear the whole GROUP's credentials, not just the names this install happens to
            # have recorded. When two connectors share a vendor app only the FIRST one records
            # the secret names, so uninstalling that one first (kept, correctly, because a
            # sibling remained) and then the sibling would otherwise leave the credential
            # orphaned in the store forever with no install left to point at it.
            for name in self._group_secret_names(install):
                try:
                    await self.secrets.write(
                        session, tenant_id=tenant_id, project_id=install.project_id,
                        name=name, value="", kind="connector",
                    )
                except Exception as e:  # noqa: BLE001 - a missing secret is already the goal state
                    log.debug("connector uninstall: could not clear secret %s (%s)", name, e)

        await session.delete(install)
        await session.commit()

    @staticmethod
    def _group_secret_names(install: ConnectorInstall) -> list[str]:
        """Every secret name belonging to this install's credential group: the ones it recorded
        at install time, plus the ones its manifest declares (which a SIBLING may have written)."""
        from forge.connectors.manifest import parse_manifest

        names = list(install.created_secret_names or [])
        try:
            manifest = parse_manifest(install.manifest or {})
        except Exception:  # noqa: BLE001 - fall back to just the recorded names
            return names
        for field in manifest.auth.setup:
            if not field.secret:
                continue
            name = secret_name(manifest.group, field.key)
            if name not in names:
                names.append(name)
        return names

    @staticmethod
    async def _group_still_in_use(session: AsyncSession, install: ConnectorInstall) -> str | None:
        """The name of another installed connector sharing this one's credential group, if any.

        Read from each install's FROZEN manifest rather than the live catalog, so the answer
        reflects what is actually deployed even if a catalog update later regrouped things."""
        from forge.connectors.manifest import parse_manifest

        try:
            mine = parse_manifest(install.manifest or {})
        except Exception:  # noqa: BLE001 - an unparseable manifest can't claim a shared group
            return None
        rows = await session.execute(
            select(ConnectorInstall).where(
                ConnectorInstall.tenant_id == install.tenant_id,
                ConnectorInstall.project_id == install.project_id,
                ConnectorInstall.id != install.id,
            )
        )
        for other in rows.scalars():
            try:
                if parse_manifest(other.manifest or {}).group == mine.group:
                    return other.name
            except Exception:  # noqa: BLE001 - skip a sibling we can't parse
                continue
        return None

    async def _rollback(
        self, session: AsyncSession, tenant_id: str, project_id: str, tool_ids: list[str],
        tool_set_id: str | None, auth_provider_id: str | None, mcp_client_id: str | None,
        secret_names: list[str],
    ) -> None:
        """Best-effort teardown of a failed partial install. Each step is independently guarded:
        the reason we're here is that something already went wrong, so a second failure must not
        mask the original exception being re-raised by the caller."""
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        stub = ConnectorInstall(
            tenant_id=tenant_id, project_id=project_id, slug="__rollback__", name="rollback",
            created_tool_ids=tool_ids, created_secret_names=secret_names,
            tool_set_id=tool_set_id, auth_provider_id=auth_provider_id, mcp_client_id=mcp_client_id,
        )
        try:
            # Reuse uninstall's teardown, minus the final row delete (the stub was never added).
            for tool_id in stub.created_tool_ids or []:
                tool = await ToolService.get(session, tenant_id, tool_id)
                if tool is not None:
                    await ToolService.delete(session, tool)
            if mcp_client_id:
                client = (await session.execute(
                    select(McpClient).where(McpClient.tenant_id == tenant_id, McpClient.id == mcp_client_id)
                )).scalar_one_or_none()
                if client is not None:
                    await session.delete(client)
                    await session.commit()
            if tool_set_id:
                ts = await ToolSetService.get(session, tenant_id, tool_set_id)
                if ts is not None:
                    await ToolSetService.delete(session, ts)
            if auth_provider_id:
                ap = (await session.execute(
                    select(AuthProvider).where(AuthProvider.tenant_id == tenant_id, AuthProvider.id == auth_provider_id)
                )).scalar_one_or_none()
                if ap is not None:
                    await AuthProviderService.delete(session, ap)
        except Exception as e:  # noqa: BLE001
            log.warning("connector install rollback was incomplete: %s", e)
