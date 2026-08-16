# Connectors — research & implementation plan

**Status:** shipped, with amendments · **Date:** 2026-08-13 · **Scope:** new `Connectors` tab +
connector catalog + custom connectors

> **What actually shipped differs from this document in five ways.** The research and the
> manifest/install design below are accurate; the credential model was tightened after review.
> The decisions table in §7 records what was *proposed* — D4 and D6 were overruled and are struck
> there:
>
> 1. **Catalog credentials come only from `FORGE_CONNECTOR_OAUTH_APPS`.** No route accepts a
>    pasted client id/secret for a bundled connector, and the UI has no form for one. A vendor
>    the deployment hasn't registered shows as unavailable and names the env key.
> 2. **Catalog connectors are per-user, always.** There is no shared-account mode for a bundled
>    connector; each person signs in as themselves. Custom (pasted) manifests keep both modes -
>    which is also the only way to give an *unattended* run (schedule, webhook) a credential,
>    since a per-user token has no end user to resolve to.
> 3. **The catalog is OAuth-only.** Connectors that need a typed API key or bot token moved to
>    `forge/connectors/examples/`, offered as starting points in the custom-connector form.
> 4. **Triggers carry an identity** (`Trigger.run_as_user_id`, migration `0012`). A webhook or
>    schedule fires with nobody signed in, so it acts as the editor who saved the workflow -
>    otherwise per-user connectors would have made every unattended run unusable.
> 5. **Triggers also carry a scope** (`Trigger.scope`, migration `0013`): `project` for a team
>    automation everyone sees, `user` for someone's own. Independent of `run_as_user_id` -
>    *whose credentials* and *whose automation* are different questions. Defaults by role
>    (owner/admin → project, everyone else → user); existing rows become `project`, which is
>    how they already behaved.
>
> Operator setup and the day-to-day flow are documented in `docs/MANUAL.md` §4 → *Connectors*.

---

## 1. Executive summary

Forge does **not** need a connector *framework* — it already has one, spread across four
subsystems. What is missing is a **catalog**, a **one-click install**, and **one gap in the MCP
client** (no OAuth when Forge acts as an MCP *client*).

The proposal:

1. Define a Forge-native **connector manifest** (`forge.connector/1`) — a JSON file that declares
   an auth recipe + a set of actions, with two interchangeable backends: `mcp` (a remote MCP
   server) and `rest` (generated REST tools).
2. Ship a **bundled catalog** of manifests in-repo (no network needed, no third-party service).
3. **Install** = manifest → `AuthProvider` + `ToolSet` + `Tool` rows. Everything downstream
   (agents, workflow tool nodes, MCP export, traces, cost) works unchanged, because the output is
   ordinary Forge entities.
4. Add **MCP client-side OAuth**, so `mcp.slack.com`-class servers can be connected.
5. New **Connectors** tab: browse catalog → connect → see actions. "External MCP" folds under it
   as the advanced surface.

**Independence rule:** no third-party connector service, SDK, or catalog is ever a runtime
dependency. External catalogs are *authoring-time* sources only, behind a feature flag, and the
product boots and runs fully offline with the bundled manifests.

---

## 2. What Forge already has

This is the reason the plan is small. Inventory of existing substrate:

| Capability | Where | Notes |
|---|---|---|
| Auth recipes | [`auth_providers/resolver.py`](../apps/api/forge/auth_providers/resolver.py) | 7 kinds: `bearer`, `api_key`, `basic`, `oauth2_client_credentials`, `oauth2_authorization_code`, `csrf_session`, `custom_script` |
| 3-legged OAuth connect | [`routers/oauth.py`](../apps/api/forge/routers/oauth.py) | authorize URL + PKCE S256 + signed state + `/v1/oauth/callback` + token exchange |
| Auto token refresh | `resolver._refresh_oauth` | per-provider keyed lock, rotated-refresh-token safe |
| Per-user credentials | [`routers/connections.py`](../apps/api/forge/routers/connections.py) | `per_user_context_keys` → each end user connects their own account; `connector` role already exists |
| Secret storage | [`secrets/store.py`](../apps/api/forge/secrets) | encrypted, `secret://proj/...` refs, never exported in bundles |
| REST action engine | [`tools/rest.py`](../apps/api/forge/tools/rest.py) (760 lines) | templating, `$each` loops, response projection, retry, rate limit, cache, SSRF guard |
| Template language | [`auth_providers/templates.py`](../apps/api/forge/auth_providers/templates.py) | `{{cred.*}} {{ctx.*}} {{input.*}} {{env.*}}` |
| External MCP client | [`tools/mcp.py`](../apps/api/forge/tools/mcp.py) + [`routers/mcp_clients.py`](../apps/api/forge/routers/mcp_clients.py) | http/sse/stdio, tool discovery, per-tool enable/disable |
| Grouping + exposure | `ToolSet` / `ToolSetMember` in [`models/entities.py`](../apps/api/forge/models/entities.py) | folders in the UI **and** GitHub-style MCP toolsets |
| Bundle import/export | [`services/portability.py`](../apps/api/forge/services/portability.py) | id remap, auto-rename, `auth_provider_id` validation, version snapshots |
| Egress guard | [`util/ssrf.py`](../apps/api/forge/util/ssrf.py) | per-project allow/deny host lists, redirect-hop revalidation |

A connector, in Forge terms, is therefore **already expressible** — it is just tedious to build by
hand. Today a user wanting Slack must hand-create an auth provider, then hand-write ~10 REST tools.

### The four real gaps

| # | Gap | Impact |
|---|---|---|
| **G1** | No catalog / no install path | Every integration is hand-built from scratch |
| **G2** | `McpClient` auth is a static `headers_ref` secret only | Cannot connect to OAuth-protected remote MCP servers (Slack, Google, Microsoft, Notion, Linear…) |
| **G3** | `McpClient` has no per-user identity | One shared token per project; an agent can't act as the calling end user over MCP |
| **G4** | No way to package/share a connector | `portability` exports tools but not the auth provider + toolset as one unit |

Note that Forge implements the *server* half of MCP OAuth already
([`routers/mcp_oauth.py`](../apps/api/forge/routers/mcp_oauth.py) — RFC 9728/8414/7591/8707). G2 is
the mirror image of code that already exists.

---

## 3. Landscape research

### 3.1 Catalog sources — license verdicts

| Project | License | Catalog size | Verdict |
|---|---|---|---|
| **Nango** `providers.yaml` | **Elastic License v2** | 900+ APIs | ❌ **Exclude.** Same class as `langgraph-api`, which the README explicitly refuses. Not OSI-open. |
| **Pipedream** components | **Pipedream Source Available License** (MIT removed Jan 2022) | 1000+ apps | ❌ **Exclude.** License explicitly forbids building a competing integration registry — exactly this feature. |
| **Composio** | MIT SDK, but toolkits served from their hosted API + `COMPOSIO_API_KEY` | 1000+ toolkits | ❌ **Exclude as runtime.** Hosted dependency violates the independence requirement. |
| **Activepieces** pieces | **MIT** (except `packages/ee`) | 700+ pieces | ✅ **Usable as an authoring-time reference** with attribution. TypeScript, so not a runtime dep — mine the auth configs + action shapes, emit Forge manifests. |
| **APIs.guru** OpenAPI directory | **CC0 1.0** | thousands of specs | ✅ **Freely usable.** Best raw input for auto-generating `rest` backends. |
| **Official MCP registry** (`registry.modelcontextprotocol.io`) | Open, unauthenticated read API | growing | ✅ **Optional browse source.** `/v0/servers` returns `{name, title, description, version, remotes:[{type,url}], _meta}` with cursor pagination. |

**Conclusion: write our own manifests.** OAuth endpoints and REST paths are *facts* published in
vendor docs, not creative expression. Authoring them from vendor documentation (cross-checked
against MIT/CC0 sources) yields a catalog Forge owns outright, with zero license entanglement.

### 3.2 The 2026 shift that changes the design

The marquee connectors now ship **first-party remote MCP servers**:

- **Slack** — official remote MCP at `mcp.slack.com`, GA 17 Feb 2026 (search, messaging, canvases, users).
- **Google Workspace** — fully-managed remote MCP servers announced at Cloud Next '26, public developer preview (Gmail, Drive, Docs, Sheets, Calendar, Chat).
- **Microsoft** — official MCP catalog at `github.com/microsoft/mcp`; Graph-backed M365 servers (plus mature community `ms-365-mcp-server`).

