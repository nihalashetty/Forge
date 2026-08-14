# Forge - User Manual

Forge is a self‑hosted platform for **building, testing, and shipping AI agents and
workflows** - visually, without writing framework code. You wire together nodes (agents,
tools, knowledge, logic) on a canvas, ground them in your own data, connect them to your
systems, and deploy them to email, an API, an MCP server, or an embeddable web widget. It
runs on the open‑source LangChain/LangGraph engine; nothing is sent to a third‑party
orchestration service.

This manual is written for **everyone** - you don't need to be a developer to follow it.

---

## 1. Getting started

### Logging in
When you open Forge you'll see a **login screen**.
- **Dev / first run:** sign in with **`you@forge.local`** / **`forge-admin`**, or click
  *Create a workspace* to register a fresh account.
- Need teammates? Open **Settings → Members & Roles** to invite them (owner / admin / editor / viewer / connector roles).

### Creating your first project
A **project** is a workspace for one assistant or automation - its workflows, tools,
knowledge, and settings live together and are isolated from other projects.

1. Click **New project**, give it a name.
2. (Recommended) Open **Settings → API Keys** and paste an OpenAI / Anthropic / Google key
   under **Model providers**. Until you do, Forge uses an offline “fake” model so you can
   build and test the *plumbing* without spending anything - but answers won't be real.
3. Pick a **Default model** under **Settings → General** (e.g. `openai:gpt-4.1-mini`).

### The left sidebar (hover any tab for a tooltip)
The nav is grouped into **Build**, **Deploy**, and **Observe**, with **Overview** at the top
and **Settings** pinned at the bottom.

| Group | Tab | What it's for |
|---|---|---|
| — | **Overview** | Dashboard: usage, cost, recent activity. |
| **Build** | **Playground** | Chat with a workflow live to test it (with token + cost metering). |
| | **Workflows** | The visual canvas - wire nodes into a graph. |
| | **Agents** | Reusable agent presets (model + prompt + tools + knowledge) to drop into workflows. |
| | **Tools** | Capabilities an agent can call: REST, GraphQL, Code, SQL, MCP, built‑ins - organized into **tool sets**. |
| | **Components** | Generative‑UI components an agent can render (tables, cards, forms). |
| | **Knowledge** | Documents + Q&A that ground answers (RAG). Add text, URLs, files, or crawl a site. |
| | **Connectors** | Gmail, Slack, GitHub, Notion… - connect your own account in one click; each one adds a tool set you can build workflows on. |
| | **Auth Providers** | Reusable credential strategies (Bearer, API key, OAuth…) tools attach to. |
| **Deploy** | **Channels** | Deploy a workflow to an email surface. |
| | **Triggers** | Event entry points - webhook URLs, schedules, pollers. |
| | **Connect** | Expose this project - as an MCP server, the run API, or an embeddable web widget. |
| **Observe** | **Traces** | Per‑run waterfall: model calls, tokens, latency, cost. |
| | **Evaluations** | Test datasets (input + expected) scored against a workflow. |
| | **Agent inbox** | Live conversations escalated to a human - reply to resume the run. |
| — | **Settings** | Model keys & secrets, members & roles, guardrails & egress, budgets, versioning, and more. |

---

## 2. Core concepts

- **Workflow** - a graph of **nodes** wired `start → … → end`. A user message (or a trigger
  event) flows through it. Built on the Workflows canvas.
- **Node** - one step. Hover any node in the palette for a description + example.
- **Agent** - a model with a system prompt, tools, and optional knowledge that *reasons →
  acts* in a loop until it can answer (the workhorse).
- **Tool** - an external capability an agent (or a `tool_call` node) can invoke.
- **Knowledge** - your documents + Q&A pairs, embedded for semantic search (RAG).
- **Trigger** - what *starts* a workflow (a person chatting, a webhook, a schedule…).
- **Channel** - where a workflow is *deployed* (email).
- **State** - the data carried through a run (always includes `messages`; you can add keys).

---

## 3. Workflows & the node catalog

