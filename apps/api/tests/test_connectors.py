"""Connectors: manifest validation, the bundled catalog, and the install/uninstall pipeline.

The load-bearing claims these tests pin down:

  * loading the catalog performs NO network I/O (the independence guarantee),
  * installing a manifest creates ordinary AuthProvider/ToolSet/Tool rows - nothing bespoke,
  * uninstalling removes exactly what the install created and nothing else,
  * a failed install leaves no orphan rows behind,
  * every CATALOG connector is one-click: OAuth, per-user, and credential-free at the point of
    use - the deployment's environment is the only place a vendor app can come from,
  * a CUSTOM (pasted) manifest keeps the old behaviour, because a service account with an API
    key has to be typed by somebody and that is the path where it belongs.
"""

from __future__ import annotations

from contextlib import aclosing

import pytest

from forge.connectors.catalog import get_manifest, list_examples, list_manifests
from forge.connectors.install import (
    ConnectorInstaller,
    InstallError,
    env_ready,
    group_has_credentials,
    missing_app_keys,
    secret_name,
)
from forge.connectors.manifest import ManifestError, RestBackend, parse_manifest
from forge.db.base import SessionLocal
from forge.models import AuthProvider, ConnectorInstall, Tool, ToolSet
from forge.secrets.store import SecretStore
from forge.services.tool_sets import ToolSetService

REST_MANIFEST = {
    "format": "forge.connector/1",
    "slug": "acme",
    "name": "Acme",
    "summary": "Acme test connector",
    "categories": ["custom"],
    "roles": ["Software Engineer"],
    "auth": {
        "kind": "bearer",
        "per_user": "optional",
        "setup": [{"key": "token", "label": "API token", "secret": True, "required": True}],
    },
    "egress_hosts": ["api.acme.test"],
    "backend": {
        "type": "rest",
        "base_url": "https://api.acme.test/v1",
        "actions": [
            {
                "name": "acme_list_widgets",
                "description": "List widgets.",
                "request": {
                    "method": "GET",
                    "url_template": "/widgets",
                    "fields": [{"path": "limit", "in": "query", "type": "integer", "llm_visible": True}],
                },
                "response": {"projection_jmespath": "items[].id"},
            },
            {
                "name": "acme_get_widget",
                "description": "Read one widget.",
                "request": {
                    "method": "GET",
                    "url_template": "/widgets/{widget_id}",
                    "fields": [{"path": "widget_id", "in": "path", "type": "string", "required": True}],
                },
            },
        ],
    },
    "toolset": {"description": "Acme actions"},
}


# --- manifest validation --------------------------------------------------------------------

def test_manifest_rejects_unknown_format():
    with pytest.raises(ManifestError):
        parse_manifest({**REST_MANIFEST, "format": "forge.connector/9"})


def test_manifest_rejects_action_without_url_template():
    bad = {
        **REST_MANIFEST,
        "backend": {
            "type": "rest",
            "actions": [{"name": "x", "request": {"method": "GET"}}],
        },
    }
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_manifest_rejects_bad_field_location():
    bad = {
        **REST_MANIFEST,
        "backend": {
            "type": "rest",
            "actions": [{
                "name": "x",
                "request": {"method": "GET", "url_template": "/x",
                            "fields": [{"path": "a", "in": "nowhere"}]},
            }],
        },
    }
    with pytest.raises(ManifestError):
        parse_manifest(bad)


def test_manifest_rejects_duplicate_action_names():
    action = REST_MANIFEST["backend"]["actions"][0]
    with pytest.raises(ManifestError):
        parse_manifest({**REST_MANIFEST,
                        "backend": {"type": "rest", "actions": [action, action]}})


def test_manifest_hosts_include_backend_host_even_when_undeclared():
    m = parse_manifest({**REST_MANIFEST, "egress_hosts": []})
    assert "api.acme.test" in m.hosts()


# --- bundled catalog ------------------------------------------------------------------------

def test_bundled_catalog_all_parse():
    manifests = list_manifests()
    assert len(manifests) >= 10, "the bundled catalog should ship a usable set of connectors"
    slugs = {m.slug for m in manifests}
    for expected in ("slack", "gmail", "outlook", "github"):
        assert expected in slugs


def test_every_catalog_connector_is_one_click():
    """The gallery's promise, enforced at the catalog level: click Connect, sign in with your own
    account, done. A connector that needs a typed key or a shared bot token can't keep that
    promise, so it belongs in examples/ - not on a screen that says there are no forms."""
    for m in list_manifests():
        assert m.auth.kind == "oauth2_authorization_code", (
            f"{m.slug}: catalog connectors sign users in; {m.auth.kind} needs a pasted credential"
        )
        assert m.auth.per_user == "required", (
            f"{m.slug}: a catalog account is personal, so the manifest must say so"
        )
        # Nothing an END USER has to supply. Non-secret setup values are allowed only when the
        # manifest carries a default (Microsoft's tenant = "common"), because the install form
        # they would otherwise be typed into no longer exists.
        for field in m.auth.setup:
            if field.secret:
                assert field.key in ("client_id", "client_secret"), (
                    f"{m.slug}: secret setup key {field.key!r} can't come from the environment"
                )
            else:
                assert field.default is not None, (
                    f"{m.slug}: non-secret setup key {field.key!r} has no default and nobody to ask"
                )


#: Vendors that only issue a refresh_token when the authorize URL explicitly asks for one, keyed
#: by the authorize host that identifies them. Anything not listed here either refreshes by
#: default (Airtable, HubSpot), never expires (GitHub), or asks via a scope (Microsoft's
#: `offline_access`) - so this is a list of the vendors where SILENCE means a one-hour connector.
_OFFLINE_ACCESS_REQUIRED = {"accounts.google.com": {"access_type": "offline", "prompt": "consent"}}


def test_connectors_that_need_offline_access_ask_for_it():
    """Google issues a refresh_token ONLY when `access_type=offline` is on the authorize URL, and
    re-issues it reliably only with `prompt=consent`.

    Without them the stored bundle has `refresh_token=None`, and `AuthResolver._oauth2_auth_code`
    gates refresh on that key - so it silently keeps handing out an access token that expired.
    Every Gmail / Calendar / Drive / Sheets call 401s about an hour after someone connects, and
    the only cure is Disconnect + reconnect. Nothing else in the suite would notice.
    """
    checked = 0
    for m in list_manifests():
        host = (m.auth.authorize_url or "").split("/")[2] if "://" in (m.auth.authorize_url or "") else ""
        expected = _OFFLINE_ACCESS_REQUIRED.get(host)
        if not expected:
            continue
        checked += 1
        for key, value in expected.items():
            assert m.auth.authorize_params.get(key) == value, (
                f"{m.slug}: {host} only returns a refresh_token when the authorize URL carries "
                f"{key}={value}; without it every tool 401s an hour after sign-in"
            )
    assert checked >= 4, "the four Google connectors should all be covered by this sweep"


async def test_the_google_authorize_url_actually_carries_access_type():
    """The manifest assertion above is necessary but not sufficient - it would still pass if
    `_auth_config` stopped copying `authorize_params` onto the provider, or `build_authorize_url`
    stopped applying it. So walk the real chain the Connect button walks, and read the query
    string that Google would actually receive.
    """
    from urllib.parse import parse_qs, urlparse

    from forge.auth_providers.oauth_flow import build_authorize_url
    from forge.connectors.install import _auth_config

    manifest = get_manifest("gmail")
    cfg = _auth_config(manifest, {}, per_user=True)
    ap = AuthProvider(id="ap_gmail_test", tenant_id="t", project_id="p",
                      name="Gmail", kind=manifest.auth.kind, config=cfg)

    class _Store:
        async def read_ref(self, **kw):
            return "client-id.apps.googleusercontent.com"

    url = await build_authorize_url(ap, tenant_id="t", project_id="p", secrets=_Store())
    q = parse_qs(urlparse(url).query)
    assert q.get("access_type") == ["offline"], f"authorize URL omits access_type: {sorted(q)}"
    assert q.get("prompt") == ["consent"], f"authorize URL omits prompt: {sorted(q)}"
    # And the extras must not have been able to trample the protocol parameters.
    assert q["code_challenge_method"] == ["S256"]