This means for Slack / Gmail / Outlook specifically, **hand-writing REST tools is the wrong
answer** — the vendor already maintains a tool surface, keeps it current, and handles their own
API churn. The right answer is: register their remote MCP server + run OAuth against it.

Hence the manifest supports **two backends**, and the catalog picks per connector:

- `backend: mcp` → Slack, Google Workspace, Microsoft 365, Notion, Linear, GitHub, Atlassian.
- `backend: rest` → anything without a good MCP server (Stripe, Jira Server, HubSpot, Zendesk, internal APIs), and as the escape hatch for air-gapped installs that can't reach a vendor MCP endpoint.

Both compile down to `Tool` rows in a `ToolSet` — indistinguishable to agents and workflows.

### 3.3 Dependency check

`langchain-mcp-adapters` **0.3.0** (already installed, `mcp = [...]` extra in
[`pyproject.toml`](../apps/api/pyproject.toml)) passes through to the official MCP SDK, which
accepts a custom `httpx.Auth` implementation and per-connection `headers`. So MCP client OAuth is
implementable **with no new dependency** — Forge owns the token lifecycle in its own `SecretStore`
and hands the transport a refreshing auth object.

---

## 4. Design

### 4.1 The connector manifest

A single JSON document, versioned `forge.connector/1`, stored in-repo under
`apps/api/forge/connectors/catalog/<slug>.json`.

```jsonc
{
  "format": "forge.connector/1",
  "slug": "slack",
  "name": "Slack",
  "version": "1.0.0",
  "publisher": "forge",              // forge | community | custom
  "categories": ["messaging"],
  "icon": "slack",
  "docs_url": "https://api.slack.com/...",
  "summary": "Send messages, search history, manage channels.",

  "auth": {
    "kind": "oauth2_authorization_code",
    "authorize_url": "https://slack.com/oauth/v2/authorize",
    "token_url": "https://slack.com/api/oauth.v2.access",
    "scopes": ["chat:write", "channels:read", "search:read"],
    "per_user": "optional",          // never | optional | required
    "setup": [                       // what the installer must supply
      { "key": "client_id",     "label": "Client ID",     "secret": true },
      { "key": "client_secret", "label": "Client secret", "secret": true }
    ],
    "setup_help": "Create an app at api.slack.com/apps, add the redirect URI shown below."
  },

  "egress_hosts": ["slack.com", "mcp.slack.com"],

  "backend": {
    "type": "mcp",
    "url": "https://mcp.slack.com/mcp",
    "transport": "streamable_http",
    "tools": { "mode": "discover", "deny": ["admin_*"] }
  },

  "toolset": { "slug": "slack", "description": "Slack workspace actions." }
}
```

The `rest` backend swaps `backend` for a base URL plus action definitions that are **verbatim
`Tool.config` payloads** — i.e. the existing REST tool schema, no second format to maintain:

```jsonc
"backend": {
  "type": "rest",
  "base_url": "https://api.example.com/v2",
  "actions": [
    { "name": "list_tickets", "description": "...", "request": { "method": "GET", "url": "{{base_url}}/tickets", ... }, "response": { ...projection... } }
  ]
}
```

**Design rationale**

- The manifest introduces **no new runtime engine**. It is a *recipe for creating rows Forge
  already knows how to execute*. If the connectors feature were deleted tomorrow, every installed
  connector keeps working — it is just tools and an auth provider.
- Secrets are never in a manifest. `setup[]` declares *what to ask for*; values go straight to
  `SecretStore` and the manifest references them as `secret://` refs — the same rule
  `portability.py` already enforces.

### 4.2 Install pipeline

`POST /v1/projects/{id}/connectors/{slug}/install` →

