"""Connectors - browse the catalog, connect your own account, and manage what's installed.

Three ways in, deliberately:

  * catalog  - `POST /{slug}/connect` is the whole flow: it expands the bundled manifest if the
               project doesn't have it yet, then hands back the vendor's sign-in URL.
  * custom   - `POST /custom` installs a pasted/uploaded manifest, so a private connector pack
               can live in a company's own git repo instead of a fork of Forge.
  * raw MCP  - the existing /mcp-clients routes, unchanged.

Two rules run through every route here:

CREDENTIALS COME FROM THE DEPLOYMENT. A catalog connector's vendor OAuth app is read from
FORGE_CONNECTOR_OAUTH_APPS and nowhere else - no route on this router accepts one for a catalog
install, and a connector whose group isn't configured reports itself unavailable, naming the env
key, rather than degrading into a form that asks an end user for a client secret.

ACCOUNTS ARE PERSONAL. Adding a connector creates project-level rows (tools a workflow is built
on), so it stays editor-gated. Connecting is not: each person signs in as themselves, their token
is stored under their own identity, and every status this router reports is answered for the
CALLER - a colleague's mailbox never becomes yours by virtue of sharing a project.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from forge.auth_providers.oauth_flow import (
    OAuthNotConfigured,
    build_authorize_url,
)
from forge.auth_providers.oauth_flow import (
    redirect_uri as oauth_redirect_uri,
)
from forge.auth_providers.resolver import AuthResolver
from forge.connectors import catalog as catalog_mod
from forge.connectors.install import (
    ENV_VAR,
    ConnectorInstaller,
    InstallError,
    env_ready,
    missing_app_keys,
    not_configured_message,
    secret_name,
)
from forge.connectors.manifest import ConnectorManifest, ManifestError, McpBackend, parse_manifest
from forge.deps import (
    CurrentUser,
    current_tenant_id,
    effective_role,
    get_current_user,
    get_session,
    require_role,
    role_at_least,
)
from forge.models import AuthProvider, ConnectorInstall, Tool
from forge.secrets.store import SecretNotFound, SecretStore
from forge.services.auth_providers import AuthProviderService
from forge.util.locks import KeyedLocks

log = logging.getLogger("forge.connectors")

router = APIRouter(prefix="/v1/projects/{project_id}/connectors", tags=["connectors"])


# --- payloads ------------------------------------------------------------------------------

class InstallIn(BaseModel):
    # Credential values keyed by the manifest's setup[].key, for the CUSTOM path only - a
    # catalog install ignores both fields (its app comes from the environment and its accounts
    # are always personal). Secret values are written to the SecretStore immediately and never
    # echoed back by any route on this router.
    values: dict[str, str] = Field(default_factory=dict)
    auth_mode: str = "shared"  # shared | per_user (ignored when the manifest forces one)


class CustomInstallIn(InstallIn):
    manifest: dict[str, Any]


class ConnectIn(BaseModel):
    # For a per-user connector an editor may connect ON BEHALF OF a specific end user id;
    # omitted means "connect as me", which is what the self-service flow sends.
    end_user_id: str | None = None


class CredentialsIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


# --- serialization -------------------------------------------------------------------------

def _manifest_out(m: ConnectorManifest, installed: ConnectorInstall | None = None,
                  *, managed: bool = True, group_ready: bool = False) -> dict:
    """Catalog-card shape. Never includes credential values.

    `managed` (every catalog entry) means the vendor app comes from the deployment environment
    and there is nothing for anyone to type: the card shows a Connect button, or - when the
    operator hasn't registered that vendor yet - says exactly which env keys are missing.
    Unmanaged is the custom-manifest path, which still renders `auth.setup` as a form.
    """
    ready = env_ready(m) if managed else group_ready
    return {
        "credential_group": m.group,
        "managed": managed,
        # Ready to connect right now, with nothing asked of the person clicking.
        "configured": ready,
        "missing_keys": missing_app_keys(m) if managed else [],
        "config_env_key": ENV_VAR,
        # The EXACT string sent to the vendor as redirect_uri, so what an operator whitelists is
        # what the flow uses. It is derived from FORGE_PUBLIC_BASE_URL (this API's public origin),
        # which is not the console's origin - guessing it from the browser produces a URL that
        # looks right and fails with redirect_uri_mismatch.
        "redirect_uri": oauth_redirect_uri({}),
        "slug": m.slug,
        "name": m.name,
        "version": m.version,
        "publisher": m.publisher,
        "summary": m.summary,
        "categories": m.categories,
        "roles": m.roles,
        "icon": m.icon,
        "docs_url": m.docs_url,
        "setup_url": m.setup_url,
        "type": m.kind_label,  # "MCP" | "REST"
        "auth": {
            "kind": m.auth.kind,
            "per_user": m.auth.per_user,
            "scopes": m.auth.scopes,
            "setup_help": m.auth.setup_help,
            "setup": [f.model_dump() for f in m.auth.setup],
        },
        "action_count": (
            len(m.backend.actions) if not isinstance(m.backend, McpBackend) else None
        ),
        "installed": installed is not None,
        "status": installed.status if installed else None,
        "install_id": installed.id if installed else None,
    }


async def _live_tool_counts(session: AsyncSession, tenant_id: str, project_id: str,
                            rows: list[ConnectorInstall]) -> dict[str, int]:
    """How many of each install's actions STILL EXIST, keyed by install id.

    A user may have deleted an action from the Tools screen; reporting the install's original
    count would then be a lie. Every route that reports a count uses this one - when only the
    list route did, deleting two of Gmail's five actions left the gallery correctly showing 3
    while the detail panel (which polls `/{slug}/status`) insisted on 5.
    """
    ids = [tid for r in rows for tid in (r.created_tool_ids or [])]
    if not ids:
        return {r.id: 0 for r in rows}
    found = await session.execute(
        select(Tool.id).where(Tool.tenant_id == tenant_id, Tool.project_id == project_id, Tool.id.in_(ids))
    )
    alive = {r[0] for r in found.all()}
    return {r.id: len([t for t in (r.created_tool_ids or []) if t in alive]) for r in rows}


def _install_out(row: ConnectorInstall, *, tool_count: int) -> dict:
    manifest = row.manifest or {}
    backend = manifest.get("backend") or {}
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "version": row.version,
        "source": row.source,
        "status": row.status,
        "status_detail": row.status_detail,
        "auth_mode": row.auth_mode,
        "auth_kind": (manifest.get("auth") or {}).get("kind", "none"),
        "type": "MCP" if backend.get("type") == "mcp" else "REST",
        "icon": manifest.get("icon"),
        "summary": manifest.get("summary", ""),
        "docs_url": manifest.get("docs_url"),
        "tool_set_id": row.tool_set_id,
        "auth_provider_id": row.auth_provider_id,
        "mcp_client_id": row.mcp_client_id,
        # Required, deliberately: the old default counted `created_tool_ids`, so a caller that
        # forgot to pass one silently reported actions the user had deleted.
        "tool_count": tool_count,
        "needs_connect": row.status == "needs_auth",
    }


async def _load_install(session: AsyncSession, tenant_id: str, project_id: str, slug: str) -> ConnectorInstall:
    row = await ConnectorInstaller.get_install(session, tenant_id, project_id, slug)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector is not installed in this project")
    return row


async def _target_end_user(request: Request, user: CurrentUser, requested: str | None) -> str:
    """Which end user a per-user connect/disconnect acts on.

    Acting on YOURSELF is open to any real logged-in user (that is the point of self-service
    per-user credentials). Acting on someone ELSE is an editor action, checked here rather than
    via Depends because it is conditional on the request body."""
    if str(user.id).startswith(("apikey:", "service")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "per-user credentials require a user identity")
    target = requested or str(user.id)
    if target != str(user.id) and not role_at_least(await effective_role(user, request), "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "connecting on behalf of another user requires role 'editor'")
    return target


# --- catalog -------------------------------------------------------------------------------

@router.get("/catalog")
async def list_catalog(project_id: str, session: AsyncSession = Depends(get_session),
                       tenant_id: str = Depends(current_tenant_id)):
    """The bundled catalog, each entry flagged with whether this project already has it.

    Reads a directory of JSON files - no network call, no third-party service, no API key. This
    route works identically on an air-gapped install. Whether an entry is CONNECTABLE is read
    from the deployment's environment, which is also free: no per-connector secret lookups just
    to paint a page."""
    installs = {i.slug: i for i in await ConnectorInstaller.list_installs(session, tenant_id, project_id)}
    return {
        "connectors": [_manifest_out(m, installs.get(m.slug)) for m in catalog_mod.list_manifests()],
        "categories": catalog_mod.categories(),
        "roles": catalog_mod.roles(),
    }


@router.get("/catalog/{slug}")
async def get_catalog_entry(project_id: str, slug: str, session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id)):
    manifest = catalog_mod.get_manifest(slug)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found in the catalog")
    install = await ConnectorInstaller.get_install(session, tenant_id, project_id, slug)
    out = _manifest_out(manifest, install)
    if not isinstance(manifest.backend, McpBackend):
        out["actions"] = [
            {"name": a.name, "description": a.description,
             "method": (a.request or {}).get("method", "GET")}
            for a in manifest.backend.actions
        ]
    else:
        out["mcp_url"] = manifest.backend.url
    return out


# --- installed -----------------------------------------------------------------------------

@router.get("")
async def list_installed(project_id: str, session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         user: CurrentUser = Depends(get_current_user)):
    rows = await ConnectorInstaller.list_installs(session, tenant_id, project_id)
    if not rows:
        return []
    counts = await _live_tool_counts(session, tenant_id, project_id, rows)
    # One query for every provider rather than one per install: painting this screen for a
    # project with the full catalog installed was a dozen sequential SELECTs before the secret
    # reads even started.
    provider_ids = [r.auth_provider_id for r in rows if r.auth_provider_id]
    providers: dict[str, AuthProvider] = {}
    if provider_ids:
        found_aps = await session.execute(
            select(AuthProvider).where(
                AuthProvider.tenant_id == tenant_id, AuthProvider.id.in_(provider_ids)
            )
        )
        providers = {ap.id: ap for ap in found_aps.scalars()}
    # ...and one read for every token bundle, for the same reason.
    bundles = await _bundles_for(tenant_id, project_id, rows, providers, str(user.id))

    out = []
    for r in rows:
        item = _install_out(r, tool_count=counts[r.id])
        # Per-user connectors have no single "connected" answer, so the list resolves it for the
        # CALLER. Without this the gallery would show a green tick to everyone the moment one
        # colleague connected their own account, and the person clicking Connect would be told
        # they were already done.
        item["connected"] = await _connected_for(
            session, tenant_id, project_id, r, str(user.id),
            provider=providers.get(r.auth_provider_id or ""), bundles=bundles,
        )
        out.append(item)
    return out


#: Distinguishes "the bundle secret does not exist" from "it exists and is unusable". Only the
#: first means a key/bearer connector is still connected via its own credential.
_MISSING = object()


def _bundle_name_for(row: ConnectorInstall, ap: AuthProvider, user_id: str) -> str:
    """The secret holding the OAuth bundle that decides whether this connector is usable by
    `user_id` right now - the caller's own for a per-user connector, the project's otherwise."""
    if row.auth_mode == "per_user":
        return AuthResolver.bundle_secret_name(ap.id, {"end_user_id": user_id}, ["end_user_id"])
    return AuthResolver.bundle_secret_name(ap.id)


async def _connection_state(tenant_id: str, project_id: str, row: ConnectorInstall,
                            ap: AuthProvider, user_id: str, *, bundle: Any = _MISSING,
                            prefetched: bool = False) -> dict:
    """`{connected, expires_at}` for one caller against one connector's auth provider.

    The single source of truth for "is this usable right now", shared by the list route and the
    per-connector status route. The two answer for different scopes but must agree on the rule -
    when they drifted, the gallery showed a green tick to someone the detail panel then asked to
    sign in.

    `prefetched` says the caller already read the bundle (see `_bundles_for`), so this does no
    I/O at all; `bundle` is then the value, or `_MISSING` if that secret does not exist.
    """
    if not prefetched:
        try:
            bundle = await SecretStore().read_ref(
                tenant_id=tenant_id, project_id=project_id,
                ref=f"secret://proj/{_bundle_name_for(row, ap, user_id)}",
            )
        except SecretNotFound:
            bundle = _MISSING
        except Exception:  # noqa: BLE001 - an undecodable bundle is not a usable connection
            bundle = None
    if row.auth_mode == "per_user":
        # A per-user connector has no project-wide answer: a colleague having connected their
        # own mailbox says nothing about yours. And no bundle means not connected, full stop -
        # there is no per-user equivalent of a shared key sitting in the provider config.
        connected = isinstance(bundle, dict) and bool(bundle.get("access_token"))
        return {"connected": connected, "expires_at": bundle.get("expires_at") if connected else None}
    if bundle is _MISSING:
        # Only OAuth stores a token bundle; for a key/bearer connector the credential itself is
        # the connection, so an absent bundle is not "disconnected".
        return {"connected": ap.kind != "oauth2_authorization_code", "expires_at": None}
    if not isinstance(bundle, dict):
        return {"connected": False, "expires_at": None}
    return {"connected": bool(bundle.get("access_token")), "expires_at": bundle.get("expires_at")}


async def _bundles_for(tenant_id: str, project_id: str, rows: list[ConnectorInstall],
                       providers: dict[str, AuthProvider], user_id: str) -> dict[str, Any]:
    """Every token bundle the installed list needs, in one read.

    A project with the full catalog installed painted this screen with a dozen SEQUENTIAL secret
    reads - a round trip, a decrypt and an audit write each - and the connect flow calls reload()
    on window focus, so it repeated every time someone came back from a consent window.
    """
    names = [
        _bundle_name_for(r, ap, user_id)
        for r in rows
        if (ap := providers.get(r.auth_provider_id or "")) is not None
    ]
    if not names:
        return {}
    return await SecretStore().read_refs(
        tenant_id=tenant_id, project_id=project_id,
        refs=[f"secret://proj/{n}" for n in names],
    )


async def _connected_for(session: AsyncSession, tenant_id: str, project_id: str,
                         row: ConnectorInstall, user_id: str,
                         *, provider: AuthProvider | None = None,
                         bundles: dict[str, Any] | None = None) -> bool:
    """Whether THIS user can currently act through this connector.

    `provider` and `bundles` let a caller that already batched those reads pass them in; without
    them this reads both itself, which is what the single-connector status route wants."""
    if not row.auth_provider_id:
        return True
    ap = provider or await AuthProviderService.get(session, tenant_id, row.auth_provider_id)
    if ap is None:
        return False
    if bundles is None:
        state = await _connection_state(tenant_id, project_id, row, ap, user_id)
    else:
        name = _bundle_name_for(row, ap, user_id)
        state = await _connection_state(
            tenant_id, project_id, row, ap, user_id,
            bundle=bundles.get(name, _MISSING), prefetched=True,
        )
    return bool(state.get("connected"))


@router.post("/{slug}/install", status_code=201)
async def install_connector(project_id: str, slug: str, body: InstallIn | None = None,
                            session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id),
                            _: CurrentUser = Depends(require_role("editor"))):
    """Add a catalog connector without connecting to it yet.

    `POST /{slug}/connect` does this implicitly, which is the path the UI takes. This route is
    what you want when adding the connector and signing in are done by different people - an
    editor wires up the tools, and everyone else connects their own account afterwards.
    """
    manifest = catalog_mod.get_manifest(slug)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found in the catalog")
    try:
        row = await ConnectorInstaller().install(
            session, tenant_id, project_id, manifest, source="catalog",
        )
    except InstallError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    # Just created: nothing has had a chance to be deleted, so the receipt IS the live count.
    return _install_out(row, tool_count=len(row.created_tool_ids or []))


@router.post("/custom", status_code=201)
async def install_custom(project_id: str, body: CustomInstallIn,
                         session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         _: CurrentUser = Depends(require_role("editor"))):
    """Install a manifest the user supplied. Same validator, same install path, same rows -
    a custom connector is not a second-class citizen, it just isn't in the bundled catalog."""
    try:
        manifest = parse_manifest(body.manifest)
    except ManifestError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Invalid manifest - {e}") from e
    try:
        row = await ConnectorInstaller().install(
            session, tenant_id, project_id, manifest,
            values=body.values, auth_mode=body.auth_mode, source="custom",
        )
    except InstallError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return _install_out(row, tool_count=len(row.created_tool_ids or []))


