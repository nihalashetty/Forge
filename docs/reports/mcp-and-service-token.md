# MCP exposure, `stdio`, service-token sufficiency, and workflow-as-MCP

Investigation for the feature-bounty work. Items marked **REPORT ONLY** were requested as a
check, not a fix; items marked **DONE** were implemented in this branch.

## 1. How Forge exposes tools as MCP to outside systems (Connect tab) — status

- **Surface:** `POST /v1/mcp/{project_id}` — JSON-RPC 2.0 implementing `initialize`,
  `tools/list`, `tools/call` ([mcp_server.py](../../apps/api/forge/routers/mcp_server.py)).
  An external HTTP MCP client (that speaks plain JSON-RPC over POST) can list and call the
  project's enabled tools. **Wired and functional.**
- **Auth:** per-project `project.config.mcp_api_key` sent as `Authorization: Bearer <key>`.
  Required when set; when unset and `auth_required` is on, the endpoint refuses (so it can't be
  left open by accident in a secured deployment).
- **Hardening added in this branch (DONE):**
  - **Rate limiting** per project (the API key is shared by every caller, so it's the real abuse ceiling).
  - **Per-project exposed-tool allow-list** (`project.config.mcp_exposed_tools`): expose only
    safe tools instead of *every* tool (previously SQL/code tools were exposed alongside the rest).
- **Remaining limitations (not fixed here):**
  - No `end_user` / per-run context is attached, so entitlement-gated tools and `{{ctx.*}}`
    injection tools can't act on behalf of a specific end user over this surface.
  - Full MCP **Streamable-HTTP / SSE** transport is not implemented — it's request/response
    JSON-RPC only.

## 2. Can we connect via `stdio`? — **REPORT ONLY (answer: not directly)**

Forge's MCP *server* is an **HTTP JSON-RPC endpoint**, not a stdio server. MCP clients that
only speak the local **stdio** transport (some Claude Desktop / IDE configs) cannot point at an
HTTP URL directly. Two supported ways to connect them:

1. **HTTP-capable client** (Cursor, VS Code MCP, Claude Desktop's HTTP/SSE server entry): point
   it at `https://<host>/v1/mcp/<project_id>` with the API key as a bearer header. Works today.
2. **stdio-only client:** bridge with a small stdio↔HTTP proxy, e.g. an `mcp-remote`-style
   launcher (`command: npx, args: ["mcp-remote", "https://<host>/v1/mcp/<project_id>", "--header", "Authorization: Bearer <key>"]`).

Separately, Forge *consuming* an external MCP server over `stdio` is now supported but **gated**
(`FORGE_ENABLE_MCP_STDIO`, off by default) because stdio launches a local process — see the
[MCP stdio hardening](../../apps/api/forge/tools/mcp.py) change.

**Recommendation (future):** add a native Streamable-HTTP/SSE MCP transport so stdio-only clients
can connect via the standard `mcp-remote` path without a hand-rolled proxy, and so streaming
tool results work.

## 3. Is the service token enough for a quoting-portal connection? — **REPORT ONLY**

`FORGE_SERVICE_API_TOKEN` is a single static shared secret. A request bearing it authenticates
as a fixed **`editor`** identity **in the seeded workspace only**
([deps.py](../../apps/api/forge/deps.py) `get_current_user`).

It depends on the integration direction:

- **Portal → Forge (portal triggers a Forge workflow):** for a simple server-to-server call to
  `POST /v1/projects/{id}/run` in the default single-workspace install, the service token **is
  enough to authenticate the call**. But it is likely **not sufficient on its own** for a real
  integration:
  - **On-behalf-of-user auth:** the token authenticates the *portal*, not its end users. To act
    for a specific portal user (e.g. carry that user's session/CSRF to a downstream system), pass
    per-run context via the `X-Forge-Context` header (`{{ctx.*}}` in tools) and/or `end_user` /
    `session_token` in the run body — the service token does none of this.
  - **Multi-tenant:** it maps to one seeded tenant. A portal whose project lives in a different
    workspace can't be reached with it.
  - **Least privilege:** it's full `editor`, unscoped, non-expiring, with no per-integration or
    read-only variant and no rotation UX. A dedicated **scoped API key** (see the RBAC/API-key
    work) would be the right primitive.
- **Forge → Portal (Forge calls the quoting portal's API as a tool):** the service token is
  **not** what's used. You need a **REST tool + an Auth Provider** (bearer / api-key / basic /
  OAuth2 / CSRF-session), and for per-user calls the per-run `X-Forge-Context` session/CSRF
  values. Note the documented caveat: for create→302→new-URL flows keep *Follow-redirects OFF*
  and read `redirect.location`, and httpx strips `Cookie` across hops.

**Bottom line to fix later:** the service token covers "is this call from our backend?" for a
single workspace. A production quoting-portal integration will also need (a) per-run context /
session injection for on-behalf-of-user calls, and/or (b) a REST tool + Auth Provider if Forge
calls the portal, and ideally (c) a scoped, revocable API key rather than the shared editor token.

## 4. Expose a whole workflow as MCP — **DONE**

Implemented in this branch: set `project.config.mcp_expose_workflow: true` (and optionally
`mcp_workflow_tool_name`, default `run_workflow`). `tools/list` then advertises a tool that runs
the project's **configured workflow** (same resolution as `POST /run`); `tools/call` runs it to
completion and returns the reply. So an external MCP client can invoke an entire Forge workflow
as a single tool, not just the project's individual tools.