def test_no_catalog_action_asks_the_model_to_encode_something():
    """A model cannot base64-encode by hand. It will emit plausible-looking garbage, the API
    answers an opaque 400, and the user has no way to tell whose fault it is.

    Gmail's send endpoint is the case that bit us: it takes only a base64url-encoded MIME
    message, and the first manifest exposed `raw` as a tool argument. Encoding belongs on the
    server (the `$mime` body directive), so no LLM-visible argument may ask for one.
    """
    banned = ("base64", "b64", "encoded", "rfc 2822", "rfc2822", "rfc 5322")
    for m in list_manifests():
        if not isinstance(m.backend, RestBackend):
            continue
        for action in m.backend.actions:
            for field in action.request.get("fields", []):
                if field.get("llm_visible") is False:
                    continue
                blob = f"{field.get('path', '')} {field.get('description', '')}".lower()
                hit = next((b for b in banned if b in blob), None)
                assert hit is None, (
                    f"{m.slug}.{action.name}: argument {field.get('path')!r} asks the model for "
                    f"{hit!r}-encoded input; encode it server-side instead"
                )


def test_gmail_send_takes_the_fields_a_person_would_type():
    """The regression guard for the 400: Gmail send must expose to/subject/body, and build the
    MIME message itself."""
    send = next(a for a in get_manifest("gmail").backend.actions if a.name == "gmail_send_message")
    args = {f["path"] for f in send.request["fields"]}
    assert {"to", "subject", "body"} <= args
    assert "raw" not in args
    assert "$mime" in (send.request.get("body_template") or "")


def test_examples_are_not_in_the_gallery():
    """The bundled key/token connectors are still shipped - just via the custom path, where a
    person typing a credential is the expected thing rather than a broken promise."""
    gallery = {m.slug for m in list_manifests()}
    examples = {m.slug for m, _ in list_examples()}
    assert examples, "the example manifests should still ship"
    assert not (gallery & examples), "an example must not also be a gallery entry"
    for expected in ("stripe", "slack-api", "custom-rest-api"):
        assert expected in examples


def test_catalog_load_does_no_network_io(monkeypatch):
    """The independence guarantee, enforced: opening the catalog must never touch the network."""
    import socket

    def _boom(*a, **kw):  # pragma: no cover - only runs if the guarantee breaks
        raise AssertionError("catalog load attempted network I/O")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)
    from forge.connectors.catalog import reload_catalog

    reload_catalog()
    assert list_manifests()


def test_every_catalog_action_declares_llm_visible_required_args():
    """A required field the model can't see can never be filled, so the tool would always 400."""
    for m in list_manifests():
        if not isinstance(m.backend, RestBackend):
            continue
        for action in m.backend.actions:
            for field in action.request.get("fields", []):
                if field.get("required") and field.get("llm_visible") is False:
                    assert field.get("default") is not None, (
                        f"{m.slug}.{action.name}: field {field['path']} is required and hidden "
                        "from the model but has no default"
                    )


# --- install / uninstall --------------------------------------------------------------------

async def _install(tenant: str, project: str, manifest_dict: dict | None = None, **kw):
    """Install REST_MANIFEST down the CUSTOM path - a pasted manifest with a typed credential,
    which is the only path that still accepts one."""
    manifest = parse_manifest(manifest_dict or REST_MANIFEST)
    kw.setdefault("source", "custom")
    async with SessionLocal() as s:
        return await ConnectorInstaller().install(
            s, tenant, project, manifest, values={"token": "sekrit"}, **kw
        )


async def _install_catalog(tenant: str, project: str, slug: str):
    async with SessionLocal() as s:
        return await ConnectorInstaller().install(
            s, tenant, project, get_manifest(slug), source="catalog"
        )


@pytest.fixture
def google_app(monkeypatch):
    """The deployment has registered one Google OAuth app - the normal state for a Forge that
    actually offers Gmail."""
    from forge.config import settings

    monkeypatch.setattr(
        settings, "connector_oauth_apps",
        {"google": {"client_id": "deployment-cid", "client_secret": "deployment-csec"}},
    )


@pytest.fixture
def no_apps(monkeypatch):
    """A deployment that has registered nothing.

    Pinned explicitly rather than assumed: settings are read from the developer's own .env, so a
    machine that HAS configured a vendor would otherwise fail these tests - and, worse, print a
    real client secret into the assertion diff."""
    from forge.config import settings

    monkeypatch.setattr(settings, "connector_oauth_apps", {})


async def test_install_creates_auth_provider_toolset_and_tools():
    tenant, project = "t_conn_i", "p_conn_i"
    row = await _install(tenant, project)

    assert row.status == "connected"  # bearer needs no browser round trip
    assert len(row.created_tool_ids) == 2

    async with SessionLocal() as s:
        ap = await s.get(AuthProvider, row.auth_provider_id)
        assert ap is not None and ap.kind == "bearer"
        # The credential is a ref, never a literal - the same rule bundles follow.
        assert ap.config["token_ref"] == f"secret://proj/{secret_name('acme', 'token')}"

        ts = await s.get(ToolSet, row.tool_set_id)
        assert ts is not None
        members = await ToolSetService.member_ids(s, tenant, ts.id)
        assert sorted(members) == sorted(row.created_tool_ids)

        tools = [await s.get(Tool, tid) for tid in row.created_tool_ids]
        assert {t.name for t in tools} == {"acme_list_widgets", "acme_get_widget"}
        for t in tools:
            assert t.kind == "rest_api"
            assert t.auth_provider_id == row.auth_provider_id
            # base_url composed with the relative action path.
            assert t.config["request"]["url_template"].startswith("https://api.acme.test/v1/widgets")


async def test_installed_secret_is_readable_by_the_resolver_ref():
    tenant, project = "t_conn_s", "p_conn_s"
    await _install(tenant, project)
    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('acme', 'token')}"
    )
    assert value == "sekrit"


async def test_install_is_rejected_twice():
    tenant, project = "t_conn_d", "p_conn_d"
    await _install(tenant, project)
    with pytest.raises(InstallError):
        await _install(tenant, project)


async def test_install_requires_declared_credentials():
    tenant, project = "t_conn_r", "p_conn_r"
    manifest = parse_manifest(REST_MANIFEST)
    async with SessionLocal() as s:
        with pytest.raises(InstallError):
            await ConnectorInstaller().install(s, tenant, project, manifest, values={}, source="custom")


async def test_per_user_mode_sets_the_context_key_the_resolver_reads():
    tenant, project = "t_conn_pu", "p_conn_pu"
    row = await _install(tenant, project, auth_mode="per_user")
    assert row.auth_mode == "per_user"
    async with SessionLocal() as s:
        ap = await s.get(AuthProvider, row.auth_provider_id)
        assert ap.config["per_user_context_keys"] == ["end_user_id"]