Open **Workflows → New**, then drag/click nodes from the palette and connect them. Click a
node to configure it in the inspector (friendly forms - no JSON). **Publish** to make it
live; **Run** (or the Playground) to test.

### Flow
| Node | What it does | Key config |
|---|---|---|
| **Start / End** | Entry / exit markers. | - |
| **Router** | Branches on a state value (no model call). One case per value + an **Else/Default**. `multi` runs every matching branch in parallel. | `expression` (state key), `cases`, `default` |
| **Loop** | Repeats a section until a condition is false or a max‑iteration cap. Writes `_loop=continue/done` - pair with a Router that loops the body back. | `max_iter`, `condition` |
| **Parallel Fanout** | Maps over a list in state - runs a child node **once per item, in parallel**. | `over` (list key), `child_node`, `item_key` |
| **Join** | Where parallel branches converge before continuing (results aggregate via an add‑reducer key). | `reducer` |
| **Subworkflow** | Runs **another workflow in this project** as a reusable component. | `workflow_id` |

### Agents & model
| Node | What it does |
|---|---|
| **Agent** | Model + prompt + tools, ReAct loop. Toggle **knowledge** (RAG / Q&A) right on the agent so it searches per sub‑question. |
| **Deep Agent** | Agent + optional planning / virtual filesystem / **sub‑agents** for long multi‑step tasks. Drag from its **subagents** handle (bottom of the node) to any specialist **Agent** node to make it a callable sub‑agent — the supervisor dispatches it via the `task` tool. Each sub‑agent keeps its own model, tools, and prompt; set the specialist's **description** so the supervisor knows when to call it. |
| **LLM** | One single model call (cheap rewriting/summarizing). |
| **Classifier** | One model call picks a label (e.g. intent) into state. `multi_label` writes all that apply. |

### Tools, data & humans
| Node | What it does |
|---|---|
| **Tool Call** | Invokes one specific tool with fixed/mapped arguments (deterministic). |
| **Transform** | Reshapes state with a JMESPath expression (no model). |
| **Retrieval** | Pulls knowledge into context - documents (RAG) and/or curated Q&A pairs, each toggleable. Place before a grounded agent. |
| **Human Input** | Pauses the run for approve/reject in the Playground. |
| **Human Handoff** | Escalates to a person via the **Agent inbox**; their reply becomes the answer and is delivered over the channel. |
| **Webhook Out** | POSTs run data to an external URL. |
| **Emit Event** | Emits a custom event into the live run stream. |

### Triggers (entry points)
| Node | Starts the workflow when… | Key config |
|---|---|---|
| **Webhook** | An external system POSTs to the workflow's hook URL (shown on **Triggers** after publish). | `message_path`, `require_signature`, `secret_ref` |
| **Schedule** | A recurring time (interval or cron) - sends a fixed message each run. | `every_minutes` or `cron`, `message` |
| **Email** | Mail arrives in the connected mailbox (configure an Email **channel**). | `mailbox`, `reply` |
| **App Event** | Polling a URL returns a **new** item (deduped) - turns any feed into events. | `poll_url`, `interval_minutes`, `items_path`, `dedupe_key` |

> **Error fallback:** set a workflow's `on_error.message` (via the Forge Assistant) to send a
> graceful reply when a run fails instead of erroring silently.

---

## 4. Tools

**Tools → New** then pick a kind. Each tool has a **description** (what the model reads to
decide when to use it) and is tested live on the right.

| Kind | Configure | Example |
|---|---|---|
| **REST** | method + URL (with `{placeholders}`), headers, input fields, optional **response projection** (JMESPath to trim the payload → fewer tokens). | `GET https://api.acme.dev/orders/{order_id}` |
| **GraphQL** | endpoint + query + variables. | A `query { order(id:$id){…} }` |
| **Code** | Python `def main(**kwargs): return …` (sandboxed; pure‑stdlib imports) + an **arguments JSON Schema**. | uppercase / reshape / compute |
| **SQL** | a **connection secret** (DB URL), a parameterized `SELECT … :id`, args schema, read‑only. | look up a customer by id |
| **MCP** | pick a registered **MCP server** (Connect screen) + the remote tool name. | a tool from a GitHub/Slack MCP server |
| **Built‑in** | `calculator`, `current_time`, `web_fetch`, `web_search`, `knowledge_search`, **`remember` / `recall`** (long‑term memory). | give an agent memory across chats |