1. Validate manifest against the JSON Schema (`packages/schemas`).
2. Write `setup[]` secrets → `secret://proj/conn_<slug>_client_id`, etc.
3. Create `AuthProvider` (kind + urls + scopes + `per_user_context_keys` if per-user).
4. Create `ToolSet` (slug, description, icon) — this is the folder the user sees.
5. Backend-specific:
   - `mcp` → create `McpClient` row pointing at the remote URL, discover tools, create one `Tool`
     (kind `mcp`) per discovered tool, apply the `deny` filter, add all to the tool set.
   - `rest` → create one `Tool` (kind `rest_api`) per action, each with `auth_provider_id` set.
6. Append `egress_hosts` to `project.config.egress.allow_hosts` (no-op unless the deploy runs a
   strict allow-list).
7. Record a `ConnectorInstall` row (see 4.4).
8. Return the connect URL if the auth kind needs a browser round-trip.

Steps 2–5 reuse `ToolService`, `ToolSetService`, `AuthProviderService`, `SecretStore` unchanged.
The id-remap + auto-rename logic in `portability.py` is directly reusable for step 5.

### 4.3 Auth modes

Both existing modes are exposed per connector, chosen at install:

- **Shared account** — one workspace credential for the whole project. Default for
  automation-style workflows (a bot posting to `#alerts`).
- **Per-user** — `per_user_context_keys: ["end_user_id"]`. Each end user connects their own
  account via the existing self-service `/connections` routes; the tool then acts as them. Already
  wired end-to-end for REST; needs G3 work for MCP.

`per_user: "required"` in a manifest (e.g. a personal-mailbox connector) forces the per-user mode
and hides the shared option.

### 4.4 New persistence

One new table only:

```python
class ConnectorInstall(PkTimestamp, Base):
    __tablename__ = "connector_installs"
    tenant_id / project_id
    slug: str                    # "slack"
    version: str                 # manifest version installed
    source: str                  # catalog | custom | url
    manifest: dict               # frozen copy — survives catalog updates
    auth_provider_id: str | None
    tool_set_id: str | None
    mcp_client_id: str | None
    created_tool_ids: list       # for clean uninstall
    status: str                  # installed | needs_auth | error
```

This is what makes **upgrade** and **uninstall** possible (and honest — uninstall removes exactly
what it created, nothing else). Without it, an install is an untraceable pile of rows.

### 4.5 Should users add their own connectors?

**Yes — three levels, all of which already have precedent in the codebase.**

| Level | Path | Reuses |
|---|---|---|
| 1. Catalog install | Browse → Connect | new |
| 2. Custom manifest | Paste/upload JSON, or fetch from URL (SSRF-guarded, flag-gated) | manifest validator + install pipeline |
| 3. Raw MCP server | "Add MCP server" — today's External MCP flow, now with OAuth | `routers/mcp_clients.py` |

Level 2 matters more than it looks: it makes the catalog *just data*, so a company can keep a
private connector pack in git and install it into every project — no fork, no PR to Forge. It also
gives the Forge Assistant a target format: "build me a connector for our internal ticket API"
becomes "emit a manifest", which is far more tractable than "emit 10 tool rows".

Level 3 stays because MCP servers exist that will never have a manifest.

### 4.6 UI — the Connectors tab

New entry in `PROJECT_NAV` ([`lib/data.ts:363`](../apps/web/lib/data.ts)), under **Build**,
positioned above Tools:

```
{ id: "connectors", label: "Connectors", icon: "plug", countKey: "connectors",
  help: "Pre-built integrations (Slack, Gmail, Outlook, …). Connect an account and their actions become tools." }
```

Screen layout (`components/screens/connectors.tsx`):

- **Gallery** — search + category filter, cards showing icon / name / summary / `installed` badge.
- **Detail drawer** — what it does, action list, scopes requested, auth mode chooser, setup fields.
- **Installed** section — connection status, "Reconnect", per-user connection count, "Open tool set", "Uninstall".
- **Tabs**: `Catalog` · `Installed` · `Custom` (paste manifest) · `MCP servers` (the current
  External MCP screen, moved here).

`External MCP` is **removed from the top-level nav** and becomes the fourth tab, keeping the route
alive for deep links. Rationale: two adjacent nav entries doing "connect an outside system" is the
kind of duplication that makes people pick wrong.

Installed connectors surface in Tools as their tool set folder — no new concept for the agent
builder or the canvas. A workflow tool node picks them up automatically, because they are just
tools.

---