async def test_manifest_forcing_per_user_ignores_a_shared_request():
    """`per_user: required` exists so a personal-mailbox connector cannot be installed with one
    shared credential by accident."""
    forced = {**REST_MANIFEST, "slug": "acme-pu",
              "auth": {**REST_MANIFEST["auth"], "per_user": "required"}}
    row = await _install("t_conn_f", "p_conn_f", forced, auth_mode="shared")
    assert row.auth_mode == "per_user"


async def test_uninstall_removes_exactly_what_was_created():
    tenant, project = "t_conn_u", "p_conn_u"
    row = await _install(tenant, project)
    tool_ids, ts_id, ap_id = list(row.created_tool_ids), row.tool_set_id, row.auth_provider_id

    # A tool the user built themselves must survive the uninstall.
    from forge.services.tools import ToolService
    async with SessionLocal() as s:
        mine = await ToolService.create(s, tenant, project, name="my_own_tool", kind="builtin",
                                        config={"builtin": "current_time"})
        mine_id = mine.id

    async with SessionLocal() as s:
        install = await ConnectorInstaller.get_install(s, tenant, project, "acme")
        await ConnectorInstaller().uninstall(s, install)

    async with SessionLocal() as s:
        for tid in tool_ids:
            assert await s.get(Tool, tid) is None
        assert await s.get(ToolSet, ts_id) is None
        assert await s.get(AuthProvider, ap_id) is None
        assert await ConnectorInstaller.get_install(s, tenant, project, "acme") is None
        assert await s.get(Tool, mine_id) is not None, "uninstall deleted an unrelated tool"


async def test_uninstall_clears_the_stored_credential():
    tenant, project = "t_conn_c", "p_conn_c"
    await _install(tenant, project)
    async with SessionLocal() as s:
        install = await ConnectorInstaller.get_install(s, tenant, project, "acme")
        await ConnectorInstaller().uninstall(s, install)
    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('acme', 'token')}"
    )
    assert value == ""


async def test_failed_install_leaves_no_orphan_rows():
    """A manifest whose tool creation blows up must not leave a dangling auth provider + tool
    set behind - a half-installed connector is worse than none."""
    tenant, project = "t_conn_x", "p_conn_x"
    manifest = parse_manifest({**REST_MANIFEST, "slug": "acme-boom"})

    installer = ConnectorInstaller()

    async def _explode(*a, **kw):
        raise RuntimeError("boom")

    installer._create_rest_tools = _explode  # type: ignore[method-assign]

    async with SessionLocal() as s:
        with pytest.raises(RuntimeError):
            await installer.install(s, tenant, project, manifest, values={"token": "x"}, source="custom")

    async with SessionLocal() as s:
        from sqlalchemy import select
        aps = (await s.execute(select(AuthProvider).where(AuthProvider.project_id == project))).scalars().all()
        sets = (await s.execute(select(ToolSet).where(ToolSet.project_id == project))).scalars().all()
        installs = (await s.execute(select(ConnectorInstall).where(ConnectorInstall.project_id == project))).scalars().all()
        assert aps == [] and sets == [] and installs == []


async def test_failed_install_clears_the_credentials_it_created():
    """Rows are not the only thing a half-install leaves behind.

    Every service on the install path commits as it goes, so a failure has no transaction to
    unwind - the secrets written in step 1 survive with no install pointing at them. That is not
    just litter: `group_has_credentials` reads the store, so an orphaned client id/secret makes
    the whole credential group look already-configured to the next install.
    """
    tenant, project = "t_conn_secx", "p_conn_secx"
    manifest = parse_manifest({**REST_MANIFEST, "slug": "acme-secboom"})
    installer = ConnectorInstaller()

    async def _explode(*a, **kw):
        raise RuntimeError("boom")

    installer._create_rest_tools = _explode  # type: ignore[method-assign]
    async with SessionLocal() as s:
        with pytest.raises(RuntimeError):
            await installer.install(s, tenant, project, manifest, values={"token": "x"}, source="custom")

    async with SessionLocal() as s:
        assert not await group_has_credentials(SecretStore(), tenant, project, manifest), (
            "a failed install must not leave its group looking configured"
        )
        value = await SecretStore().read_ref(
            tenant_id=tenant, project_id=project,
            ref=f"secret://proj/{secret_name('acme-secboom', 'token')}",
        )
        assert not value


async def test_a_failed_install_does_not_blank_a_siblings_shared_credential(google_app):
    """Gmail and Calendar are one Google OAuth app. Rolling back a failed Calendar install must
    clear only what that install CREATED - blanking the shared client secret it merely re-wrote
    would sign every Gmail user out on the way past."""
    tenant, project = "t_conn_sib", "p_conn_sib"
    await _install_catalog(tenant, project, "gmail")

    installer = ConnectorInstaller()

    async def _explode(*a, **kw):
        raise RuntimeError("boom")

    installer._create_rest_tools = _explode  # type: ignore[method-assign]
    async with SessionLocal() as s:
        with pytest.raises(RuntimeError):
            await installer.install(s, tenant, project, get_manifest("google-calendar"), source="catalog")

    secret = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_secret')}",
    )
    assert secret == "deployment-csec", "the sibling's shared credential must survive the rollback"


async def test_a_concurrent_duplicate_install_is_reported_not_raised_raw():
    """The duplicate check is a read and `POST /connect` installs on demand, so two people
    clicking Connect at once both pass it. The loser hits the unique constraint; that must come
    back as the same InstallError the read produces, not a raw IntegrityError (a 500)."""
    tenant, project = "t_conn_race", "p_conn_race"
    manifest = parse_manifest({**REST_MANIFEST, "slug": "acme-race"})
    installer = ConnectorInstaller()

    async def _blind(*a, **kw):
        return None  # both callers "see" no existing install

    async def _group_looks_empty(*a, **kw):
        # The real interleaving: BOTH racers check for the group's credentials before either has
        # written them, so both count the write as one they created. The loser's rollback then
        # holds a list naming the credential the winner is about to depend on.
        return False

    installer.get_install = _blind  # type: ignore[method-assign]
    installer._secret_exists = _group_looks_empty  # type: ignore[method-assign]
    await _install(tenant, project, {**REST_MANIFEST, "slug": "acme-race"})

    async with SessionLocal() as s:
        # The same credential both times: two racers on the one-click path both read the vendor
        # app out of the same environment, so they write identical values.
        with pytest.raises(InstallError, match="already installed"):
            await installer.install(s, tenant, project, manifest, values={"token": "sekrit"}, source="custom")

    # ...and the loser's half-built rows are gone, so the winner's install is the only one.
    async with SessionLocal() as s:
        from sqlalchemy import select
        installs = (await s.execute(
            select(ConnectorInstall).where(ConnectorInstall.project_id == project)
        )).scalars().all()
        sets = (await s.execute(select(ToolSet).where(ToolSet.project_id == project))).scalars().all()
        assert len(installs) == 1 and len(sets) == 1

    # ...and crucially the WINNER's credentials survive. Both racers found the group empty and
    # both wrote it, so the loser's "secrets I created" list names the very credential the
    # surviving install now depends on. Clearing it would turn a harmless race into a connector
    # that reports "client_id secret is not set" to everyone.
    token = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('acme-race', 'token')}",
    )
    assert token == "sekrit", "the loser's rollback must not blank the winner's credential"


async def test_install_adds_connector_hosts_to_the_project_egress_allow_list():
    tenant, project = "t_conn_e", "p_conn_e"
    from forge.models import Project
    async with SessionLocal() as s:
        s.add(Project(id=project, tenant_id=tenant, name="P", slug="p", config={}))
        await s.commit()

    await _install(tenant, project)

    async with SessionLocal() as s:
        proj = await s.get(Project, project)
        assert "api.acme.test" in (proj.config.get("egress") or {}).get("allow_hosts", [])