@router.get("/examples")
async def list_examples(project_id: str, _: CurrentUser = Depends(require_role("editor"))):
    """Ready-made manifests for services that CAN'T be one-click: an API key, a bot token or a
    per-tenant subdomain has to come from somewhere, and that somewhere is a person typing it.

    They are offered as starting points for the custom-connector form - open one, adjust it, add
    your key - rather than sitting in the gallery pretending to be one click away.
    """
    return [
        {
            "slug": m.slug, "name": m.name, "summary": m.summary, "icon": m.icon,
            "type": m.kind_label, "auth_kind": m.auth.kind,
            "needs": [f.label for f in m.auth.setup if f.required],
            # The file as authored, so what lands in the form is editable and re-installable.
            "manifest": source,
        }
        for m, source in catalog_mod.list_examples()
    ]


@router.post("/validate")
async def validate_manifest(project_id: str, body: dict,
                            _: CurrentUser = Depends(require_role("editor"))):
    """Dry-run a manifest so the paste-a-manifest form can show errors before anything is
    created. Creates nothing and touches no credentials."""
    try:
        manifest = parse_manifest(body.get("manifest") if "manifest" in body else body)
    except ManifestError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "connector": _manifest_out(manifest, managed=False), "hosts": manifest.hosts()}


@router.delete("/{slug}", status_code=204)
async def uninstall_connector(project_id: str, slug: str, session: AsyncSession = Depends(get_session),
                              tenant_id: str = Depends(current_tenant_id),
                              _: CurrentUser = Depends(require_role("editor"))):
    row = await _load_install(session, tenant_id, project_id, slug)
    await ConnectorInstaller().uninstall(session, row)