**Built‑ins are project defaults:** every project is auto‑provisioned the built‑ins above; they
sit pinned at the top of the Tools screen, **can't be deleted** (disable instead), and never travel
in import/export bundles — so importing a project neither duplicates nor loses them.

**Per‑environment endpoints (`{{env.*}}`):** a REST URL / GraphQL endpoint (or an auth template)
can reference `{{env.<key>}}`, resolved from the `FORGE_TOOL_VARS` JSON map set per deploy — so the
**same** tool row points at your dev / qa / prod host without editing it. A referenced key that
isn't defined **fails the call loudly** (never a broken URL). (`{{ctx.*}}` per‑run values stay
lenient — a missing one renders empty.)

**Response projection (cost lever):** for REST/GraphQL, the Projection tab trims the raw
response with JMESPath before it reaches the model - watch the Raw→Projected token meter shrink.

**Reliability:** REST tools support `retry` (with backoff), `rate_limit.per_minute`, and
`cache.ttl_seconds` (caches idempotent GETs).

**Safety:** every outbound call (tools, webhooks, fetch, crawl) is checked by the **SSRF guard**
- private/loopback/cloud‑metadata addresses are blocked. A project‑level **egress policy**
(Settings → Guardrails & Egress) can further allow/deny hosts across every tool at once (§9).

**Tool sets:** group related tools into a **tool set** - a reusable, many‑to‑many folder. Sets
organize the Tools screen (filter chips + a "Manage toolsets" drawer), can be granted to an agent
in one click (instead of picking tools one by one), and can be published over the project's MCP
server as a *toolset* (§10).

### Connectors (the fast way to get tools)

**Connectors** is the front door for Gmail, Google Calendar/Drive/Sheets, Outlook, Slack, Notion,
Linear, Atlassian, GitHub, HubSpot and Airtable. Click **Connect**, sign in on the vendor's own
page, approve - and the connector adds an auth provider, a **tool set**, and one tool per action.
Those tools then behave like any hand‑built tool: drop them on the canvas, grant the set to an
agent, publish it over MCP.

Two things are worth knowing:

* **Your account is yours.** Every catalog connector is per‑user. The tools are the project's
  (anyone can build a workflow on them), but the *account behind them* is personal: the green
  tick means **you** are connected, disconnecting affects only you, and nobody can act as you.
  Everyone who will run the workflow connects themselves.
* **Nobody types a client secret.** The vendor OAuth apps are registered once by whoever runs
  the deployment (see below). Forge's UI never asks an end user for a client id, secret, or API
  token. A connector whose vendor isn't registered yet shows as **Unavailable** and tells you
  which env key to set.

**Add > Custom connector** is the escape hatch, and it is where a typed credential belongs: paste
a `forge.connector/1` manifest (or start from a bundled example - Stripe, Twilio, Zendesk, Jira,
SendGrid, PagerDuty, Shopify, Discord, a Slack bot token, a generic REST API), fill in its key,
and choose **one shared account for the project** instead of per‑user - a genuine service account,
used by everyone.

**What about triggers?** A webhook or a schedule fires with nobody signed in, so each trigger
carries the person it **runs as**: the editor who saved the workflow it came from. A scheduled
workflow therefore reads *their* mailbox - the same account they connected while building it.
Triggers are also either **Project** (a team automation) or **Personal** (yours alone) - see §7.
**Use my accounts** claims one, which is what you want when you inherit an automation or its
original owner has left. A trigger showing *runs as nobody* still fires, but any connector‑backed
tool in it will fail - claim it, or give that workflow a shared‑account custom connector instead.