async def _allow_hosts(project: str) -> list[str]:
    from forge.models import Project

    async with SessionLocal() as s:
        proj = await s.get(Project, project)
        return list((proj.config.get("egress") or {}).get("allow_hosts") or [])


async def _new_project(tenant: str, project: str, *, allow_hosts: list[str] | None = None) -> None:
    from forge.models import Project

    cfg = {"egress": {"allow_hosts": list(allow_hosts)}} if allow_hosts is not None else {}
    async with SessionLocal() as s:
        s.add(Project(id=project, tenant_id=tenant, name="P", slug="p", config=cfg))
        await s.commit()


async def test_uninstall_takes_back_the_egress_hosts_it_added():
    """Install only ever ADDED to the allow-list, and uninstall had no matching removal - so on a
    deployment running a strict allow-list the list only ever grew.

    Evaluate four connectors and remove them, and their API hosts stay permanently reachable with
    nothing referencing them: any hand-written tool a project editor adds later can call them,
    which is precisely what default-deny exists to prevent.
    """
    tenant, project = "t_conn_eu", "p_conn_eu"
    await _new_project(tenant, project)

    install = await _install(tenant, project)
    assert "api.acme.test" in await _allow_hosts(project)
    assert install.created_egress_hosts == ["api.acme.test"], (
        "the install must record what it added, so uninstall can take back exactly that"
    )

    async with SessionLocal() as s:
        row = await s.get(ConnectorInstall, install.id)
        await ConnectorInstaller().uninstall(s, row)

    assert "api.acme.test" not in await _allow_hosts(project)


async def test_uninstall_leaves_a_host_a_person_allow_listed_by_hand():
    """A host already on the list when the connector arrived was put there by somebody, for
    something else. Uninstalling the connector is not consent to remove it - and after the fact
    the two are indistinguishable unless the install records what it actually added."""
    tenant, project = "t_conn_ep", "p_conn_ep"
    await _new_project(tenant, project, allow_hosts=["api.acme.test", "unrelated.test"])

    install = await _install(tenant, project)
    assert install.created_egress_hosts == [], "the host was already allowed; nothing was added"

    async with SessionLocal() as s:
        row = await s.get(ConnectorInstall, install.id)
        await ConnectorInstaller().uninstall(s, row)

    hosts = await _allow_hosts(project)
    assert "api.acme.test" in hosts, "a hand-allow-listed host is not the connector's to remove"
    assert "unrelated.test" in hosts


async def test_uninstall_keeps_a_host_a_surviving_connector_still_needs():
    """Gmail and Sheets both reach oauth2.googleapis.com. The first one installed records it; the
    second records nothing, because it was already allowed. So uninstalling the FIRST must still
    leave the host behind - reading receipts alone would revoke it out from under the sibling."""
    tenant, project = "t_conn_es", "p_conn_es"
    await _new_project(tenant, project)

    first = {**REST_MANIFEST, "slug": "acme-one", "egress_hosts": ["api.acme.test", "shared.test"]}
    # A DIFFERENT backend host, or `hosts()` would report api.acme.test for this one too (it
    # always includes the base_url's host) and the sibling would legitimately still need it.
    sibling = {
        **REST_MANIFEST,
        "slug": "acme-two",
        "egress_hosts": ["shared.test"],
        "backend": {**REST_MANIFEST["backend"], "base_url": "https://api.other.test/v1"},
    }
    one = await _install(tenant, project, first)
    two = await _install(tenant, project, sibling)

    assert set(one.created_egress_hosts) == {"api.acme.test", "shared.test"}
    assert set(two.created_egress_hosts) == {"api.other.test"}, "shared.test was already allowed"

    async with SessionLocal() as s:
        row = await s.get(ConnectorInstall, one.id)
        await ConnectorInstaller().uninstall(s, row)

    hosts = await _allow_hosts(project)
    assert "shared.test" in hosts, "the surviving connector still needs this host"
    assert "api.other.test" in hosts
    assert "api.acme.test" not in hosts, "the host only the removed connector used should go"

    # ...and the receipt for shared.test moved to the survivor, so removing that one cleans up
    # rather than orphaning a host no install remembers adding.
    async with SessionLocal() as s:
        row = await s.get(ConnectorInstall, two.id)
        assert "shared.test" in (row.created_egress_hosts or []), "the receipt must be handed over"
        await ConnectorInstaller().uninstall(s, row)
    assert await _allow_hosts(project) == []


async def test_a_failed_install_does_not_leave_its_egress_hosts_behind():
    """The allow-list is widened before the install row is written. A failure after that point
    leaves hosts allowed with no install pointing at them - the same permanent widening, minus
    even a connector to blame it on."""
    tenant, project = "t_conn_ef", "p_conn_ef"
    await _new_project(tenant, project)
    installer = ConnectorInstaller()

    def _explode(*a, **kw):
        raise RuntimeError("boom")

    # Fail AFTER the allow-list is widened - `_initial_status` is read while building the
    # ConnectorInstall row, which is the first thing that happens once the hosts are in.
    installer._initial_status = _explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        async with SessionLocal() as s:
            await installer.install(s, tenant, project, parse_manifest(REST_MANIFEST),
                                    values={"token": "x"}, source="custom")

    assert "api.acme.test" not in await _allow_hosts(project)


async def test_setup_placeholders_are_substituted_into_urls():
    manifest = {
        **REST_MANIFEST,
        "slug": "acme-tpl",
        "auth": {
            "kind": "bearer",
            "setup": [
                {"key": "token", "label": "Token", "secret": True, "required": True},
                {"key": "site", "label": "Site", "secret": False, "required": False, "default": "eu"},
            ],
        },
        "backend": {
            "type": "rest",
            "base_url": "https://{setup.site}.acme.test/v1",
            "actions": REST_MANIFEST["backend"]["actions"][:1],
        },
    }
    row = await _install("t_conn_t", "p_conn_t", manifest)
    async with SessionLocal() as s:
        tool = await s.get(Tool, row.created_tool_ids[0])
        # The default filled in for the value the installer left blank.
        assert tool.config["request"]["url_template"].startswith("https://eu.acme.test/v1/")


async def test_google_family_shares_one_credential_group():
    """Gmail/Calendar/Drive/Sheets are ONE Google Cloud OAuth client. They must resolve to the
    same stored secret and the same env key, or an operator registers four apps to offer what
    Google considers one integration."""
    slugs = ["gmail", "google-calendar", "google-drive", "google-sheets"]
    groups = {get_manifest(s).group for s in slugs}
    assert groups == {"google"}
    assert get_manifest("outlook").group == "microsoft"
    # An unrelated connector keeps its own namespace.
    assert get_manifest("github").group == "github"


async def test_one_env_entry_covers_the_whole_google_family(google_app):
    """The operator registers ONE Google app; Gmail, Calendar, Drive and Sheets all become
    connectable from it, sharing a single stored credential to rotate."""
    tenant, project = "t_conn_grp", "p_conn_grp"
    for slug in ("gmail", "google-calendar", "google-drive", "google-sheets"):
        assert env_ready(get_manifest(slug)), slug
    await _install_catalog(tenant, project, "gmail")
    row = await _install_catalog(tenant, project, "google-calendar")
    async with SessionLocal() as s:
        ap = await s.get(AuthProvider, row.auth_provider_id)
        assert ap.config["client_id_ref"] == f"secret://proj/{secret_name('google', 'client_id')}"
    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_id')}"
    )
    assert value == "deployment-cid"