# --- credentials + connect -----------------------------------------------------------------

@router.put("/{slug}/credentials", status_code=204)
async def set_credentials(project_id: str, slug: str, body: CredentialsIn,
                          session: AsyncSession = Depends(get_session),
                          tenant_id: str = Depends(current_tenant_id),
                          _: CurrentUser = Depends(require_role("editor"))):
    """Update the connector's stored setup values (rotate a client secret, fix a typo'd key).

    Only keys the manifest declares are accepted; an unknown key is ignored rather than written,
    so this route can never be used to plant an arbitrary secret under a connector's namespace.
    """
    row = await _load_install(session, tenant_id, project_id, slug)
    if row.source == "catalog":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{row.name}'s credentials belong to the deployment, not the project - rotate the "
            f"{ENV_VAR} entry and restart the API. Everyone's own sign-in survives the rotation.",
        )
    manifest = parse_manifest(row.manifest or {})
    declared = {f.key: f for f in manifest.auth.setup}
    store = SecretStore()
    written = list(row.created_secret_names or [])
    for key, value in body.values.items():
        field = declared.get(key)
        if field is None or not field.secret or not value:
            continue
        name = secret_name(manifest.group, key)
        await store.write(session, tenant_id=tenant_id, project_id=project_id,
                          name=name, value=value, kind="connector")
        if name not in written:
            written.append(name)
    row.created_secret_names = written
    if row.status == "needs_setup":
        row.status = "needs_auth" if manifest.auth.kind == "oauth2_authorization_code" else "connected"
    await session.commit()


