"""The `forge.connector/1` manifest - the one format a connector is authored in.

A manifest declares WHAT to create, never HOW to execute. Two backends cover the field:

* `mcp`  - the vendor runs an MCP server (Slack, Google Workspace, Microsoft 365, Notion,
           Linear, GitHub, Atlassian). Forge registers it, discovers its tools, and lets the
           vendor own their own API churn. This is the right default in 2026 for any vendor
           that ships one.
* `rest` - Forge calls the API directly. Each action is a VERBATIM `Tool.config` payload for
           the existing rest_api tool, so there is no second tool format to keep in sync, and
           an action can use every feature the Tool Builder already has (templating, response
           projection, retry, rate limits, caching).

Secrets never appear in a manifest. `auth.setup[]` declares which credentials to ASK the
installer for; the values go straight to the SecretStore and the created AuthProvider refers to
them as `secret://` refs - the same rule services/portability.py enforces for bundles.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

FORMAT = "forge.connector/1"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# A tool name is used verbatim as the LLM tool name, so hold it to the provider-safe charset
# every model API accepts (mirrors the component-name rule in services/portability.py).
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Auth kinds a manifest may declare. Deliberately a SUBSET of AuthResolver's kinds:
# `custom_script` is an audited advanced path and must never be reachable by installing a
# pasted manifest, and `csrf_session` is bespoke enough that it belongs in the Auth Providers
# screen rather than in a shareable catalog entry.
AuthKind = Literal["none", "bearer", "api_key", "basic", "oauth2_authorization_code", "oauth2_client_credentials"]


class ManifestError(ValueError):
    """A manifest is malformed or declares something an install refuses to create."""


class SetupField(BaseModel):
    """One credential the installer must supply (rendered as a form field).

    `secret=True` values are written to the SecretStore and referenced as `secret://proj/<name>`;
    non-secret values are substituted into the manifest as plain config (e.g. a tenant id or a
    region that forms part of a URL).
    """

    key: str
    label: str
    help: str | None = None
    secret: bool = True
    required: bool = True
    placeholder: str | None = None
    default: str | None = None

    @field_validator("key")
    @classmethod
    def _key_ok(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z0-9_]{1,40}", v or ""):
            raise ValueError("setup key must be lowercase alphanumeric/underscore")
        return v


class AuthSpec(BaseModel):
    kind: AuthKind = "none"
    # oauth2_*
    authorize_url: str | None = None
    token_url: str | None = None
    scopes: list[str] = Field(default_factory=list)
    # How the client authenticates at the token endpoint. "post" (client id/secret in the form
    # body) is what most vendors accept; "basic" is the HTTP Basic method RFC 6749 makes
    # mandatory for servers and that a few - Airtable among them - accept exclusively.
    token_auth: Literal["post", "basic"] = "post"
    # Extra authorize-URL parameters a vendor needs and the generic flow has no opinion about
    # (Google's access_type/prompt for a refresh token, Slack's user_scope, HubSpot's optional
    # scopes). Copied verbatim onto the query string.
    authorize_params: dict[str, str] = Field(default_factory=dict)
    # api_key / bearer
    header_name: str | None = None
    prefix: str | None = None
    location: Literal["header", "query"] = "header"
    param_name: str | None = None
    # Whether each end user connects their own account, or the project shares one.
    #   never    - shared only (a service/bot credential)
    #   optional - installer chooses (default)
    #   required - per-user only (a personal mailbox: a shared token would be wrong)
    per_user: Literal["never", "optional", "required"] = "optional"
    setup: list[SetupField] = Field(default_factory=list)
    setup_help: str | None = None
    # Connectors that are authorised by the SAME vendor OAuth app share a credential group, so
    # the app is registered once and every connector in the family reuses it. Gmail, Calendar,
    # Drive and Sheets are one Google Cloud OAuth client; Outlook and Teams are one Entra app.
    # Without this, a user pastes identical credentials four times and then has four copies to
    # rotate. Defaults to the connector's own slug (a family of one).
    credential_group: str | None = None
    # For MCP-backed connectors whose server advertises OAuth metadata (RFC 9728/8414): let
    # Forge discover the endpoints and register a client dynamically (RFC 7591) instead of
    # requiring the installer to create an app by hand. Falls back to the static urls above.
    discover: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> AuthSpec:
        if self.kind in ("oauth2_authorization_code", "oauth2_client_credentials"):
            # `discover` supplies these at connect time for MCP servers that advertise metadata.
            if not self.discover and not self.token_url:
                raise ValueError(f"{self.kind} requires token_url (or discover: true)")
            if self.kind == "oauth2_authorization_code" and not self.discover and not self.authorize_url:
                raise ValueError("oauth2_authorization_code requires authorize_url (or discover: true)")
        if self.kind == "api_key" and self.location == "query" and not self.param_name:
            raise ValueError("api_key in query requires param_name")
        return self


class McpBackend(BaseModel):
    type: Literal["mcp"] = "mcp"
    url: str
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    # Which of the server's tools to expose. `allow` (if non-empty) is an exact allow-list;
    # otherwise everything is exposed except `deny` (supports a trailing `*` wildcard).
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("mcp backend url must be http(s)")
        return v


class RestAction(BaseModel):
    """One REST action == one `rest_api` Tool.

    `request` and `response` are the EXACT structures `tools/rest.py` already consumes and the
    Tool Builder already edits - `{method, url_template, fields[], headers[], body_template}`
    and `{projection_jmespath}` / `{fields[]}`. They are copied into `Tool.config` untouched.

    Passing them through verbatim rather than inventing a friendlier connector-specific schema
    is the point: it means an action gets every capability the Tool Builder has (per-field
    `in: query|header|body|cookie`, `llm_visible: false` for server-injected values, `{{ctx.*}}`
    templating, `$each` loops, response projection) for free, an installed action can be opened
    and edited in the Tool Builder like any hand-built tool, and there is no second request
    format to keep in sync with the executor.
    """

    name: str
    description: str = ""
    display_name: str | None = None
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    # Optional passthroughs to the rest tool's reliability knobs.
    timeout_seconds: int | None = None
    retry: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    cache: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not _TOOL_NAME_RE.fullmatch(v or ""):
            raise ValueError("action name must be 1-64 chars of [A-Za-z0-9_-]")
        return v

    @model_validator(mode="after")
    def _request_ok(self) -> RestAction:
        if not self.request.get("url_template"):
            raise ValueError(f"action {self.name!r}: request.url_template is required")
        if not self.request.get("method"):
            raise ValueError(f"action {self.name!r}: request.method is required")
        for field in self.request.get("fields") or []:
            if not isinstance(field, dict) or not field.get("path"):
                raise ValueError(f"action {self.name!r}: every request field needs a `path`")
            where = field.get("in", "query")
            if where not in ("query", "header", "body", "cookie", "path"):
                raise ValueError(f"action {self.name!r}: field {field['path']!r} has invalid `in`: {where!r}")
            # `extract` pulls an id out of a pasted URL at call time. Compile it HERE so a typo in
            # a manifest fails at install, not on someone's first tool call.
            pattern = field.get("extract")
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as e:
                    raise ValueError(
                        f"action {self.name!r}: field {field['path']!r} has an invalid `extract` regex: {e}"
                    ) from e
        return self


class RestBackend(BaseModel):
    type: Literal["rest"] = "rest"
    base_url: str = ""
    actions: list[RestAction] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_actions(self) -> RestBackend:
        if not self.actions:
            raise ValueError("rest backend needs at least one action")
        names = [a.name for a in self.actions]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate action names: {sorted(dupes)}")
        return self


class ToolSetSpec(BaseModel):
    slug: str | None = None
    description: str = ""


class ConnectorManifest(BaseModel):
    format: str = FORMAT
    slug: str
    name: str
    version: str = "1.0.0"
    publisher: Literal["forge", "community", "custom"] = "custom"
    summary: str = ""
    categories: list[str] = Field(default_factory=list)
    icon: str | None = None
    docs_url: str | None = None
    setup_url: str | None = None
    # Roles this connector is "popular for" - drives the suggestion row at the top of the
    # Connectors screen. Free-form strings matched case-insensitively against the viewer's
    # selected role.
    roles: list[str] = Field(default_factory=list)
    auth: AuthSpec = Field(default_factory=AuthSpec)
    # Hosts this connector talks to. Appended to the project's egress allow-list on install so
    # a deployment running a strict allow-list doesn't have to be edited by hand. Never used to
    # widen anything else - the SSRF guard's default-deny stays in force.
    egress_hosts: list[str] = Field(default_factory=list)
    backend: McpBackend | RestBackend = Field(discriminator="type")
    toolset: ToolSetSpec = Field(default_factory=ToolSetSpec)

    @field_validator("format")
    @classmethod
    def _format_ok(cls, v: str) -> str:
        if v != FORMAT:
            raise ValueError(f"unsupported manifest format {v!r} (expected {FORMAT!r})")
        return v

    @field_validator("slug")
    @classmethod
    def _slug_ok(cls, v: str) -> str:
        if not _SLUG_RE.fullmatch(v or ""):
            raise ValueError("slug must be lowercase [a-z0-9_-], max 64 chars")
        return v

    @model_validator(mode="after")
    def _auth_backend_coherent(self) -> ConnectorManifest:
        # A per-user credential is stored per end user and injected per call. The REST path
        # supports that for every auth kind; the MCP path resolves the provider per (user,
        # server) connection. Both are fine - what is NOT fine is declaring per_user on a
        # connector with no auth at all, which would silently do nothing.
        if self.auth.kind == "none" and self.auth.per_user == "required":
            raise ValueError("per_user: required is meaningless with auth.kind: none")
        return self

    # --- derived helpers ------------------------------------------------------------------

    @property
    def kind_label(self) -> str:
        return "MCP" if self.backend.type == "mcp" else "REST"

    @property
    def group(self) -> str:
        """The credential namespace this connector's secrets live under."""
        return self.auth.credential_group or self.slug

    def hosts(self) -> list[str]:
        """Every host this connector needs egress to: the declared list plus the hosts implied
        by its own backend/token URLs, so a manifest can't forget to declare its own endpoint."""
        from urllib.parse import urlparse

        out = list(self.egress_hosts)
        candidates = [self.auth.token_url, self.auth.authorize_url]
        if isinstance(self.backend, McpBackend):
            candidates.append(self.backend.url)
        else:
            candidates.append(self.backend.base_url or None)
        for url in candidates:
            if not url:
                continue
            host = urlparse(url).hostname
            if host and host not in out:
                out.append(host)
        return out


def parse_manifest(data: dict[str, Any]) -> ConnectorManifest:
    """Validate a manifest dict, raising ManifestError with a readable message.

    Pydantic's own error text is verbose and nested; a pasted-manifest form needs one clear
    line per problem, so flatten it here rather than at every call site."""
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    try:
        return ConnectorManifest.model_validate(data)
    except Exception as e:  # noqa: BLE001 - normalize pydantic/ValueError into one type
        errors = getattr(e, "errors", None)
        if callable(errors):
            lines = []
            for err in e.errors():  # type: ignore[attr-defined]
                loc = ".".join(str(p) for p in err.get("loc", ()) if p != "__root__")
                lines.append(f"{loc or 'manifest'}: {err.get('msg', 'invalid')}")
            raise ManifestError("; ".join(lines[:8])) from e
        raise ManifestError(str(e)) from e