**Add > MCP server** registers a raw MCP server by URL (the old *External MCP* screen).

**Keeping a connector current.** An installed connector keeps a copy of its manifest, so a fix
shipped in a later Forge release doesn't reach it automatically. Open it and click **Refresh
actions**: MCP connectors re-ask the vendor what they expose, REST connectors re-apply the
bundled manifest. Tool ids are preserved (workflow nodes and agent grants keep working), an
action the new manifest no longer declares is kept rather than deleted, and nobody's sign-in is
disturbed — unlike removing and re-adding, which deletes the auth provider and makes everyone
reconnect.

#### Setting up connectors (for whoever runs Forge)

Register **one** OAuth app per vendor, add it to `FORGE_CONNECTOR_OAUTH_APPS` in your `.env`, and
restart the API. Everyone in every project then connects with a single click.

```
FORGE_CONNECTOR_OAUTH_APPS={"google":{"client_id":"…","client_secret":"…"},"github":{…}}
```

The key is the **credential group** - the vendor, not the connector: one `google` entry covers
Gmail, Calendar, Drive and Sheets.

| Group | Covers |
|---|---|
| `google` | Gmail · Google Calendar · Google Drive · Google Sheets |
| `microsoft` | Outlook |
| `github` | GitHub |
| `hubspot` | HubSpot |
| `airtable` | Airtable |

Slack, Notion, Linear and Atlassian need **no entry at all** - they publish OAuth metadata, so
Forge discovers their endpoints and registers a client automatically the first time someone
connects. They work on a deployment that has configured nothing.

Every vendor needs this exact **redirect URI** whitelisted on the app you register:

```
<FORGE_PUBLIC_BASE_URL>/v1/oauth/callback     # dev: http://localhost:8000/v1/oauth/callback
```

Rotating a credential is an env change plus a restart - people's own sign‑ins survive it.

---

## 5. Auth Providers & OAuth

**Auth Providers** hold a reusable credential strategy that tools attach to. Secrets are
referenced (never pasted into config) as `secret://proj/<name>` - set the values in
**Settings → Secrets**.

| Strategy | Use for |
|---|---|
| **Bearer** | A static API token in `Authorization: Bearer …`. |
| **API key** | A key in a header or query param. |
| **Basic** | username/password. |
| **OAuth2 client‑creds** | Machine‑to‑machine token from a token URL. |
| **OAuth2 (user login)** | 3‑legged OAuth: click **Connect**, grant access in the popup; Forge stores + **auto‑refreshes** tokens. Use for Google/HubSpot/Notion‑style user auth. |
| **CSRF + session** | Log in, extract a CSRF/session token, inject it on each call. |

**Per‑user connected credentials:** an OAuth2 (user‑login) provider can key its token bundle
**per end user** instead of sharing one account. Each end user then links their own downstream
account on the **Connect** screen (§10, *"Connect your accounts"*), and Forge injects *their*
credential when acting on their behalf over MCP or the run API - no token is ever passed through.

---

## 6. Knowledge (RAG)

**Knowledge → Files** to add sources; **Q&A** for curated question/answer pairs.

- **Add a source:** *Paste text*, a *URL*, *Crawl site* (same‑domain pages), or *Upload file*
  (.txt/.md/.csv/.json/.html/.pdf). Organize with folders.