@router.post("/{slug}/connect")
async def connect_connector(request: Request, project_id: str, slug: str, body: ConnectIn | None = None,
                            session: AsyncSession = Depends(get_session),
                            tenant_id: str = Depends(current_tenant_id),
                            user: CurrentUser = Depends(get_current_user)):
    """Begin the OAuth consent for this connector and return the URL to open.

    This is the WHOLE flow from the user's side: one call, one browser round trip, connected.
    If the project doesn't have the connector yet it is installed here rather than being a
    separate step the person has to know about - "install" is Forge's internal bookkeeping
    (create an auth provider, a tool set, a tool per action), not a decision anyone came here
    to make.

    For an MCP-backed connector whose manifest opted into discovery, the server's OAuth metadata
    is fetched and a client is registered dynamically FIRST - that is what makes a one-click
    connect possible against servers that support it, with the static manifest endpoints as the
    fallback for those that don't.
    """
    row = await ConnectorInstaller.get_install(session, tenant_id, project_id, slug)
    if row is None:
        row = await _install_on_demand(request, session, tenant_id, project_id, slug, user)
    if not row.auth_provider_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this connector needs no connection")
    ap = await AuthProviderService.get(session, tenant_id, row.auth_provider_id)
    if ap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "auth provider missing for this connector")

    per_user = bool((ap.config or {}).get("per_user_context_keys"))
    if per_user:
        target = await _target_end_user(request, user, body.end_user_id if body else None)
        context = {"end_user_id": target}
    else:
        # A SHARED credential is the project's, not the caller's - setting it up is an editor
        # action even though connecting your own per-user account is not.
        if not role_at_least(await effective_role(user, request), "editor"):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "requires role 'editor' or higher")
        context = {}

    await _ensure_oauth_endpoints(session, tenant_id, project_id, row, ap)
    try:
        url = await build_authorize_url(ap, tenant_id=tenant_id, project_id=project_id, context=context)
    except OAuthNotConfigured as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e
    return {"authorize_url": url, "per_user": per_user}