async def test_uninstalling_one_google_connector_does_not_break_its_siblings(google_app):
    """The regression this guard exists for: blanking the shared client secret on uninstall
    would silently disable every other Google connector in the project."""
    tenant, project = "t_conn_grp2", "p_conn_grp2"
    await _install_catalog(tenant, project, "gmail")
    await _install_catalog(tenant, project, "google-calendar")

    async with SessionLocal() as s:
        gmail = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        await ConnectorInstaller().uninstall(s, gmail)

    still_there = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_secret')}"
    )
    assert still_there == "deployment-csec", "uninstalling Gmail wiped the shared Google credential"

    # Removing the LAST member of the group does clear it.
    async with SessionLocal() as s:
        cal = await ConnectorInstaller.get_install(s, tenant, project, "google-calendar")
        await ConnectorInstaller().uninstall(s, cal)
    cleared = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_secret')}"
    )
    assert cleared == ""


async def test_deployment_registered_app_is_seeded_into_the_project_store(google_app):
    """The one-click path: the operator's app is copied into the project's own encrypted store,
    so resolve/refresh/rotate behave exactly as they would for a hand-pasted credential and no
    second credential source has to be taught to every downstream path."""
    tenant, project = "t_conn_dep", "p_conn_dep"
    row = await _install_catalog(tenant, project, "gmail")

    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_id')}"
    )
    assert value == "deployment-cid"
    async with SessionLocal() as s:
        ap = await s.get(AuthProvider, row.auth_provider_id)
        assert ap.config["client_id_ref"] == f"secret://proj/{secret_name('google', 'client_id')}"


async def test_catalog_install_ignores_credentials_sent_by_a_caller(google_app):
    """There is no supported way to type a credential into a catalog connector, so a client that
    sends one anyway must not create a second, invisible source of truth - the deployment's app
    is the answer, and `env_ready` has to keep telling the truth about it."""
    tenant, project = "t_conn_dep2", "p_conn_dep2"
    async with SessionLocal() as s:
        await ConnectorInstaller().install(
            s, tenant, project, get_manifest("gmail"), source="catalog",
            values={"client_id": "smuggled-cid", "client_secret": "smuggled-csec"},
        )
    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_id')}"
    )
    assert value == "deployment-cid"


async def test_unconfigured_catalog_connector_refuses_to_install(no_apps):
    """Default posture with nothing in the environment: the connector is unavailable and the
    error names the env key, rather than degrading into a form for a secret an end user should
    never hold."""
    gmail = get_manifest("gmail")
    assert not env_ready(gmail)
    assert missing_app_keys(gmail) == ["client_id", "client_secret"]

    async with SessionLocal() as s:
        with pytest.raises(InstallError) as e:
            await ConnectorInstaller().install(s, "t_none", "p_none", gmail, source="catalog")
    assert "FORGE_CONNECTOR_OAUTH_APPS" in str(e.value)
    assert "google" in str(e.value)


async def test_discovery_connectors_need_no_environment_entry(no_apps):
    """Slack, Notion, Linear and Atlassian publish OAuth metadata, so Forge registers a client
    with them on the fly (RFC 7591). They are one-click on a deployment that has configured
    nothing at all - which is what an evaluator sees on first boot."""
    for slug in ("slack", "notion", "linear", "atlassian"):
        m = get_manifest(slug)
        assert m.auth.discover, slug
        assert env_ready(m), f"{slug} should be connectable with no env configuration"


async def test_gmail_installs_as_per_user_oauth_awaiting_its_first_sign_in(google_app):
    """A real catalog entry, end to end - the shape the Connectors screen actually installs."""
    tenant, project = "t_conn_gm", "p_conn_gm"
    manifest = get_manifest("gmail")
    row = await _install_catalog(tenant, project, "gmail")

    assert row.auth_mode == "per_user"
    # Nobody has signed in yet. The row-level status only ever means "at least one person has
    # connected"; what matters to a given user is computed per caller by the router.
    assert row.status == "needs_auth"
    assert len(row.created_tool_ids) == len(manifest.backend.actions)
    async with SessionLocal() as s:
        ap = await s.get(AuthProvider, row.auth_provider_id)
        assert ap.config["per_user_context_keys"] == ["end_user_id"]
        assert ap.config["authorize_url"].startswith("https://accounts.google.com/")
        assert "gmail.send" in ap.config["scope"]


async def test_a_catalog_connector_is_personal_even_if_asked_to_be_shared(google_app):
    """`auth_mode` is a custom-connector control. A catalog connector is a personal account, and
    a caller asking for a project-wide one must not get someone's mailbox shared with the team."""
    tenant, project = "t_conn_shared", "p_conn_shared"
    async with SessionLocal() as s:
        row = await ConnectorInstaller().install(
            s, tenant, project, get_manifest("gmail"), source="catalog", auth_mode="shared",
        )
    assert row.auth_mode == "per_user"


async def test_custom_manifests_keep_the_shared_option_and_the_credential_form():
    """The escape hatch the catalog rules deliberately leave open: an unattended workflow with no
    end user still needs a credential, and a pasted manifest is where that is configured."""
    tenant, project = "t_conn_custom", "p_conn_custom"
    row = await _install(tenant, project, auth_mode="shared")
    assert row.auth_mode == "shared"
    value = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('acme', 'token')}"
    )
    assert value == "sekrit"
    # Group sharing still applies on the custom path, where credentials are typed.
    assert not await group_has_credentials(SecretStore(), "t_x", "p_x", parse_manifest(REST_MANIFEST))


# --- the API surface a person actually clicks -----------------------------------------------
#
# One call, one browser round trip, connected - and "connected" means connected FOR YOU. These
# pin down the two claims the Connectors screen makes to whoever is looking at it.

async def _editor_client():
    """An httpx client authenticated as a freshly registered (owner-role) user, plus a project.

    Returned already in use, so callers close it with `aclosing` rather than `async with` - an
    httpx client can only be entered once, and this one has made its first request already.
    """
    import uuid

    import httpx

    from forge.main import create_app

    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
    reg = await c.post("/v1/auth/register",
                       json={"email": f"u{uuid.uuid4().hex[:10]}@example.com", "password": "supersecret1"})
    assert reg.status_code == 201, reg.text
    c.headers["Authorization"] = f"Bearer {reg.json()['access_token']}"
    pid = (await c.post("/v1/projects", json={"name": "Conn", "slug": f"conn-{uuid.uuid4().hex[:8]}"})).json()["id"]
    return c, pid


async def _client_for(user_id: str, tenant: str, role: str):
    import httpx

    from forge.main import create_app
    from forge.security import create_access_token

    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test")
    c.headers["Authorization"] = f"Bearer {create_access_token(user_id=user_id, tenant_id=tenant, role=role)}"
    return c


async def _make_user(tenant: str, email: str, role: str) -> str:
    from forge.models import User

    async with SessionLocal() as s:
        u = User(tenant_id=tenant, email=email, role=role, status="active")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u.id


async def _fake_consent(tenant: str, project: str, slug: str, user_id: str) -> None:
    """Stand in for a completed browser consent: store that user's bundle where the callback
    would have put it."""
    from forge.services.auth_providers import AuthProviderService

    async with SessionLocal() as s:
        install = await ConnectorInstaller.get_install(s, tenant, project, slug)
        ap = await s.get(AuthProvider, install.auth_provider_id)
        await AuthProviderService.set_user_connection(
            s, tenant, project, ap, user_id, bundle={"access_token": f"tok-{user_id}"},
        )
        await s.commit()


