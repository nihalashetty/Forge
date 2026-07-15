# Tool Sets & MCP Toolsets

Status: Phases 1–3 are implemented and tested on branch `feat/tool-sets-and-mcp-toolsets` (not yet
committed). The OAuth 2.1 authorization-server surface ships **default-OFF behind
`FORGE_MCP_OAUTH_ENABLED`** and should get a security review before it is enabled in production
(review notes at the end).

## Concept

A **tool set** is a describable group of tools. One concept serves two jobs:
1. **Organization** — sets render as folders/filters on the Tools screen.
2. **Exposure & assignment** — an agent can be granted a whole set, and the MCP server can publish
   a set as a GitHub-style *toolset*.

Membership is **many-to-many** (a tool can live in several sets). MCP itself has no native
"toolset" primitive (still only a proposal upstream); toolsets are a server-side convention here.

## What shipped

### Phase 1 — Tool Sets
- Entities `ToolSet` + `ToolSetMember` (`models/entities.py`); Alembic migration `0007_tool_sets.py`
  (dev `create_all` builds them automatically).
- `ToolSetService` (`services/tool_sets.py`) + router `/v1/projects/{id}/tool-sets`
  (CRUD + `POST|DELETE /{set_id}/tools/{tool_id}` membership). Slugs are unique per project.
- Agents: `agent config.toolsets: [set_id]` resolves to member tools at compile time
  (`CompileContext.resolve_tool_ids`, union with `config.tools`, unknown ids tolerated).
  `build_compile_context` loads `ctx.toolset_members` in one query.
- Deleting a tool removes its membership rows (no DB cascade).
- Web UI: Tools screen filter chips + "Tool sets" management modal; agent builder "Tool sets" chips
  (workflow node inspector + Agents tab).

### Phase 2 — Expose tool sets over MCP
- Per-set endpoint: `POST /v1/mcp/{project_id}/toolset/{slug}` exposes only that set's tools.
- Exposure is **toolset-driven** (GitHub-style): the surface is exactly the **enabled tools of
  exposed tool sets** (`ToolSet.exposed`). There are no loose/"direct" tools — a tool that isn't in
  an exposed set isn't published. (Superseded the earlier `mcp_published_toolsets` /
  `mcp_exposed_tools` config.) MCP has no native "toolset" primitive, so the wire stays a flat
  `tools/list`; tool sets are a Forge-side grouping we flatten (`_exposed_names` in mcp_server.py).
- Connect screen: per-set **Expose** toggles, per-set URLs, a "currently exposed tools" summary,
  and a step-by-step "How to use this MCP server" guide. Tools screen: a per-tool "Tool sets"
  quick action to organize tools into sets.
- **Connector role**: a least-privileged user (below viewer) — can authenticate, self-serve MCP
  tokens, and use MCP, but the web console shows only a minimal "Your MCP access" page (no
  projects/settings) and mutations are blocked by RBAC. Provision via Settings → Members.

### Phase 3 (core) — Per-user identity over MCP
- The MCP endpoint accepts, besides the shared `mcp_api_key`, a **project-scoped Forge session
  token** (`create_session_token`) and resolves its `end_user`. `end_user` + the `X-Forge-Context`
  header (`run_context`) are threaded into `build_compile_context` and the workflow-run path, so
  entitlement gating and `{{ctx.*}}` injection act per user. The shared key remains for
  server-to-server callers (no identity).

## Auth model (agreed)

Two hops, both authenticated:
1. **Client → Forge**: standard bearer token so any MCP client works — shared project key
   (server-to-server), a per-user session token (implemented), and later PAT / OAuth 2.1.
2. **Forge → the customer's app (act as the user)**: a **separate** per-user credential resolved
   server-side — never pass the MCP token through (MCP spec: token passthrough forbidden). The app
   owner owns their own users/sessions; Forge only carries identity + (optionally) an out-of-band
   session via `X-Forge-Context` → `{{ctx.session}}`.

### Phase 3 (full) — the richer per-user auth layer (shipped)

- **PAT** — per-user personal access token (`forge_pat_…`, on the `api_keys` table with `user_id`
  + optional `project_id`). Endpoints `/v1/projects/{id}/mcp-tokens` (create/list/revoke, bound to
  the logged-in user) + a Connect-screen card to generate/revoke. Resolved on the MCP endpoint to
  an `end_user`; deliberately NOT a general-API principal (`get_current_user` only honors
  `forge_sk_`). Paste into any MCP client.
- **Per-user connected credentials** — an `AuthProvider` of kind `oauth2_authorization_code` with
  `per_user_context_keys: ["end_user_id"]` keys its OAuth token bundle per end user. The tool
  context now exposes `end_user_id` / `end_user_email`, so the resolver picks the acting user's
  bundle. The app owner's connect flow stores each user's bundle via
  `PUT /v1/projects/{id}/auth-providers/{apId}/connections/{endUserId}` (GET status / DELETE too).
  No token passthrough — Forge holds a separate downstream credential per user.
- **OAuth 2.1** (`forge.routers.mcp_oauth`, `FORGE_MCP_OAUTH_ENABLED`, default off) — Forge as an
  OAuth 2.1 resource + authorization server: `/.well-known/oauth-protected-resource/v1/mcp/{id}`
  and `/.well-known/oauth-authorization-server`, `WWW-Authenticate` challenge on a 401 from the MCP
  endpoint, Dynamic Client Registration (RFC 7591), authorization-code + PKCE S256 with a
  server-rendered login+consent, single-use authorization codes (jti revoke), and audience-bound
  access tokens (RFC 8707; the resource is carried in a custom `res` claim rather than the JWT
  `aud`, because the shared `decode_token` rejects tokens that carry `aud`). The MCP endpoint
  accepts a valid access token as another `end_user` source.

**OAuth security-review items (before enabling in prod):** the consent screen is a minimal
server-rendered login form (no per-client consent memory, no branding); MFA-enabled accounts are
**refused** here (no MFA bypass) and must use a PAT; add rate-limiting/lockout tuning on the
consent POST; consider persisting/rotating client secrets if you later support confidential
clients; review redirect-URI policy (currently exact-match, https-or-loopback).

## Tests

- `apps/api/tests/test_tool_sets.py`: tool-set service CRUD + membership + slug uniqueness,
  tool-deletion cleanup, agent-toolset → member-tool resolution via `build_compile_context`, the
  REST API (auth'd), MCP per-set / published-toolset scoping, session-token identity, and PAT
  identity + the `/mcp-tokens` API.
- `apps/api/tests/test_connected_credentials.py`: the AuthResolver picks each end user's own OAuth
  bundle by `end_user_id`; connection status/clear.
- `apps/api/tests/test_mcp_oauth.py`: discovery (404 when disabled), DCR, authorization-code +
  PKCE end to end, token → MCP acceptance, audience binding, the 401 `WWW-Authenticate` challenge,
  single-use codes, and PKCE enforcement.