async def _install_on_demand(request: Request, session: AsyncSession, tenant_id: str, project_id: str,
                             slug: str, user: CurrentUser) -> ConnectorInstall:
    """Add a catalog connector to the project as part of connecting to it.

    Adding a connector creates project-level rows (tools a workflow can be built on), so it stays
    an editor action. A viewer who is first through the door gets told what to ask for rather
    than a 404 - and once an editor has added it, every other person's connect is just their own
    sign-in.
    """
    manifest = catalog_mod.get_manifest(slug)
    if manifest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "connector is not installed in this project")
    if not env_ready(manifest):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, not_configured_message(manifest))
    if not role_at_least(await effective_role(user, request), "editor"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"{manifest.name} hasn't been added to this project yet. An editor adds it once, then "
            "everyone connects their own account.",
        )
    try:
        return await ConnectorInstaller().install(
            session, tenant_id, project_id, manifest, source="catalog",
        )
    except InstallError as e:
        # Two people can click Connect on the same connector at the same moment; the loser's
        # install is rejected as a duplicate. That is the right outcome for the ROW and the wrong
        # one for the person - the connector they asked for now exists, so join it and carry on
        # to their sign-in rather than telling them it is "already installed".
        existing = await ConnectorInstaller.get_install(session, tenant_id, project_id, slug)
        if existing is not None:
            return existing
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e