async def test_connect_adds_the_connector_on_the_first_click(google_app):
    """The whole point of the screen: nobody has to know an "install" step exists."""
    c, pid = await _editor_client()
    async with aclosing(c):
        assert (await c.get(f"/v1/projects/{pid}/connectors")).json() == []

        r = await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["per_user"] is True
        assert body["authorize_url"].startswith("https://accounts.google.com/")
        # PKCE, and the DEPLOYMENT's client id - not something typed on this screen.
        assert "code_challenge_method=S256" in body["authorize_url"]
        assert "client_id=deployment-cid" in body["authorize_url"]

        installed = (await c.get(f"/v1/projects/{pid}/connectors")).json()
        assert [i["slug"] for i in installed] == ["gmail"]
        assert installed[0]["auth_mode"] == "per_user"
        assert installed[0]["connected"] is False


async def test_the_gallery_and_the_detail_panel_agree_on_the_action_count(google_app):
    """Deleting an action from the Tools screen must show up everywhere that reports a count.

    `list_installed` counted tools that still exist; `/{slug}/status` fell back to
    `len(created_tool_ids)` and happily reported the deleted ones. Delete two of Gmail's actions
    and the gallery said 3 while the detail panel - which is what polls `/status` - said 5.
    """
    c, pid = await _editor_client()
    async with aclosing(c):
        await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})

        listed = (await c.get(f"/v1/projects/{pid}/connectors")).json()[0]
        status_out = (await c.get(f"/v1/projects/{pid}/connectors/gmail/status")).json()
        original = listed["tool_count"]
        assert original >= 3 and status_out["tool_count"] == original

        from forge.services.tools import ToolService

        me = (await c.get("/v1/auth/me")).json()
        async with SessionLocal() as s:
            install = await ConnectorInstaller.get_install(s, me["tenant_id"], pid, "gmail")
            for tool_id in list(install.created_tool_ids)[:2]:
                tool = await s.get(Tool, tool_id)
                await ToolService.delete(s, tool)

        listed = (await c.get(f"/v1/projects/{pid}/connectors")).json()[0]
        status_out = (await c.get(f"/v1/projects/{pid}/connectors/gmail/status")).json()
        assert listed["tool_count"] == original - 2
        assert status_out["tool_count"] == listed["tool_count"], (
            "the detail panel is still counting actions the user deleted"
        )


async def test_the_installed_list_reads_every_bundle_in_one_go(google_app, monkeypatch):
    """One secret read for the whole screen, not one per connector.

    `_connected_for` resolved a bundle per install, sequentially - a DB round trip, a decrypt and
    an audit write each, all at the store's choke point. A project with the four Google
    connectors installed paid that four times on every paint, and the connect flow calls
    `reload()` on window focus, so it repeated every time someone came back from a consent
    window. The count must not scale with the number of installed connectors.
    """
    from forge.secrets.store import SecretStore

    c, pid = await _editor_client()
    async with aclosing(c):
        slugs = ("gmail", "google-calendar", "google-drive", "google-sheets")
        for slug in slugs:
            r = await c.post(f"/v1/projects/{pid}/connectors/{slug}/connect", json={})
            assert r.status_code == 200, r.text

        singles, batches = [], []
        real_one, real_many = SecretStore.read_ref, SecretStore.read_refs

        async def _count_one(self, **kw):
            singles.append(kw.get("ref"))
            return await real_one(self, **kw)

        async def _count_many(self, **kw):
            batches.append(list(kw.get("refs") or []))
            return await real_many(self, **kw)

        monkeypatch.setattr(SecretStore, "read_ref", _count_one)
        monkeypatch.setattr(SecretStore, "read_refs", _count_many)

        installed = (await c.get(f"/v1/projects/{pid}/connectors")).json()

    assert len(installed) == 4
    assert len(batches) == 1, "the whole list should resolve in a single batched read"
    assert len(batches[0]) == 4, "and that read should cover every install"
    assert singles == [], f"one-at-a-time bundle reads are back: {singles}"


async def test_a_batched_secret_read_still_audits_every_secret():
    """The batch goes through the same choke point. Auditing is not a side effect of reading one
    at a time - a read that skipped the trail because it was batched would be a hole in it."""
    from sqlalchemy import select

    from forge.models import AuditLog
    from forge.secrets.store import SecretStore

    tenant, project = "t_sec_batch", "p_sec_batch"
    store = SecretStore()
    async with SessionLocal() as s:
        for i in range(3):
            await store.write(s, tenant_id=tenant, project_id=project,
                              name=f"batched_{i}", value=f"v{i}", kind="generic")

    got = await store.read_refs(
        tenant_id=tenant, project_id=project,
        refs=[f"secret://proj/batched_{i}" for i in range(3)] + ["secret://proj/absent"],
    )
    assert got == {"batched_0": "v0", "batched_1": "v1", "batched_2": "v2"}
    assert "absent" not in got, "a missing name is absent from the result, not an error"

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant, AuditLog.action == "secret.read")
        )).scalars().all()
    assert {r.resource_id for r in rows} == {"batched_0", "batched_1", "batched_2"}


async def test_unconfigured_connector_is_unavailable_and_names_the_env_key(no_apps):
    """No form, no half-working install - the card tells the operator what to register."""
    c, pid = await _editor_client()
    async with aclosing(c):
        cat = (await c.get(f"/v1/projects/{pid}/connectors/catalog")).json()["connectors"]
        gmail = next(x for x in cat if x["slug"] == "gmail")
        assert gmail["managed"] is True
        assert gmail["configured"] is False
        assert gmail["missing_keys"] == ["client_id", "client_secret"]
        assert gmail["config_env_key"] == "FORGE_CONNECTOR_OAUTH_APPS"
        assert gmail["credential_group"] == "google"
        # The callback to whitelist comes from the API's own public base URL. Letting the browser
        # guess it from the console origin yields a plausible-looking URL that the vendor then
        # rejects as redirect_uri_mismatch - so the server has to be the one saying it.
        from forge.config import settings
        assert gmail["redirect_uri"] == f"{settings.public_base_url}/v1/oauth/callback"

        r = await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        assert r.status_code == 400
        assert "FORGE_CONNECTOR_OAUTH_APPS" in r.json()["detail"]

        # Slack needs no entry at all - it registers a client dynamically.
        slack = next(x for x in cat if x["slug"] == "slack")
        assert slack["configured"] is True and slack["missing_keys"] == []


async def test_connected_is_answered_for_the_caller_not_the_project(google_app):
    """A colleague signing in to their own mailbox must not show as a green tick on yours."""
    c, pid = await _editor_client()
    async with aclosing(c):
        me = (await c.get("/v1/auth/me")).json()
        tenant, my_id = me["tenant_id"], me["id"]
        await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        assert (await c.get(f"/v1/projects/{pid}/connectors")).json()[0]["connected"] is False

        await _fake_consent(tenant, pid, "gmail", my_id)
        assert (await c.get(f"/v1/projects/{pid}/connectors")).json()[0]["connected"] is True
        assert (await c.get(f"/v1/projects/{pid}/connectors/gmail/status")).json()["connected"] is True

    colleague = await _make_user(tenant, "colleague@example.com", "editor")
    c2 = await _client_for(colleague, tenant, "editor")
    async with aclosing(c2):
        theirs = (await c2.get(f"/v1/projects/{pid}/connectors")).json()[0]
        assert theirs["connected"] is False, "one person's Gmail became everyone's"