## 5. Phased plan

### Phase 0 — Manifest + install engine (no UI)
*The load-bearing phase. Everything else is surface.*

- `apps/api/forge/connectors/` — `manifest.py` (pydantic model + validation), `install.py`
  (pipeline), `catalog.py` (load bundled JSON, in-memory index).
- `ConnectorInstall` entity + Alembic migration.
- `routers/connectors.py` — `GET /catalog`, `GET /catalog/{slug}`, `POST /{slug}/install`,
  `GET /installed`, `DELETE /installed/{id}`, `POST /installed/{id}/upgrade`.
- Two seed manifests to prove both backends: one `rest`, one `mcp`.
- Tests: install → assert exact rows created; uninstall → assert exact rows removed.

### Phase 1 — MCP client OAuth (closes G2)
*The one genuinely new piece of engineering.*

- Discovery: fetch `/.well-known/oauth-protected-resource` → authorization server metadata
  (RFC 9728 → RFC 8414), fall back to the manifest's static `authorize_url`/`token_url`.
- Dynamic client registration (RFC 7591) when the server supports it — otherwise use the
  operator-supplied client id/secret from `setup[]`.
- Reuse `routers/oauth.py`'s PKCE + signed-state + `/v1/oauth/callback` machinery; store the
  bundle in `SecretStore` under the existing `AuthResolver.bundle_secret_name` convention so
  refresh is free.
- Teach `tools/mcp.py::_connection_for` to resolve an `auth_provider_id` on `McpClient` into a
  refreshing `httpx.Auth` (or per-call header), replacing the static-`headers_ref`-only path.
- `McpClient` gains `auth_provider_id` (nullable) — additive, existing rows unaffected.

**Risk:** the MCP auth spec is young and vendor implementations vary. Mitigation: manifest can
pin static endpoints and skip discovery entirely; discovery is a fast path, not a requirement.

### Phase 2 — Connectors tab
- `components/screens/connectors.tsx`, `PROJECT_NAV` entry, `lib/api.ts` client methods.
- Move `McpClientsScreen` under the Connectors tab.
- OAuth connect popup + status polling (mirror the existing auth-provider connect UX).

### Phase 3 — Catalog build-out
Target ~20–25 launch connectors, prioritised by demand:

- **MCP-backed:** Slack, Google Workspace (Gmail/Drive/Calendar), Microsoft 365 (Outlook/Teams/OneDrive), GitHub, Notion, Linear, Atlassian.
- **REST-backed:** Stripe, HubSpot, Salesforce, Zendesk, Airtable, Shopify, Twilio, SendGrid, Discord, Google Sheets, Jira Data Center, PostgREST, generic webhook.

Each manifest is hand-authored from vendor docs and **verified against a live sandbox account**
before it ships. A manifest that has never been run is a bug report waiting to happen.