#: Serializes the once-per-install discovery + dynamic client registration. Two people clicking
#: Connect on the same connector in the same second would otherwise both register a client and
#: both write the credential: last writer wins, and the first person's browser is already sitting
#: on an authorize URL carrying a client_id whose secret has just been replaced, so their callback
#: fails at the token exchange with nothing to explain it.
#:
#: IN-PROCESS ONLY, like the OAuth refresh locks in auth_providers/resolver.py. Across scaled
#: `api` replicas the two connects can still collide; the stored-credential re-check below is
#: what keeps that case converging on one registered client rather than flip-flopping.
_discovery_locks = KeyedLocks()


async def _ensure_oauth_endpoints(session: AsyncSession, tenant_id: str, project_id: str,
                                  row: ConnectorInstall, ap: AuthProvider) -> None:
    """Fill in authorize/token endpoints (and a client registration) for a discovery connector.

    Runs at most once per install: the discovered values are written back onto the provider
    config, so the second connect is a plain authorize-URL build with no extra round trips.
    """
    if not (ap.config or {}).get("oauth_discover"):
        return
    lock = await _discovery_locks.acquire_cm(f"{tenant_id}:{project_id}:{ap.id}")
    async with lock:
        # Re-read INSIDE the lock: a peer may have completed the whole dance while we waited,
        # in which case there is nothing left to do and re-registering would replace their app.
        await session.refresh(ap)
        await _discover_and_register(session, tenant_id, project_id, row, ap)