- **Re‑ingest (↻)** a source to re‑fetch/re‑crawl or re‑embed under your current model.
- **Health banner:** if you switch embedding models, Forge flags sources that need
  re‑embedding (otherwise they'd silently vanish from search) - click **Re‑embed all**.
- **Use it:** add a **Retrieval** node before an agent (it grounds on documents and/or Q&A
  pairs, each toggleable), or enable **knowledge** directly on an Agent node so it searches
  per sub‑question.

---

## 7. Triggers & Channels (deploying)

**Channels** put a workflow in front of real users:

- **Email** - create an Email channel, enter **SMTP** (host/port/user/from + a password
  secret) for replies. Your provider (Mailgun/SendGrid/Postmark) posts inbound mail to the
  channel's inbound URL.
- **Live handoff** - add a **Human Handoff** node; escalated chats appear in the **Agent
  inbox**, and your reply is delivered back over the same channel.

**Triggers** lists each workflow's event entry points (webhook URLs, schedules) after you
publish a workflow containing a trigger node.

Every trigger answers **two separate questions**, and they vary independently:

| | Question | Values |
|---|---|---|
| **Runs as** | *Whose connected accounts does it use?* | a person |
| **Belongs to** | *Whose automation is it, and who sees it?* | **Project** or **Personal** |

A team automation can legitimately act through one person's Slack; a personal one can be handed
to a colleague without becoming everybody's. Keeping them apart is what lets both work.

**Runs as.** A trigger fires with nobody signed in, so each one names the person whose connected
accounts it uses - stamped with the editor who saved the workflow, and shown on every card. This
is what lets a scheduled workflow send mail from a connected Gmail (§4 → *Connectors*): it acts
as that person. Editing someone else's workflow does **not** take it over; ownership only moves
when someone explicitly clicks **Use my accounts**, or an editor assigns it. A trigger showing
*runs as nobody* (one created before this existed, or by the AI assistant) still fires - it just
has no credential for connector‑backed tools until someone claims it.

**Project or Personal.** A platform team's prod monitor or nightly build belongs to the project:
everyone sees it, everyone can manage it. A salesperson's own lead‑chaser is theirs, listed only
for them. New triggers pick a sensible default from the role of whoever saved the workflow -
**owners and admins create Project triggers, everyone else creates Personal ones** - and either
can be moved with one click (**Share with project** / **Make personal**). Sharing requires
*editor*; making one personal is open to the person it runs as, since it's their account doing
the work. An edit to a workflow never changes an existing trigger's scope in either direction.

Project **admins do see** everyone's personal triggers, flagged as someone else's. Every run a
trigger produces already appears in **Traces**, so hiding the trigger that explains *why* those
runs happen would be privacy in appearance only - and would cost the person accountable for the
project the ability to answer for it.

> A webhook URL is unguessable but public: anyone holding it can fire the workflow, and it will
> act as the trigger's owner. **Scope is ownership, not a lock** - marking a trigger Personal
> changes whose list it appears in, not who can fire it. That is the same trust boundary a
> shared API key already has: the URL *is* the credential, so treat it as one.

---

## 8. Evaluations

**Evaluations → New dataset:** pick a workflow, a scoring mode (`contains` / `exact` /
`regex` / LLM‑`judge`), and add `{input, expected}` rows. **Run** to get a pass rate - use it
to catch regressions before publishing a change. The run **streams live**: every case appears
immediately and resolves as it finishes (a progress bar, a running/pass/fail pill, and per‑case
latency + tokens), so a big dataset shows progress instead of blocking. Each case runs on behalf
of you (the launching editor), so per‑user tool auth resolves your connected credential.

---

## 9. Observability & Settings

- **Traces** - every run as a **collapsible span tree** (model calls, tools, chains, sub‑agents)
  with tokens, latency, and **cost**, nested by real parent/child and labeled with your canvas node
  names. A deep‑agent dispatch shows as a named `subagent · <name>` span; collapse/expand all.
- **Playground** streams a live **Agent activity** timeline while a run is in flight — which
  sub‑agent (prominent) or tool (dimmed) is running, ticking to done/error as each finishes — and
  attributes the run to you (the signed‑in operator), so per‑user tool auth resolves your credential.
- **OpenTelemetry** - point `FORGE_OTEL_EXPORTER_OTLP_ENDPOINT` at a collector or Langfuse to export
  run traces (also configurable under Settings → Observability & Retention).

**Settings** is a section sidebar:

| Section | What's inside |
|---|---|
| **General** | Project name/description, **default model**, workspace ID. |
| **Members & Roles** | Invite members; set roles (owner/admin/editor/viewer/connector). |
| **API Keys** | **Model providers** (LLM keys) and **Secrets** (write‑only, encrypted, referenced as `secret://proj/<name>`), plus scoped, revocable platform API keys. |
| **Model Pricing** | Per‑model $/token rates that drive the cost meter. |
| **Budgets & Quotas** | USD / token caps and allowed‑model enforcement per project. |
| **Guardrails & Egress** | One I/O policy enforced on every agent (see below). |
| **Knowledge & Embeddings** | Embedding model, chunking defaults, and re‑embed health. |
| **Versioning** | Retention limit for entity **version history** (view/restore on each entity). |
| **Observability & Retention** | OpenTelemetry export + scheduled data‑retention purge. |
| **Advanced** | Feature flags and a **Danger zone** (delete project). |
| **History** | A read‑only log of settings changes. |

> Every successful mutation is also recorded to an **audit log** (who changed what, plus auth
> events), retained per your settings and exportable via the API.

### Guardrails & Egress
A single, admin‑gated policy that applies to **every agent in the project by default** - no
per‑agent wiring:
- **Content guardrails** - redact PII (email / card / IP / MAC / URL), add custom `Label = regex`
  patterns (phone, national ID…), and block terms, each with a *redact / mask / hash / block / flag*
  strategy, scanning input and/or output.
- **Network egress** - block private/loopback hosts and set allow/deny domain lists across every
  REST/GraphQL tool, webhook, `web_fetch`, and SQL host at once. A project can only **tighten** the
  server's egress policy, never loosen it.

---

## 10. Connect - MCP server, run API & embed

The **Connect** screen (Deploy → Connect) is where you expose this project to the outside world.
It covers the **run API**, an **integration reference**, the **MCP server**, and the **Embed**
widget.

### Expose an MCP server
Publish your project's tools to any MCP client (Claude Desktop, Cursor, VS Code). The endpoint is a
single URL - `…/v1/mcp/<project_id>` - and the surface is exactly the **enabled tools of the tool
sets you toggle "Expose"** (plus, optionally, `run_workflow`, `search_knowledge_base`, and
`lookup_faq`). Native clients connect **directly over Streamable‑HTTP/SSE** - no `mcp-remote`
bridge needed.

Choose how clients authenticate:
- **Project API key** - one shared key sent as `Authorization: Bearer <key>`. Server‑to‑server, no
  per‑user identity. Generate it on the screen.
- **Personal access token (PAT)** - a per‑user token (`forge_pat_…`) each teammate generates (and
  revokes) for themselves; the server then acts as *that* user (entitlements, `{{ctx.*}}`).
- **OAuth 2.1** *(optional, off by default)* - when enabled, a standard MCP client discovers Forge
  and the user logs in, with no pre‑shared key. Turn on with `FORGE_MCP_OAUTH_ENABLED` (review the
  security notes first).

**Connect your accounts:** if the project has per‑user auth providers (§5), each signed‑in user
links their own downstream account here so Forge can act on their behalf.

> **Connector role:** invite integration users as **connector** (Settings → Members & Roles) - the
> least‑privileged role. They can authenticate, self‑serve an MCP token, and call tools, but see no
> projects or settings.

### Consume external MCP servers
Under **Build → External MCP**, register an outside server (e.g. a GitHub or Slack MCP server),
then create an **MCP tool** (§4) that calls one of its tools.

### Run API & embed
The same screen also shows the **run API** (call a workflow server‑to‑server), a copy‑paste
**integration reference**, and the **Embed** snippet - a floating chat bubble you drop onto any
website, locked to the origins you allow. End users see only the conversation; steps, tokens, cost,
and node names stay private in the dashboard.

---

## 11. Import & export (portability)

Move your build between projects - or share it - as portable JSON **bundles**. On the **Tools**,
**Workflows**, **Agents**, and **Components** screens, use **Export** (pick items → download a
`forge.bundle/1` file) and **Import** (upload a bundle into the current project).

- **What travels:** the entity's full configuration. **Secret *values* never leave** - only
  `secret://…` *references* do, so after importing you recreate those secrets (and any missing auth
  provider) in the target project; the importer tells you which.
- **Never overwrites:** every import creates fresh items with new IDs, and a name clash is
  auto‑renamed (`…_imported`). References *within* a bundle (a workflow's subworkflows, an agent's
  tools/components) are rewritten to the new IDs.
- **Lands as a draft:** imported workflows arrive unpublished - review, then publish.
- **Permissions:** importing requires the **editor** role.

---

## 12. Sample use cases (end‑to‑end)

### A. Grounded support chatbot on your website
1. **Knowledge** → add your help docs (URL/crawl/upload) + a few **Q&A** pairs.
2. **Workflows** → `start → Retrieval (include Q&A) → Agent → end`. Agent prompt: “Answer only
   from the knowledge base; if it's not there, say you don't know and offer a human.”
3. **Publish**, test in the **Playground**.
4. **Deploy** via **Connect (MCP)** (point an MCP client at it) or call the run API from your
   own site's chat UI.
*Result: visitors chat with an assistant grounded in your docs, through your own front‑end.*

### B. Email support agent with human handoff
1. Build `email_in trigger → Retrieval → Agent → Human Handoff → end`.
2. **Channels** → **Email** channel with your SMTP settings (+ password secret); wire your
   inbound‑mail provider to the channel's inbound URL.
3. When the agent is unsure, the chat lands in the **Agent inbox**; a teammate replies and the
   answer is emailed back.

### C. Scheduled daily digest
1. `schedule trigger (cron 0 9 * * 1-5, message "Summarize overnight tickets") → Tool Call
   (your tickets API) → Agent (summarize) → Webhook Out (post to your channel)`.
2. Publish - the in‑process scheduler fires it each weekday at 9am.

### D. Multi‑intent router
1. `start → Classifier (labels: billing, technical, sales) → Router (one case each, Else =
   general) → a specialist Agent per intent → end`.
2. For two‑part questions, set Classifier `multi_label` + Router `multi`, and converge on a
   synthesizer agent before `end`.

### E. Tool‑using agent (e.g. order lookup)
1. **Tools** → REST tool `get_order` with a response projection; attach an **Auth Provider**.
2. **Agents** → an agent with that tool. **Workflows** → `start → Agent → end`.
3. Ask “where's order A‑1007?” - the agent calls the tool and answers from the projected result.

---

## 13. Going to production

Forge runs locally with **zero external infra** (SQLite + embedded Chroma + in‑process
scheduler). For production, set these and restart (the app **refuses to boot** if they're wrong):

- `FORGE_ENVIRONMENT=production`
- `FORGE_JWT_SECRET=<strong random>`  ·  `FORGE_AUTH_REQUIRED=true`
- `FORGE_BOOTSTRAP_ADMIN_PASSWORD=<your own>` (not the dev default)
- `FORGE_DATABASE_URL=postgresql+psycopg://…` (Postgres), then `alembic upgrade head`
  (and optionally apply `infra/postgres_rls.sql` for row‑level tenant isolation)
- `FORGE_PUBLIC_BASE_URL=https://forge.yourco.com` (OAuth redirects + webhook/channel URLs)
- Optional: `FORGE_REDIS_URL` (multi‑worker), `FORGE_OTEL_*` (tracing), `FORGE_EGRESS_ALLOW_HOSTS`,
  `FORGE_MCP_OAUTH_ENABLED=true` (delegated OAuth 2.1 for MCP clients).

See `.env.example` for the full, annotated list.

---

## 14. Glossary

- **RAG** - Retrieval‑Augmented Generation: search your docs, feed the best chunks to the model.
- **ReAct** - the agent loop: reason → call a tool → observe → repeat → answer.
- **Projection** - trimming a tool's response (JMESPath) so fewer tokens reach the model.
- **MCP** - Model Context Protocol: a standard way for AI clients to call tools/servers.
- **Trigger / Channel** - what *starts* a workflow vs. where it's *deployed*.
- **State / reducer** - the run's data and how parallel writes to a key are merged.
- **Idempotency key** - a header so a retried request doesn't run twice.