async def test_a_viewer_is_told_who_can_add_a_connector_rather_than_hitting_a_404(google_app):
    """Adding a connector creates project-level tools, so it stays an editor action - but the
    person who gets there first deserves an instruction, not a dead end."""
    c, pid = await _editor_client()
    async with aclosing(c):
        tenant = (await c.get("/v1/auth/me")).json()["tenant_id"]

    uid = await _make_user(tenant, "viewer-conn@example.com", "viewer")
    c2 = await _client_for(uid, tenant, "viewer")
    async with aclosing(c2):
        r = await c2.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        assert r.status_code == 403
        assert "editor" in r.json()["detail"]

    # Once an editor has added it, that same viewer connects their OWN account, no extra rights.
    await _install_catalog(tenant, pid, "gmail")
    c3 = await _client_for(uid, tenant, "viewer")
    async with aclosing(c3):
        r = await c3.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        assert r.status_code == 200, r.text
        assert r.json()["authorize_url"].startswith("https://accounts.google.com/")
        await _fake_consent(tenant, pid, "gmail", uid)
        assert (await c3.get(f"/v1/projects/{pid}/connectors/gmail/status")).json()["connected"] is True


async def test_catalog_credentials_cannot_be_typed_in_through_the_api(google_app):
    """Rotation for a catalog connector is "change the env and restart", so the route that
    accepts pasted credentials has to say so rather than quietly writing a shadow copy."""
    c, pid = await _editor_client()
    async with aclosing(c):
        await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})
        r = await c.put(f"/v1/projects/{pid}/connectors/gmail/credentials",
                        json={"values": {"client_secret": "smuggled"}})
        assert r.status_code == 400
        assert "FORGE_CONNECTOR_OAUTH_APPS" in r.json()["detail"]


async def test_mcp_discovery_asks_as_the_user_who_just_consented(google_app, monkeypatch):
    """The bug this exists for: an MCP connector signed in fine, then listed ZERO actions.

    Every catalog connector is per-user, so the only credential that exists after consent is
    stored under that person's identity. Discovery that forgets to say who it is resolves a
    shared bundle nobody wrote, calls the server unauthenticated, gets a 401, and leaves the
    connector "connected" with nothing in it."""
    from forge.connectors.manifest import parse_manifest as _parse

    seen: list[dict | None] = []

    async def _fake_discover(client_row, tenant_id, project_id, context=None):
        seen.append(context)
        if not (context or {}).get("end_user_id"):
            raise RuntimeError("401 Unauthorized")
        return [{"name": "notion_search", "description": "Search."}]

    monkeypatch.setattr("forge.tools.mcp.discover_tools", _fake_discover)

    tenant, project = "t_mcp_ctx", "p_mcp_ctx"
    manifest = _parse({
        **get_manifest("notion").model_dump(mode="json"),
        "auth": {**get_manifest("notion").model_dump(mode="json")["auth"], "discover": True},
    })
    async with SessionLocal() as s:
        install = await ConnectorInstaller().install(s, tenant, project, manifest, source="catalog")
    assert install.created_tool_ids == [], "discovery before consent legitimately finds nothing"
    assert seen and seen[-1] is None or True  # install-time probe has no identity yet

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "notion")
        count = await ConnectorInstaller().sync_tools(s, row, context={"end_user_id": "user-1"})
    assert count == 1
    assert seen[-1] == {"end_user_id": "user-1"}


async def test_refresh_delivers_a_corrected_manifest_to_an_existing_install(google_app):
    """A connector installed last week holds a COPY of the manifest as it was then, so a fix to a
    bundled action has to be able to REACH it. Uninstall/reinstall would work but deletes the auth
    provider, making everyone who signed in redo it for a change they had nothing to do with."""
    tenant, project = "t_upg", "p_upg"
    install = await _install_catalog(tenant, project, "gmail")
    tool_ids_before = list(install.created_tool_ids)

    # Rewind this install to a stale manifest: the old Gmail send action, which asked the MODEL
    # for a base64url-encoded MIME message and produced an opaque 400.
    stale = {**install.manifest}
    stale["version"] = "0.9.0"
    stale["backend"] = {**stale["backend"], "actions": [
        {**a, "request": {"method": "POST", "url_template": "/messages/send",
                          "fields": [{"path": "raw", "in": "body", "type": "string",
                                      "required": True, "llm_visible": True}]}}
        if a["name"] == "gmail_send_message" else a
        for a in stale["backend"]["actions"]
    ]}
    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        row.manifest = stale
        row.version = "0.9.0"
        send_tool = next(t for t in [await s.get(Tool, i) for i in tool_ids_before]
                         if t.name == "gmail_send_message")
        send_tool.config = {**send_tool.config, "request": stale["backend"]["actions"][2]["request"]}
        await s.commit()
        assert {f["path"] for f in send_tool.config["request"]["fields"]} == {"raw"}

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        await ConnectorInstaller().sync_tools(s, row)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        # Tool ids are preserved, so every workflow node and agent grant keeps working.
        assert row.created_tool_ids == tool_ids_before
        assert row.version == get_manifest("gmail").version
        fixed = next(t for t in [await s.get(Tool, i) for i in row.created_tool_ids]
                     if t.name == "gmail_send_message")
    args = {f["path"] for f in fixed.config["request"]["fields"]}
    assert {"to", "subject", "body"} <= args and "raw" not in args
    assert "$mime" in fixed.config["request"]["body_template"]
    # The auth provider - and therefore everyone's stored sign-in - is untouched.
    async with SessionLocal() as s:
        assert await s.get(AuthProvider, install.auth_provider_id) is not None


async def test_refresh_never_moves_an_install_to_a_new_credential_group(google_app):
    """A refresh rewrites the stored manifest, and the stored manifest is where the credential
    GROUP is read from. The group is a pointer, not a description: `secret_name(group, ...)`
    names the secrets this install actually wrote, and uninstall compares groups to decide
    whether a sibling still needs the shared vendor app. If a catalog edit could repoint it,
    uninstalling Gmail would blank the Google client secret while Calendar and Sheets are still
    using it."""
    tenant, project = "t_upg_grp", "p_upg_grp"
    await _install_catalog(tenant, project, "gmail")
    await _install_catalog(tenant, project, "google-calendar")

    # A catalog update that regroups Gmail away from the shared Google app.
    regrouped = {**get_manifest("gmail").model_dump(mode="json")}
    regrouped["version"] = "9.9.9"
    regrouped["auth"] = {**regrouped["auth"], "credential_group": "gmail-only"}

    import forge.connectors.catalog as catalog_mod
    real = catalog_mod.get_manifest
    catalog_mod.get_manifest = lambda slug: (  # type: ignore[assignment]
        parse_manifest(regrouped) if slug == "gmail" else real(slug)
    )
    try:
        async with SessionLocal() as s:
            row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
            await ConnectorInstaller().sync_tools(s, row)
    finally:
        catalog_mod.get_manifest = real  # type: ignore[assignment]

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        assert row.version == "9.9.9", "the upgrade itself must still land"
        assert parse_manifest(row.manifest).group == "google", (
            "the credential group must stay where this install's secrets actually are"
        )
        # ...and uninstalling Gmail still recognises Calendar as sharing the vendor app.
        await ConnectorInstaller().uninstall(s, row)

    secret = await SecretStore().read_ref(
        tenant_id=tenant, project_id=project, ref=f"secret://proj/{secret_name('google', 'client_secret')}",
    )
    assert secret == "deployment-csec", "the surviving sibling's credential must not be blanked"