async def _discover_and_register(session: AsyncSession, tenant_id: str, project_id: str,
                                 row: ConnectorInstall, ap: AuthProvider) -> None:
    cfg = dict(ap.config or {})
    if cfg.get("authorize_url") and cfg.get("token_url"):
        return
    manifest = parse_manifest(row.manifest or {})
    if not isinstance(manifest.backend, McpBackend):
        return

    from forge.connectors import mcp_auth

    found = await mcp_auth.discover(manifest.backend.url)
    if found.get("authorize_url"):
        cfg["authorize_url"] = found["authorize_url"]
    if found.get("token_url"):
        cfg["token_url"] = found["token_url"]
    if not cfg.get("authorize_url") or not cfg.get("token_url"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{manifest.name} did not publish OAuth metadata. Add the authorize/token URLs to the "
            "connector's Auth Provider, or register an app with the vendor and paste its credentials.",
        )
    # RFC 8707 - bind issued tokens to this specific MCP server.
    cfg["resource"] = manifest.backend.url

    store = SecretStore()
    have_client = False
    if cfg.get("client_id_ref"):
        try:
            have_client = bool(await store.read_ref(tenant_id=tenant_id, project_id=project_id, ref=cfg["client_id_ref"]))
        except SecretNotFound:
            have_client = False
    if not have_client and found.get("registration_url"):
        try:
            reg = await mcp_auth.register_client(
                found["registration_url"], client_name="Forge",
                redirect_uri=mcp_auth.callback_url(),
                scopes=manifest.auth.scopes or found.get("scopes_supported") or [],
            )
        except Exception as e:  # noqa: BLE001 - fall through to "paste your own app credentials"
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Could not register with {manifest.name} automatically ({e}). Create an app with the "
                "vendor and paste its Client ID/Secret in the connector's settings.",
            ) from e
        # Last check before writing. The in-process lock can't see a peer on ANOTHER replica
        # that registered while we were talking to the vendor; if one did, keep theirs and
        # discard the client we just registered. Both sides then converge on a single stored
        # app instead of overwriting each other and stranding whoever is mid-consent.
        try:
            if await store.read_ref(tenant_id=tenant_id, project_id=project_id,
                                    ref=f"secret://proj/{secret_name(manifest.group, 'client_id')}"):
                log.info("connector %s: another registration won the race; keeping it", manifest.slug)
                reg = {}
        except SecretNotFound:
            pass
        names = list(row.created_secret_names or [])
        for key, value in (("client_id", reg.get("client_id")), ("client_secret", reg.get("client_secret"))):
            if not value:
                continue
            name = secret_name(manifest.group, key)
            await store.write(session, tenant_id=tenant_id, project_id=project_id,
                              name=name, value=value, kind="connector")
            if name not in names:
                names.append(name)
        row.created_secret_names = names
        cfg["client_id_ref"] = f"secret://proj/{secret_name(manifest.group, 'client_id')}"
        cfg["client_secret_ref"] = f"secret://proj/{secret_name(manifest.group, 'client_secret')}"
    if not cfg.get("scope") and (manifest.auth.scopes or found.get("scopes_supported")):
        cfg["scope"] = " ".join(manifest.auth.scopes or found.get("scopes_supported") or [])
    ap.config = cfg
    await session.commit()