### Phase 4 — Authoring accelerators *(optional, flag-gated)*
- **OpenAPI → manifest** importer (feeds on CC0 APIs.guru specs or a user's own spec). Biggest
  force-multiplier for the long tail; also useful standalone for the Tool Builder.
- **MCP registry browse** — query `registry.modelcontextprotocol.io/v0/servers` in the Custom tab.
  Default **off** (`FORGE_CONNECTOR_REGISTRY_ENABLED=false`); the bundled catalog is always the
  source of truth.
- Forge Assistant skill: "build a connector" → emits a manifest.

---

## 6. Independence guarantees

Concrete, testable statements — each should become an actual test:

1. **No new runtime dependency.** Phases 0–3 add zero packages. `langchain-mcp-adapters` and `mcp`
   are already optional extras.
2. **Offline install.** A `rest` connector installs and runs with no outbound call except to the
   target API itself. A test asserts catalog load performs no network I/O.
3. **No third-party catalog at runtime.** The bundled JSON is the source of truth. Registry browse
   is off by default and never required for install.
4. **No vendor lock in the data model.** An installed connector is `AuthProvider` + `ToolSet` +
   `Tool` rows. Delete `forge/connectors/` and every installed connector still executes.
5. **Clean-room manifests.** Authored from public vendor docs. No Elastic-licensed or
   source-available content is copied. MIT/CC0 sources, where consulted, are credited in
   `docs/CONNECTOR_ATTRIBUTION.md`.
6. **Egress stays default-deny.** Install appends to the allow-list; it never disables the guard.

---

## 7. Open decisions

These were the decisions **as proposed**. Two of them were overruled by what shipped — the
`~~struck~~` rows below are historical, not current. See the amendments at the top of this file.

| # | Question | Recommendation | Shipped? |
|---|---|---|---|
| D1 | Connectors project-scoped or tenant-scoped? | **Project-scoped**, matching every other entity. Tenant-level sharing can come later via bundle export. | as proposed |
| D2 | Does "Connectors" replace "External MCP" in the nav? | **Yes** — fold it in as a tab. Two nav items for "connect an outside system" is a usability trap. | as proposed |
| D3 | Who can install? | `editor`. Per-user *connecting* stays open to `connector` role (already the case). | as proposed |
| D4 | Ship OAuth client credentials for the catalog? | ~~**No.** Forge is self-hosted; each install registers its own app. Manifest carries the setup instructions + shows the exact redirect URI.~~ | **superseded.** The vendor app belongs to the DEPLOYMENT: it is registered once in `FORGE_CONNECTOR_OAUTH_APPS` and every user then signs in against it. Asking each project to register its own app put a client secret in front of the person who just wanted to click Connect. |
| D5 | Bundled catalog vs downloadable index? | **Bundled** for v1. A signed remote index is a v2 conversation once there's a release cadence. | as proposed |
| D6 | Per-user auth for MCP-backed connectors (G3)? | ~~Phase 1 covers shared-account. Per-user MCP is a Phase 1.5 follow-on — it needs a per-user MCP client cache keyed the way `AuthResolver` keys bundles.~~ | **shipped in Phase 1.** The per-user MCP client cache exists (`forge/tools/mcp.py`), keyed on the same auth dims the resolver varies on, with a TTL, an LRU ceiling and a retirement queue. It was not deferrable: catalog connectors are per-user always, so a shared-account-only MCP path would have had no callers. |

---

## 8. Effort estimate

| Phase | Scope | Estimate |
|---|---|---|
| 0 — manifest + install engine | ~600 LOC backend + tests | 3–4 days |
| 1 — MCP client OAuth | ~400 LOC, spec-sensitive | 3–5 days |
| 2 — Connectors tab | ~700 LOC frontend | 3–4 days |
| 3 — 20–25 manifests | data + live verification | 5–8 days |
| 4 — accelerators | optional | 4–6 days |

**Phases 0–3 ≈ 3 weeks** for a usable, self-contained connectors feature covering Slack, Gmail and
Outlook.

---

## Sources

- [Activepieces LICENSE (MIT, except `packages/ee`)](https://github.com/activepieces/activepieces/blob/main/LICENSE) · [editions & licensing](https://deepwiki.com/activepieces/activepieces/1.2-editions-and-licensing)
- [Nango repository (Elastic License v2)](https://github.com/nangohq/nango) · [`providers.yaml`](https://github.com/NangoHQ/nango/blob/master/packages/providers/providers.yaml)
- [Pipedream Source Available License announcement](https://pipedream.com/blog/introducing-the-pipedream-source-available-license/) · [LICENSE](https://github.com/PipedreamHQ/pipedream/blob/master/LICENSE)
- [Composio SDK repository](https://github.com/ComposioHQ/composio)
- [APIs.guru OpenAPI directory (CC0)](https://github.com/APIs-guru/openapi-directory/blob/main/LICENSE)
- [Official MCP Registry](https://registry.modelcontextprotocol.io/) · [registry source](https://github.com/modelcontextprotocol/registry)
- [Slack official remote MCP server](https://mcpservers.org/remote-mcp-servers/slack)
- [Google: official MCP support for Google services](https://cloud.google.com/blog/products/ai-machine-learning/announcing-official-mcp-support-for-google-services) · [Configure Google Workspace MCP servers](https://developers.google.com/workspace/guides/configure-mcp-servers)
- [Microsoft official MCP catalog](https://github.com/microsoft/mcp)
- [langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters) · [LangChain MCP docs](https://docs.langchain.com/oss/python/langchain/mcp)