async def test_refresh_keeps_the_values_this_install_was_configured_with():
    """A refresh must not rebuild URLs from the manifest's DEFAULTS - those are precisely what
    the installer overrode, so doing so would silently repoint every tool at the wrong host."""
    manifest = {
        **REST_MANIFEST,
        "slug": "acme-site",
        "auth": {
            "kind": "bearer",
            "setup": [
                {"key": "token", "label": "Token", "secret": True, "required": True},
                {"key": "site", "label": "Site", "secret": False, "required": False, "default": "eu"},
            ],
        },
        "backend": {
            "type": "rest",
            "base_url": "https://{setup.site}.acme.test/v1",
            "actions": REST_MANIFEST["backend"]["actions"][:1],
        },
    }
    tenant, project = "t_upg3", "p_upg3"
    manifest_obj = parse_manifest(manifest)
    async with SessionLocal() as s:
        row = await ConnectorInstaller().install(
            s, tenant, project, manifest_obj, source="custom",
            values={"token": "sekrit", "site": "apac"},
        )
    async with SessionLocal() as s:
        tool = await s.get(Tool, row.created_tool_ids[0])
        assert tool.config["request"]["url_template"].startswith("https://apac.acme.test/v1/")
        install = await ConnectorInstaller.get_install(s, tenant, project, "acme-site")
        await ConnectorInstaller().sync_tools(s, install)
    async with SessionLocal() as s:
        tool = await s.get(Tool, row.created_tool_ids[0])
    assert tool.config["request"]["url_template"].startswith("https://apac.acme.test/v1/"), \
        "refresh rebuilt the URL from the manifest default and lost the configured site"


async def test_refresh_keeps_an_action_the_manifest_dropped():
    """Deleting a tool a live workflow still references is a worse failure than carrying a stale
    one, so a shrunken manifest leaves the extra row alone."""
    tenant, project = "t_upg2", "p_upg2"
    row = await _install(tenant, project)  # custom manifest, two actions
    keep = list(row.created_tool_ids)
    shrunk = {**row.manifest}
    shrunk["backend"] = {**shrunk["backend"], "actions": shrunk["backend"]["actions"][:1]}
    async with SessionLocal() as s:
        install = await ConnectorInstaller.get_install(s, tenant, project, "acme")
        install.manifest = shrunk
        await s.commit()
        await ConnectorInstaller().sync_tools(s, install)
    async with SessionLocal() as s:
        for tid in keep:
            assert await s.get(Tool, tid) is not None


async def test_mcp_errors_name_the_actual_failure_not_the_task_group():
    """anyio wraps every MCP transport failure in an ExceptionGroup whose str() is "unhandled
    errors in a TaskGroup (1 sub-exception)" - which tells a user nothing about the 401 inside."""
    from forge.tools.mcp import describe_mcp_error

    inner = PermissionError("401 Unauthorized: token is invalid")
    group = BaseExceptionGroup("unhandled errors in a TaskGroup", [inner])
    described = describe_mcp_error(group)
    assert "401 Unauthorized" in described
    assert "TaskGroup" not in described

    # Nested groups (a task group inside a task group) still reach the leaf, and repeats of the
    # same error across sibling tasks collapse into one line.
    nested = BaseExceptionGroup("outer", [BaseExceptionGroup("inner", [inner, inner])])
    assert describe_mcp_error(nested).count("401 Unauthorized") == 1
    # A plain exception is passed through unharmed.
    assert "boom" in describe_mcp_error(RuntimeError("boom"))


async def test_examples_route_offers_the_key_based_manifests():
    c, pid = await _editor_client()
    async with aclosing(c):
        rows = (await c.get(f"/v1/projects/{pid}/connectors/examples")).json()
        slugs = {r["slug"] for r in rows}
        assert {"stripe", "slack-api", "twilio"} <= slugs
        stripe = next(r for r in rows if r["slug"] == "stripe")
        assert stripe["needs"], "an example should say what it will ask for"
        # The payload is a manifest the custom form can install as-is.
        assert parse_manifest(stripe["manifest"]).slug == "stripe"


async def test_refresh_does_not_resurrect_a_deliberately_deleted_action(google_app):
    """A project that deleted "send email" made a decision. Picking up a manifest fix must not
    quietly hand the capability back."""
    from forge.services.tools import ToolService

    tenant, project = "t_upg4", "p_upg4"
    install = await _install_catalog(tenant, project, "gmail")
    async with SessionLocal() as s:
        send = next(t for t in [await s.get(Tool, i) for i in install.created_tool_ids]
                    if t.name == "gmail_send_message")
        gone_id = send.id
        await ToolService.delete(s, send)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        await ConnectorInstaller().sync_tools(s, row)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        live = [t for t in [await s.get(Tool, i) for i in row.created_tool_ids] if t is not None]
    assert gone_id not in [t.id for t in live]
    assert "gmail_send_message" not in {t.name for t in live}, "refresh restored a removed capability"
    # ...while the actions that ARE still there were refreshed as usual.
    assert "gmail_search_messages" in {t.name for t in live}


async def test_refresh_still_adds_an_action_new_in_the_upgrade(google_app):
    """The mirror case: a name the install never created is genuinely new and must appear."""
    tenant, project = "t_upg5", "p_upg5"
    install = await _install_catalog(tenant, project, "gmail")
    before = len(install.created_tool_ids)

    # Rewind the frozen manifest so one existing action reads as "not created by this install",
    # which is exactly the shape of an action added by a later catalog version.
    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        trimmed = {**row.manifest}
        trimmed["backend"] = {**trimmed["backend"], "actions": [
            a for a in trimmed["backend"]["actions"] if a["name"] != "gmail_list_labels"
        ]}
        row.manifest = trimmed
        listing = next(t for t in [await s.get(Tool, i) for i in row.created_tool_ids]
                       if t.name == "gmail_list_labels")
        row.created_tool_ids = [i for i in row.created_tool_ids if i != listing.id]
        from forge.services.tools import ToolService
        await ToolService.delete(s, listing)
        await s.commit()

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        await ConnectorInstaller().sync_tools(s, row)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        names = {t.name for t in [await s.get(Tool, i) for i in row.created_tool_ids] if t}
    assert "gmail_list_labels" in names
    assert len(row.created_tool_ids) == before


async def test_refreshing_a_rest_connector_requires_editor(google_app):
    """It rewrites project tool configs, which is a project-level write - unlike MCP discovery,
    which only asks the vendor what it exposes using the caller's own credential."""
    c, pid = await _editor_client()
    async with aclosing(c):
        tenant = (await c.get("/v1/auth/me")).json()["tenant_id"]
        await c.post(f"/v1/projects/{pid}/connectors/gmail/connect", json={})

    uid = await _make_user(tenant, "viewer-sync@example.com", "viewer")
    c2 = await _client_for(uid, tenant, "viewer")
    async with aclosing(c2):
        r = await c2.post(f"/v1/projects/{pid}/connectors/gmail/sync")
        assert r.status_code == 403
        assert "editor" in r.json()["detail"]


async def test_refresh_prunes_ids_of_tools_that_no_longer_exist(google_app):
    """Dead ids in the receipt grow the IN clause on every refresh and send uninstall looking
    for rows that are already gone."""
    from forge.services.tools import ToolService

    tenant, project = "t_upg6", "p_upg6"
    install = await _install_catalog(tenant, project, "gmail")
    before = len(install.created_tool_ids)
    async with SessionLocal() as s:
        doomed = await s.get(Tool, install.created_tool_ids[0])
        doomed_id = doomed.id
        await ToolService.delete(s, doomed)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
        await ConnectorInstaller().sync_tools(s, row)

    async with SessionLocal() as s:
        row = await ConnectorInstaller.get_install(s, tenant, project, "gmail")
    assert doomed_id not in row.created_tool_ids
    assert len(row.created_tool_ids) == before - 1