@router.get("/{slug}/status")
async def connector_status(project_id: str, slug: str, session: AsyncSession = Depends(get_session),
                           tenant_id: str = Depends(current_tenant_id),
                           user: CurrentUser = Depends(get_current_user)):
    """Whether this connector is usable right now - and for a per-user connector, whether the
    CALLER personally has connected it (which is the only status that matters to them)."""
    row = await _load_install(session, tenant_id, project_id, slug)
    counts = await _live_tool_counts(session, tenant_id, project_id, [row])
    out = _install_out(row, tool_count=counts[row.id])
    if not row.auth_provider_id:
        out["connected"] = True
        return out
    ap = await AuthProviderService.get(session, tenant_id, row.auth_provider_id)
    if ap is None:
        out["connected"] = False
        return out
    state = await _connection_state(tenant_id, project_id, row, ap, str(user.id))
    out["connected"] = bool(state.get("connected"))
    out["expires_at"] = state.get("expires_at")
    return out


@router.post("/{slug}/sync")
async def sync_connector(request: Request, project_id: str, slug: str,
                         session: AsyncSession = Depends(get_session),
                         tenant_id: str = Depends(current_tenant_id),
                         user: CurrentUser = Depends(get_current_user)):
    """Bring a connector's actions back in line with its manifest.

    Two different operations behind one button, with two different gates:

    * MCP - ask the server what it exposes, using the CALLER's credential because that is the
      only one a per-user connector has. Open to anyone who can connect: gating it would leave
      whoever actually signed in staring at zero actions, and the rows created are entirely
      determined by what the vendor advertises for a connector an editor already added.
    * REST - re-apply the bundled manifest over existing Tool rows. That is a project-level
      write (it rewrites tool configs everyone's workflows run), so it stays editor-gated even
      though the values are deterministic.
    """
    row = await _load_install(session, tenant_id, project_id, slug)
    # `or {}` on the inner get too: a stored manifest with an explicit null backend would make
    # `.get("backend", {})` return None and the next .get() raise, 500ing instead of falling
    # through to the safe editor-gated branch.
    if ((row.manifest or {}).get("backend") or {}).get("type") != "mcp":
        if not role_at_least(await effective_role(user, request), "editor"):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "refreshing this connector's actions rewrites project tools - requires role 'editor'",
            )
    context = {"end_user_id": str(user.id)} if row.auth_mode == "per_user" else None
    try:
        count = await ConnectorInstaller().sync_tools(session, row, context=context)
    except Exception as e:  # noqa: BLE001 - report, don't 500: the usual cause is "not connected yet"
        from forge.tools.mcp import describe_mcp_error

        return {"ok": False, "error": describe_mcp_error(e)}
    return {"ok": True, "tool_count": count}


@router.post("/{slug}/disconnect", status_code=204)
async def disconnect_connector(request: Request, project_id: str, slug: str, body: ConnectIn | None = None,
                               session: AsyncSession = Depends(get_session),
                               tenant_id: str = Depends(current_tenant_id),
                               user: CurrentUser = Depends(get_current_user)):
    """Revoke the stored token WITHOUT uninstalling: the tools stay wired into workflows and
    agents, they just stop being able to act until reconnected. Uninstall is the destructive one."""
    row = await _load_install(session, tenant_id, project_id, slug)
    if not row.auth_provider_id:
        return
    ap = await AuthProviderService.get(session, tenant_id, row.auth_provider_id)
    if ap is None:
        return
    if row.auth_mode == "per_user":
        target = await _target_end_user(request, user, body.end_user_id if body else None)
        await AuthProviderService.clear_user_connection(session, tenant_id, project_id, ap, target)
        return
    if not role_at_least(await effective_role(user, request), "editor"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "requires role 'editor' or higher")
    await SecretStore().write(
        session, tenant_id=tenant_id, project_id=project_id,
        name=AuthResolver.bundle_secret_name(ap.id), value={}, kind="oauth",
    )
    row.status = "needs_auth"
    await session.commit()
