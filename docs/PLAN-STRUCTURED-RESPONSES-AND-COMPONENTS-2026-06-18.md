# Implementation Plan — Structured Responses & Custom UI Components

_Date: 2026-06-18 · Status: proposed · Scope: two related but independent features_

## 0. Summary

Two features, one principle: **the model emits a tiny payload; the heavy markup lives client-side and never enters the token stream.**

1. **Structured responses (always on).** Every agent reply renders like ChatGPT/Claude — headings, paragraphs, bullets, tables, code — by having the model emit **GitHub-Flavored Markdown (GFM)** and rendering it with a client-side renderer. This is Forge's *default* output style, applied even when no custom component exists.
2. **Custom UI components (opt-in).** A new **Components** tab where the integrator **writes the component themselves — HTML + CSS + button/action settings (a code editor, not a visual builder)**. Components are saved per-project, attached to agents **by selector or by instruction (exactly like tool access)**, and at runtime are exposed to the agent **as widget-tools**. When the agent "calls" one, it emits only `{component_id, props}`; Forge renders the saved template. User interactions (add-to-cart, checkout, form submit) flow back as a **normal user message**.

Both must work in **two integration modes**:
- **Chatbot embed** — Forge renders the markdown + components.
- **Headless API** — Forge returns *whatever was configured* (markdown text + component id + props, and optionally server-rendered HTML) so the integrator's own app renders it.

This validates against the **MCP Apps** standard (OpenAI Apps SDK + mcp-ui, merged into an official MCP extension on 2026-01-26): widget = a tool that references a UI resource; model emits props; host renders a template; actions post back as a message. We adopt that *shape* on top of Forge's existing tool + custom-SSE-frame machinery.

---

## 1. Mental model & how it maps onto existing Forge

| New concept | Existing Forge analog it mirrors | File anchor |
| --- | --- | --- |
| `Component` resource | `Tool` model + service + router | `apps/api/forge/models`, `services/tools.py`, `routers/tools.py` |
| Components tab (code editor) | Tool Builder screen | `apps/web/components/screens/tool-builder.tsx` |
| Components nav entry | `PROJECT_NAV` "Build" section | `apps/web/lib/data.ts:348` |
| Attach components to agent (`config["components"]`) | `config["tools"]` resolution in agent node | `apps/api/forge/nodes/agent_node.py:75` |
| Component → widget-tool at runtime | `materialize_tool` / `build_rest_tool` | `apps/api/forge/tools/materialize.py`, `tools/rest.py` |
| props_schema → Pydantic args | REST tool field → args_schema | `apps/api/forge/tools/rest.py` |
| `component` render frame | `emit_event` custom SSE frame via `get_stream_writer()` | `apps/api/forge/nodes/data.py:146` |
| Frame transport (run path) | run stream already forwards `"custom"` | `apps/api/forge/services/runs.py:177,198` |
| Action → new user turn | existing `send()` message path | `apps/web/components/shell.tsx`, `screens/playground.tsx` |
| Blocking confirm (checkout) | `interrupt` / HITL approval frame | `apps/api/forge/services/assistant.py:830`, `nodes` human_input |

The big takeaway: **almost no new transport is needed.** The run stream already carries `custom` frames; we add (a) a `Component` resource, (b) a Components editor tab, (c) a widget-tool materializer that emits a `component` frame and returns an ack, and (d) frontend renderers for markdown and for components.

---

## FEATURE 1 — Structured (markdown) responses

### 1.1 Backend — the output-style contract (≈0 marginal tokens)

Add one concise, shared **OUTPUT_STYLE** preamble, prepended to every agent's system prompt:

> Respond in GitHub-Flavored Markdown. Use headings (≤ h3), short paragraphs, bullet/numbered lists, GFM tables for comparisons, and fenced code blocks with a language hint. Use **bold** sparingly for key terms. Do not emit raw HTML. Keep structure minimal — only as much as the answer needs.

- **Where:** prepend in `_build_prompt()` / `_common_kwargs()` in [agent_node.py:19](apps/api/forge/nodes/agent_node.py:19), gated by a config flag (default on) so an agent can opt out. Lives in the system prompt → free per-turn, and already cacheable via the existing `anthropic_prompt_caching` middleware ([agent_node.py:56](apps/api/forge/nodes/agent_node.py:56)).
- Models already emit GFM natively; this just makes it consistent and minimal.

### 1.2 Frontend — render markdown instead of plain text

Today the assistant bubble and playground render **pre-wrapped plain text**. Replace with a markdown renderer.

- **Library:** `streamdown` (Vercel) — built for token-by-token SSE: it **completes unterminated blocks mid-stream** (open code fences/lists), bundles Shiki highlighting and `rehype-harden` sanitization, and is a drop-in for `react-markdown`. (Fallback: `react-markdown` + `remark-gfm` + `rehype-highlight`.)
- **Where:** a new `<Markdown>` component used in `apps/web/components/shell.tsx` (AssistantPanel message bubble) and `apps/web/components/screens/playground.tsx` (streaming + final answer).
- **Styling:** map elements (`h1-3`, `p`, `ul/ol/li`, `table`, `code`, `pre`, `a`, `blockquote`) to Forge's existing CSS variables via the renderer's `components` prop. No model-emitted classes.
- **Safety:** keep raw-HTML rendering **off** (no `rehype-raw`) — agent output is untrusted. Memoize per-block for streaming performance.

### 1.3 API mode & integrator docs

- API integrators already receive the answer text over the run stream (`messages` frames) / final result. It's now guaranteed GFM — document that.
- Add a short **"Response formatting"** section to `docs/MANUAL.md`: the markdown contract, and a 10-line snippet showing how to render it (`streamdown`/`react-markdown` + `remark-gfm`) if they build their own client.

### 1.4 Token cost

Markdown ≈ **34–38% fewer tokens than JSON** and **~80% fewer than XML** for the same content; the OUTPUT_STYLE preamble is in the (cached) system prompt. Net per-turn overhead ≈ 0.

### 1.5 Tasks (Feature 1)

1. `OUTPUT_STYLE` constant + prepend logic + opt-out flag in `agent_node.py`.
2. `apps/web/components/markdown.tsx` (`<Markdown>` with styled `components` map).
3. Swap plain-text rendering → `<Markdown>` in `shell.tsx` and `playground.tsx`.
4. Add `streamdown` to `apps/web/package.json`; theme tokens for tables/code.
5. Docs: "Response formatting" section in `MANUAL.md`.

---

## FEATURE 2 — Custom UI Components (widgets)

### 2.1 Data model — `Component`

New SQLAlchemy model + migration, scoped per project (mirror `Tool`):

```
Component
  id, tenant_id, project_id
  name          # machine name, e.g. "product_card"  (also the widget-tool name)
  title         # display name
  description   # MODEL-FACING: when to use this widget — drives agent selection
  props_schema  # JSON Schema for the props the agent must supply
  html          # author-written template, Mustache-style {{props.x}} / {{#each}}
  css           # author-written, scoped to the component
  actions       # [{ id, label, message, params, style }] — the "button settings"
  sample_props  # for the editor's live preview
  kind          # "html" (Phase 2) | "declarative" (future) | "remote_url" (future)
  enabled, version, created_at, updated_at
```

- **Binding:** Mustache-style `{{ }}` (portable: identical rendering in Forge and in any integrator's language). Interpolated values are **HTML-escaped by default**.
- **Actions ("button settings"):** declarative. Each button has a label, a templated user-visible `message`, and structured `params`. Forge wires the click; the author writes no JS. (Advanced: an optional `<script>` may read a `window.forge` bridge — sandboxed.)

### 2.2 API endpoints (mirror `/tools`)

`apps/api/forge/routers/components.py` + `services/components.py`:
- `GET /v1/projects/{pid}/components` — list (id, name, description, props_schema, actions, version).
- `POST /v1/projects/{pid}/components` — create.
- `PUT /v1/projects/{pid}/components/{cid}` — update (bumps version).
- `DELETE …/{cid}`.
- `GET …/{cid}` — full definition incl. `html`/`css` (what a renderer fetches & caches).
- `POST …/{cid}/preview` — server-render with `sample_props` for the editor preview.

### 2.3 Components tab — a code editor (not a visual builder)

New screen `apps/web/components/screens/components.tsx` (mirror Tool Builder), nav entry in `PROJECT_NAV` "Build" group after **Tools** ([data.ts:354](apps/web/lib/data.ts:354)); wire the screen in `app/page.tsx` and `SCREEN_LABEL`.

Editor panes:
- **Name / title / description** (description is emphasized: "this is what the agent reads to decide when to use the widget").
- **Props** — add typed fields (name, type, required, description) → compiles to `props_schema`. Same UX as REST tool fields.
- **HTML** + **CSS** — two plain `<textarea>` editors (Monaco deferred to a later phase). A new component opens **pre-filled with the default table template** (Appendix A) so authors start from a working example and just tweak it.
- **Actions/buttons** — list of `{label, message, params, style}`.
- **Live preview** — renders the component in the sandboxed iframe with `sample_props`; a "fire action" log shows what each button would send back.

### 2.4 Attaching components to agents (selector + instruction)

Exactly like tools, two complementary paths:
- **Selector** (primary): the Agents screen and the agent-node config get a **Components** multi-select that writes `config["components"] = [component_id, …]` — the analog of `config["tools"]`.
- **Instruction**: the author can also name components in the agent's `system_prompt` to steer *when* to use them. Each component's `description` is the model-facing selection text regardless.

### 2.5 Runtime — materialize component as a widget-tool + emit a render frame

This is the core. Add `apps/api/forge/tools/components.py`:

```python
def build_component_tool(component, ctx):
    # 1. args_schema from props_schema (reuse the REST-tool JSON-Schema→Pydantic pattern)
    # 2. coroutine:
    async def _render(**props):
        validated = validate(props, component.props_schema)   # reject/repair bad props
        from langgraph.config import get_stream_writer
        try:
            get_stream_writer()({
                "channel": "component",
                "payload": {
                    "component_id": component.id,
                    "name": component.name,
                    "version": component.version,
                    "instance_id": new_id(),
                    "props": validated,          # goes to the CLIENT, not the model
                    "actions": component.actions,
                },
            })
        except Exception:   # no active stream (e.g. ainvoke) — degrade gracefully
            pass
        return f"Rendered '{component.name}'."   # tiny ACK is all the model sees
    return StructuredTool.from_function(
        coroutine=_render, name=component.name,
        description=component.description, args_schema=...)
```

Wire it into the agent node — add to `_common_kwargs()` in [agent_node.py:75](apps/api/forge/nodes/agent_node.py:75), right after tools/knowledge/mcp:

```python
for comp in ctx.components_for(config.get("components", [])):
    tools.append(build_component_tool(comp, ctx))
```

- Add `components_for(...)` to `CompileContext` and preload project components in the runtime assembler (`services/runtime.py`, alongside tools/MCP).
- **Token split:** the model emits only the tool call (`{props}`) and reads back `"Rendered 'product_card'."`. The `html`/`css`/full props **never touch the prompt or completion** — the MCP Apps `structuredContent` vs `_meta` split, realized with LangGraph's `get_stream_writer` (the same mechanism `emit_event` uses, [data.py:146](apps/api/forge/nodes/data.py:146)).
- **Transport:** the **run stream already forwards `custom`** ([runs.py:177,198](apps/api/forge/services/runs.py:177)) → the `component` payload reaches Playground / chatbot / API integrators with no new stream plumbing. (For the in-app **assistant** path, add `"custom"` to its `stream_mode` in [assistant.py:802](apps/api/forge/services/assistant.py:802) and map it to a `component` frame — optional, since user components run on the run path.)

### 2.6 Rendering — chatbot embed (Forge renders)

Frontend handler for the `custom`/`component` frame in `shell.tsx` + `playground.tsx` (and the embeddable chat widget):
- New `apps/web/components/component-renderer.tsx`. On a `component` frame: fetch the component definition (`GET …/components/{id}`, cached by `id@version`), interpolate `props` into `html` (Mustache, escaped), and render inside a **sandboxed `<iframe srcDoc>`** with `css` scoped. `sandbox` = minimal (`allow-scripts` only if a component opts into `<script>`; never `allow-same-origin` for untrusted authors).
- **Buttons/actions:** a tiny injected bridge listens for clicks on declared actions and `postMessage`s `{action_id, params}` to the parent; the parent validates origin, composes a user message, and posts it (see 2.8).
- **Forms/inputs:** a `<form>` submit collects named fields into `params` → same post-back path. (Confirms the user's point: input *and* output are just a user message.)

### 2.7 Response contract — headless API (integrator renders)

The integrator consumes the run stream (or final result) and gets a `component` event:

```json
{ "event": "custom",
  "data": { "channel": "component",
    "payload": { "component_id": "...", "name": "product_card", "version": 3,
                 "instance_id": "...", "props": { ... }, "actions": [ ... ] } } }
```

Two render options for the integrator:
1. **By reference (lean, cacheable, default):** fetch `GET …/components/{id}` once, cache by `id@version`, render `props` in their own app, post actions back to the same run endpoint.
2. **Inline HTML (zero-setup):** request `?render=inline` and Forge includes server-rendered, interpolated `html`+`css` in the payload; the integrator drops it into a sandboxed container.

Either way the **model output is identical and minimal** (id + props); HTML over the wire is bandwidth, not tokens.

### 2.8 Interaction loop — actions become a user turn

- Click/submit → bridge `postMessage` → parent composes a **user message**: visible text = templated `action.message` (e.g. "Add Acme Widget to cart"), with structured `params` carried in message metadata for the agent.
- Re-enter via the existing `send()` path → normal agent turn. The agent decides the next step (and may render another widget — e.g. a cart summary).
- **Blocking confirmations** (checkout/payment): use the existing **HITL `interrupt`** mechanism ([assistant.py:830](apps/api/forge/services/assistant.py:830) / `human_input` node) so the run pauses for explicit approval rather than just chatting.

### 2.9 Security

- Untrusted author HTML renders **only inside a sandboxed iframe** (no parent DOM/cookies/storage; no `allow-same-origin` by default).
- Validate every `postMessage` **origin**; never post with `targetOrigin: "*"`.
- **Re-validate** widget→host `params` server-side against a per-action schema before they re-enter the agent.
- HTML-escape interpolated props by default; CSP allow-list for any external resources a component loads; host decides which (if any) tools a widget may call.
- Validate agent-supplied props against `props_schema` before rendering (reject/repair).

### 2.10 Tasks (Feature 2)

**Backend**
1. `Component` model + Alembic migration.
2. `services/components.py` (`ComponentService`: CRUD, validate, server-side preview render).
3. `routers/components.py` (endpoints in 2.2) + register router.
4. `tools/components.py` (`build_component_tool`: props_schema→args, emit `component` frame, ack).
5. `CompileContext.components_for(...)` + preload in `services/runtime.py`.
6. Hook into `agent_node._common_kwargs` (`config["components"]`).
7. (Optional) add `"custom"` to assistant `stream_mode` + map to `component` frame.

**Frontend**
8. `components/screens/components.tsx` (code editor + props + actions + live preview).
9. Nav entry in `PROJECT_NAV`; screen wiring in `app/page.tsx` + `SCREEN_LABEL`; API client methods in `lib/api.ts`.
10. Components multi-select on the Agents screen / agent-node config panel.
11. `components/component-renderer.tsx` (sandboxed iframe, Mustache interpolation, action bridge).
12. Handle the `component` frame in `shell.tsx` + `playground.tsx` (+ embeddable widget).

**Docs**
13. "UI Components" section in `MANUAL.md`: authoring an HTML/CSS component, props, actions; attaching to an agent; the API response contract + how to render headless.

---

## 3. Integration modes & the contract

- **Chatbot embed:** Forge's chat surface (Playground today; an embeddable web widget is the natural consumer) renders markdown (F1) and components (F2). Reuses the run-stream contract.
- **Headless API:** integrator runs a workflow and consumes the stream/result; receives GFM text + `component` frames (id + props, optionally inline HTML). Renders in their own app; posts actions back as user messages.

Authentication, browser-safe credentials, and how components reach each surface are detailed in **§6**.

The wire contract (markdown text + `component` frames) is the **single source of truth** for both modes — documented once, consumed by Forge's renderer and by external apps alike, aligned to MCP Apps so Forge components can later be exported to / imported from other MCP hosts.

---

## 4. Phased delivery

- **Phase 1 — Structured responses.** Tasks F1.1–F1.5. Small, immediate UX win; no schema/API changes.
- **Phase 2 — Components core.** Data model, CRUD, Components editor tab, widget-tool materialization, `component` frame, agent selector, chatbot renderer (Tier: HTML-in-sandboxed-iframe), preview. Tasks F2.1–F2.6, F2.8–F2.12 (sync render).
- **Phase 3 — Headless contract + interactions.** API render-by-reference + `?render=inline`, action post-back loop, HITL confirmations, security hardening, docs. Tasks F2.7–F2.9, F2.13.
- **Phase 4 (later) — Extensions.** Declarative/no-iframe primitive renderer for trusted/themable widgets; `remote_url` components; MCP Apps import/export for cross-host portability.

---

## 5. Decisions & defaults (chosen unless you object)

1. **Markdown, not JSON/HTML, for prose** (F1) — cheapest, native to models, renders like ChatGPT/Claude.
2. **Author writes code; no visual builder** (per your direction) — Components tab is a code editor with live preview.
3. **HTML-in-sandboxed-iframe** is the Phase-2 render tier (handles arbitrary author HTML/CSS safely); declarative primitives deferred to Phase 4.
4. **Mustache-style binding** for portability across Forge's renderer and integrators' languages.
5. **Components attach like tools** via `config["components"]` (selector) + system-prompt mention (instruction).
6. **Reuse the `custom` stream channel** (`get_stream_writer`) — no bespoke transport.
7. **Wire contract = markdown + `component` frames**, aligned to MCP Apps shape.

**Open questions for you:**
- A. *(Auth + component-delivery design now resolved in §6.)* Open: does an embeddable widget exist in the codebase yet, or is building it part of this scope (vs. Playground-only for now)?
- B. Component scope — strictly per-project, or also shareable/global across a tenant's projects (library)?
- C. *(Resolved)* Editor = two plain textareas (HTML + CSS) + a live preview pane + the default table template pre-loaded (Appendix A). Monaco deferred.

---

## 6. Embedding, authentication & component delivery

_Resolves open question A and "how do components pass through in server-to-server mode."_

### 6.1 The rule

The **secret API key is server-to-server only** — it never reaches a browser. A browser may talk to Forge **directly**, but only with a **browser-safe credential** (publishable key or short-lived session token). "Server-to-server" describes where the *secret call* happens — **not** where rendering happens.

### 6.2 Three connection modes

| Mode | Who calls Forge | Credential | Forge renders? |
| --- | --- | --- | --- |
| **Headless API** | Integrator backend | **Secret API key** | No — integrator's UI does |
| **Widget · public** | Browser → Forge directly | **Publishable key** + origin allow-list | Yes (Forge widget) |
| **Widget · verified** | Browser → Forge directly | **Session token** (minted server-side) | Yes (Forge widget) |

- **Publishable key** — safe in HTML; identifies the project and is scoped to one op ("chat with this published workflow"), no data/config access; locked to **allowed origins** (CORS + `Origin` check) + edge rate-limiting/abuse protection.
- **Session token (verified)** — the integrator backend calls Forge server-to-server (secret key) to mint a short-lived, scoped token (project + workflow + end-user, optional HMAC identity à la Intercom), hands it to the widget, and the widget streams **directly** from Forge. Secret stays server-side; the stream stays low-latency. Refreshed via the backend on expiry.

### 6.3 Where components render (the key point)

HTML/CSS only renders in a browser. Regardless of mode, the `component` frame flows:

```
Forge ──stream──▶ [integrator backend relay] ──▶ integrator frontend ──▶ browser renders
```

In headless mode the integrator backend is a **pass-through relay** for the SSE stream; the **frontend** renders. Components travel as structured data (`component_id` + `props`, or pre-rendered HTML) in the same stream as the text — server-to-server changes nothing about that.

### 6.4 What the integrator must do, per surface

| Surface | Component integration effort |
| --- | --- |
| **Forge widget** (public/verified) | **None.** The widget receives the frame, fetches the definition, renders in a sandboxed iframe, and posts actions back. Integration = one `<script>` tag. |
| **Own UI — inline HTML** | **Small.** Start the session with `ui: "inline-html"`; each `component` frame carries server-rendered `html`+`css`; drop it into a sandboxed `<iframe srcdoc>` and forward action `postMessage`s as the next user message. |
| **Own UI — renderer SDK** | **Small.** `npm i @forge/embed`; pipe `component` frames into `<ForgeComponent frame onAction>` — it handles fetch / interpolation / sandbox / actions while you keep your own chat UI. |
| **Own UI — by reference** | **Most, optional.** Fetch `GET /components/{id}`, cache by `id@version`, render `props` with your own templating. Only if you want to fully restyle. |
| **Text-only** (SMS, voice, backend automation) | **None.** Declare `ui: "none"`; Forge doesn't expose widget-tools, so the agent answers in markdown. |

### 6.5 Capability negotiation (why "do nothing" still works)

At session/run start the caller declares its surface: `capabilities: { ui: "forge-widget" | "inline-html" | "renderer-sdk" | "none" }`. Forge exposes widget-tools to the agent **only when the surface can render them** (mirrors MCP Apps hosts advertising UI support) and shapes the frame to match. So a headless integrator who does nothing extra still gets correct behavior — plain text — because components are **opt-in by capability**, not forced on every caller.

### 6.6 Identity, actor & authorization

"Who initiated the conversation" is a first-class **actor** distinct from anything in the chat text, and it must come from a trusted source.

- **Shape:** `end_user: { id, name, email?, role?, region?, entitlements / allowed_accounts? }` — whatever the workflow + tools need.
- **Source of trust (by mode):**
  - *Verified widget* — claims live inside the **session token** (minted by the integrator's backend, server-to-server).
  - *Headless API* — the integrator's backend passes `end_user` in the **run-create body** (already trusted; it authenticated the user).
  - *Public / anonymous widget* — only an anonymous session id, **no entitled actor**. Gate sensitive flows (e.g. quote creation) behind verified mode.
- **Binding:** on conversation/run creation Forge pins `end_user` to the Thread (`Thread.meta.end_user`, reusing the existing column at [entities.py:162](apps/api/forge/models/entities.py:162)) and to the audit actor ([audit_middleware.py:6](apps/api/forge/audit_middleware.py:6)). This mirrors how channels already capture a sender ([email.py:27](apps/api/forge/channels/email.py:27), `customer=` at [channels.py:125](apps/api/forge/routers/channels.py:125)) — the widget is just another channel with a trusted identity source. **The chat message never carries identity.**
- **Initiator vs. subject (critical):** the *initiator* (actor) comes from the token; "create quote for **so-and-so**" names a *subject* parsed from the message — untrusted input, validated against the initiator's entitlements before any tool acts. Never derive the initiator from message text.
- **Runtime use:** `end_user` lives in the run/compile context →
  - injected into the system prompt (dynamic prompt): "You're assisting {{end_user.name}} ({{end_user.role}})…";
  - referenceable in tool templates (`{{end_user.id}}`) and forwarded as an **on-behalf-of** credential to the downstream API;
  - enforced as an **authorization gate** — the agent cannot exceed the user's entitlements regardless of what it's asked (defeats prompt-injection / "quote for an account I don't own").
- **Audit:** actor persisted on Run/Trace → "quote #123 created by jane@westcon via run R."

### 6.7 How the session token reaches the widget & travels

The embeddable `<script>` boots with a **publishable key** (project + allowed origins). For verified mode it also needs a per-user token, obtained at runtime — never hardcoded, and the secret key never reaches the browser:

1. The widget calls a **token endpoint on the integrator's own backend** (e.g. `GET portal.westcon.com/api/forge-chat-token`). The user is already logged into that portal (session cookie), so the backend knows who they are.
2. That backend calls Forge **server-to-server with the secret key** to mint a short-lived, scoped session token carrying the `end_user` claims, and returns it to the widget.
3. The widget sends the token to Forge in the **HTTP `Authorization: Bearer …` header** on each request — **not inside the chat message body**. Identity is transport-level; the body is just the user's text.

- **Lifecycle:** tokens are short-lived (e.g. 15–30 min). On expiry the widget silently re-fetches from the integrator's token endpoint (still authed by the portal session) — no re-login.
- **SSE caveat:** the browser `EventSource` API can't set custom headers, so the token rides the create-run `POST` (header); the run is then pinned to that `end_user` and the stream is authorized by the run binding (or a short-lived signed stream URL). Forge's current `openSSE`/EventSource path ([api.ts](apps/web/lib/api.ts)) would move to fetch-based streaming or signed URLs for the authenticated embed.

---

## 7. Risks & mitigations

- **Untrusted HTML / XSS** → sandboxed iframe, escaped interpolation, origin-validated postMessage, server-side param re-validation.
- **Streaming markdown jank** → `streamdown` (completes partial blocks) + per-block memoization.
- **Model over-/under-using widgets** → crisp `description` per component; optionally the existing `llm_tool_selector` middleware; instruction guidance in the agent prompt.
- **Definition/version drift between Forge & integrators** → cache by `id@version`; bump version on edit; expose version in the frame.
- **Cross-host portability churn** (MCP Apps still stabilizing) → we own our renderer; treat MCP Apps export as Phase 4.

---

## 8. Scaling & production operations

Designed for millions of end-users from day one. The identity layer scales for free; the engineering goes into runtime, persistence, and streams.

### 8.1 Identity scales statelessly

- Session tokens are **signed JWTs** — minted on a stateless signature, **only when a user actually chats**, **reused for the session** (~15–30 min), with **no per-user row or provisioning** in Forge. Verification is signature-only (no DB lookup per request).
- **Tenants are provisioned (O = clients); end-users are ephemeral (O = active conversations).** Westcon = one tenant with one secret key; its millions of users are token claims, not Forge accounts. We never create a key/account per end-user.

### 8.2 The real scaling axes (and the levers Forge already has)

| Axis | Approach | Existing building blocks |
|---|---|---|
| **Compute / LLM throughput** | Stateless horizontal API workers; run execution off the request thread via a **queue + workers**; per-tenant concurrency caps; respect provider rate-limits/quotas | Redis + **arq** workers (optional dep), async FastAPI |
| **Persistence** | **Durable checkpointer** (Postgres) keyed by thread; **trace/run retention + TTL + partitioning**; archive cold data | `run_durability` setting; Postgres-swappable DB; Thread/Run/Trace tables |
| **Streaming (concurrent SSE)** | Stateless workers + pub/sub (Redis) so any worker serves any stream; direct browser→Forge (proxying through the integrator would double connections) | SSE infra; `openSSE` |
| **Rate limiting / quotas** | Token-bucket per `end_user.id`, per tenant, per publishable key; edge throttling; 429 + graceful "busy" | `tenant_budget`, `model_call_limit`, `tool_call_limit` middleware |
| **Cost control** | Per-tenant budgets; cap context growth; trim tool payloads; cache prompts | `summarization`, `anthropic_prompt_caching`, tool response projection, `llm_tool_selector` |
| **Reliability** | Idempotent run creation; retries/fallback; graceful degradation when LLM/downstream is down | idempotent create ([runs.py:36](apps/api/forge/routers/runs.py:36)), `model_retry` / `tool_retry` / `model_fallback` middleware |

### 8.3 Multi-tenant isolation

`tenant_id` scoping is already pervasive — enforce it on every component/widget/run/token path. Per-tenant secret + publishable keys. Noisy-neighbor protection via per-tenant concurrency + budget caps. Component HTML is tenant-authored → sandboxed so one tenant's widget can never reach another's data or the host page.

### 8.4 Observability & operations

Traces/spans + audit log already exist → extend with per-tenant metrics (runs, latency, tokens, cost, error/timeout rates, queue depth), health checks, and alerting. Every quote/action carries the `end_user` actor for audit.

---

## 9. Scenario dimensions — build axes, not 100 scenarios

There are hundreds of possible scenarios (e-commerce, quoting, support, booking, dashboards, surveys…). We do **not** code each one. We build the **axes of variation** so any scenario is *configuration + an authored component*, not new code. Every axis already has a home:

| Dimension | Range of scenarios | Absorbed by |
|---|---|---|
| **Surface / channel** | widget (public/verified), headless API, Teams, email, SMS/voice, backend automation, MCP host | **capability negotiation** (§6.5) + channel adapters |
| **Identity** | anonymous · authenticated + entitlements · system actor | **`end_user` context** + **entitlement authz** (§6.6) |
| **Render capability & theme** | forge-widget · inline-html · sdk · none · per-tenant styling | capability flag + **declarative components** + **CSS-variable theming** |
| **Component type** (the "100s") | product card, quote table, ticket form, chart, survey, confirmation… | **user-authored components** (`props_schema` + `html`/`css` + `actions`) — data, not code |
| **Action semantics** | fire-and-forget · blocking confirm · form submit · multi-step | action post-back as a user turn + existing **HITL interrupt** |
| **Trust level** | first-party · tenant-authored · untrusted | **sandboxed iframe** + escaping + CSP + server-side validation |
| **Scale / load** | one user · millions · bursty | stateless workers + queue + rate limits (§8) |
| **Failure modes** | token expiry · version drift · malformed props · downstream/LLM outage · prompt-injection | token refresh · `id@version` · schema validate/repair · retry/fallback · entitlement authz |
| **Statefulness** | one-shot · threaded · cross-session memory | threads + durable checkpointer + optional per-user memory keyed by `end_user.id` |

**Litmus test for any new scenario:** if it needs new *code* (not just a new component + config), an axis is missing — fix the axis, not the scenario.

---

## 10. Build order (security & scalability aware)

| Phase | Scope | Notes |
|---|---|---|
| **1. Structured responses** | Markdown contract + safe streaming renderer in chat & playground | Foundational, additive; no schema/auth change |
| **2. Components core** | `Component` model + CRUD + Components tab (2 textareas + live preview + default table template) + widget-tool materialization + `component` frame + Playground sandboxed render + agent selector | **Scope per-project** (default for open-Q B) |
| **3. Identity & authorization** | `end_user` context, verified-mode session tokens, publishable key + origin allow-list, entitlement authz, on-behalf-of tool calls, audit actor; headless `end_user` in run-create | The Westcon scenario lands here |
| **4. Embeddable widget + scale hardening** | Embed script + integrator token-endpoint pattern + fetch-streaming/signed SSE; durable checkpointer, queue workers, rate limits, retention/TTL, dashboards, backpressure | Production load readiness |
| **5. Extensions** | Shared component **library** (if open-Q B = shared), MCP Apps export, declarative primitives, per-tenant theming | Non-breaking add-ons |

**Open-question defaults (correct me anytime):** B → components are **per-project** first; a tenant-shared library is a Phase-5, non-breaking add-on. A → **Playground-first** for components (Phase 2); the embeddable widget + token/identity machinery is Phase 3–4.

**Status (2026-06-18):** Phase 1 implemented — `OUTPUT_STYLE` GFM preamble on workflow agents ([agent_node.py](apps/api/forge/nodes/agent_node.py)) + a safe, memoized streaming `<Markdown>` renderer ([markdown.tsx](apps/web/components/markdown.tsx), `.md` styles in [globals.css](apps/web/app/globals.css)) wired into the assistant panel ([shell.tsx](apps/web/components/shell.tsx)) and playground ([playground.tsx](apps/web/components/screens/playground.tsx)). `pnpm --filter web build` compiles + type-checks clean. NOTE: build/run requires **Node ≥18** — the dev shell's default was Node 10; use `nvm use 22` (22 and 18 are installed).

**Status (2026-06-18) — Phase 2 backend done:** `Component` model ([entities.py](apps/api/forge/models/entities.py)) + CRUD service/router ([services/components.py](apps/api/forge/services/components.py), [routers/components.py](apps/api/forge/routers/components.py), registered in [main.py](apps/api/forge/main.py)) + widget-tool materializer ([tools/components.py](apps/api/forge/tools/components.py)) that emits a `component` custom-stream frame and returns only an ack (markup never enters tokens) + `CompileContext.components_for` ([context.py](apps/api/forge/engine/context.py)) + runtime preload ([runtime.py](apps/api/forge/services/runtime.py)) + agent `config["components"]` wiring ([agent_node.py](apps/api/forge/nodes/agent_node.py)). API imports clean; component endpoints present in the OpenAPI schema (76 paths). DB table auto-creates via `create_all` (no migration needed). **Phase 2 frontend done:** Components editor tab ([screens/components.tsx](apps/web/components/screens/components.tsx)) — HTML/CSS/props/actions + live sandboxed preview, pre-loaded with the weather-table default; sandboxed-iframe renderer ([component-renderer.tsx](apps/web/components/component-renderer.tsx)) with Mustache interpolation + button/form action post-back; wired into the Playground ([playground.tsx](apps/web/components/screens/playground.tsx)) on the `component` SSE frame; plus nav + routing + api client + sidebar counts. `pnpm --filter web build` compiles + type-checks clean (Node ≥18). **Phase 2 complete:** the agent components-selector is wired into the Agent config ([AgentConfig.tsx](apps/web/components/canvas/AgentConfig.tsx) + [screens/agents.tsx](apps/web/components/screens/agents.tsx)) — a chips multi-select writing `config["components"]`, mirroring the tools selector (no agent-schema change needed; the schema is lenient). The full **author → attach → render → action** loop is in place and the web app builds clean (Node ≥18; clear `.next` if a stale-cache `/_not-found` error appears). **Next: Phase 3** — identity & authorization (`end_user` context, verified-mode session tokens, publishable key + origin allow-list, entitlement authz, audit) per §6/§10; then Phase 4 (embeddable widget + scale hardening), Phase 5 (extensions).

**Refinements (from testing):** components now render inside the assistant turn (one avatar, no separate "message"); the agent is steered (prompt + tool ack) to render a component *instead of* restating its data as text; action buttons auto-render from the `actions` config (plus inline `data-forge-action`); the default table header is conditional (`{{#col1}}…`); and the components selector is now also on workflow agent nodes. (Existing components keep their saved HTML — re-apply the conditional header or recreate to pick it up.)

**Status (2026-06-18) — Phase 3a done (identity foundation, app-agnostic):** a generic `end_user` actor (`{id, display_name?, email?, roles?, attributes?, entitlements?}` — no product-specific shape) now flows from the run-create API → `Thread` (`user_external_id` + `meta.end_user`) → `CompileContext.end_user` ([context.py](apps/api/forge/engine/context.py), [runtime.py](apps/api/forge/services/runtime.py)) → the agent system prompt (an identity-awareness block, [agent_node.py](apps/api/forge/nodes/agent_node.py)) + REST tool templating context (`{{ctx.end_user…}}` / on-behalf-of, [rest.py](apps/api/forge/tools/rest.py)) + the run `Trace.meta` (audit, [runs.py](apps/api/forge/services/runs.py)). Supplied via the headless run-create body (the integrator's authenticated backend, server-to-server) or the Playground's **"Acting as"** test control. Backend imports clean; web builds clean. **Phase 3b pending:** the browser token/key subsystem — publishable key + origin allow-list, server-minted verified session tokens carrying `end_user` claims (so the widget never holds the secret), and entitlement *enforcement* at the tool layer (3a gives the agent awareness; hard gates land in 3b).

**Status (2026-06-18) — full audit + fixes:** A 51-agent review of all uncommitted changes (full findings in [AUDIT-2026-06-18.md](docs/AUDIT-2026-06-18.md); 33 confirmed) drove fixes for: the **KB/FAQ over-refusal regression** (positively-scoped `[END USER]` block, gated on actual roles/entitlements, whitelisted + clamped); the **table-vs-prose conflict** (components-aware OUTPUT_STYLE that drops the markdown-tables clause + imperative COMPONENT_STYLE that keeps retrieval/tool-use primary); **text+component flow** (assistant replies render as ONE flowing message — bare markdown + inline components, no bubble — with live in-flight rendering from `liveParts`); **components on non-Playground channels** (`run_to_completion` now drives a custom-stream loop + non-empty fallback); **finalAnswer/error never dropped** when a component is present; **missing-component fallback**; **interrupt-flush** (Playground); **props validation + size cap**; **component-name charset**; **project-scoped component CRUD**; **version-bump-on-change**; **iframe origin guard + form-scoped actions**. Verified: API imports clean, web builds clean (Node ≥18). Deferred (lower-risk / Phase 3b): DB unique-constraint on component name (needs migration), renderer memoization (perf), server-side entitlement *enforcement* (3b), `streamdown` (cosmetic), per-version component fetch.

**Status (2026-06-18) — Phase 3b core done (verified identity + authorization):** a `session` JWT type ([security.py](apps/api/forge/security.py)) carries a signed `end_user`; the integrator's backend mints one server-to-server via `POST /v1/projects/{id}/session-tokens` ([embed.py](apps/api/forge/routers/embed.py), editor-role). Run-create now verifies a `session_token` (tenant/project-scoped, expiring) and uses its `end_user` as the **trusted** identity — overriding any body value; the headless body `end_user` is a typed `EndUser` model. **Server-side entitlement enforcement**: a tool that declares `required_entitlements` is denied independently of the LLM when `ctx.has_entitlements` fails ([context.py](apps/api/forge/engine/context.py) + [rest.py](apps/api/forge/tools/rest.py)) — the real hard gate, not just prompt guidance. Remaining deferred audit fixes also landed: component-name uniqueness (constraint + 409 pre-check), "Acting as" validation, type-aware action-field collection, renderer memoization. Verified: API imports clean (token roundtrip + gate), web builds clean. **Remaining (separate, scoped build):** the embeddable widget UI + the publishable-key / per-origin-CORS browser *transport* (the crypto + enforcement core is ready for it), and extending the entitlement gate to non-REST tools.

**Status (2026-06-18) — embeddable widget + publishable-key transport done.** Architecture: the widget is served **same-origin** from Forge (`/embed?key=…`) and dropped into a host site via an `<iframe>`, so widget↔API calls go through the existing `/api/forge` proxy — **no cross-origin CORS**. Backend: `Project.embed_key` (indexed publishable key, [entities.py](apps/api/forge/models/entities.py) + dev auto-migrate in [db/base.py](apps/api/forge/db/base.py)); embed settings (enabled / allowed_origins / workflow_id in `config["embed"]`) managed via `GET|PUT /v1/projects/{id}/embed` ([embed.py](apps/api/forge/routers/embed.py), editor-role); a **key-gated public router** ([embed_public.py](apps/api/forge/routers/embed_public.py)) with `/config`, `/components`, `POST /runs`, `GET /runs/{id}/stream` — resolves the project by key, requires embed enabled, rate-limits per key (60/min), runs only the configured/active workflow, and derives `end_user` **only** from a verified `session_token` (else anonymous). Frontend: standalone [/embed widget](apps/web/app/embed/page.tsx) (reuses `Markdown` + `ComponentRenderer` + the parts/flow model), a [middleware](apps/web/middleware.ts) that sets `Content-Security-Policy: frame-ancestors` from the project's allowed_origins (default `'self'` blocks external embedding until allow-listed), and a console [Embed screen](apps/web/components/screens/embed.tsx) (enable, pick workflow, allow-list origins, copy iframe snippet + key). Verified: API imports + all 5 embed routes registered; web builds clean (`/embed` route + middleware emitted); stream-event + run-input shapes and `create_run`/`stream` signatures match the Playground path. **Needs the running stack + an LLM key to E2E** (enable embed → open `/embed?key=…` → chat). **Remaining/deferred:** interrupt (human-in-the-loop) handling inside the widget; a floating launcher bubble (vs. raw iframe); per-version component fetch; Alembic migration for `embed_key` on managed Postgres (dev SQLite auto-adds it).

**Status (2026-06-18) — widget Playground-parity + floating launcher done.** Mapped the Playground's full behavior and the backend resume/interrupt contract with a 4-agent understand workflow, implemented, then ran a 4-dimension adversarial review (8 agents) and fixed all 3 confirmed findings. **Interrupt/HITL parity:** the widget now ports the Playground's `parseInterrupt` (the `middleware` flag drives the resume value encoding — `{decisions:[{type}]}` vs bare string), the pre-pause commit, the approval card (one button per decision, approve=primary), and the resume-response handling; backed by a new key-gated `POST /v1/embed/{key}/runs/{run_id}/resume` ([embed_public.py](apps/api/forge/routers/embed_public.py)) that reuses `RunService.resume` scoped to the key's tenant. **Floating launcher:** [public/launcher.js](apps/web/public/launcher.js) — a self-contained, idempotent, accessible (aria/focus/Escape), mobile-fullscreen bubble that lazy-loads the iframe and speaks a `forge:`-namespaced, origin-locked postMessage protocol (`forge:ready`/`forge:host`/`forge:close`); the console [Embed screen](apps/web/components/screens/embed.tsx) now offers the bubble snippet (recommended) + the raw iframe. **Review fixes:** in-iframe Escape handler (host-page key handler can't see cross-origin focus); handshake no longer trusts `"*"` — the launcher passes its origin via `?host=…` and the widget validates strictly; the component sandbox bridge now posts with a concrete `targetOrigin` instead of `"*"`. **Intentional non-parity:** the run-steps panel and token/cost meter are deliberately NOT shown to anonymous end users (operator-only; would leak LLM cost + internal node names). Verified: API imports + resume route registered; web builds clean. Still needs the running stack + an LLM to E2E an actual approval round-trip.

---

## Appendix A — Default component template (ships in the editor)

A new component opens pre-filled with this working, theme-aware example — a simple 2-column table (a 3-day weather forecast). Binding is Mustache-style; interpolated values are HTML-escaped; colors use CSS variables so the host page (or Forge theme) can restyle without editing the component.

**HTML**
```html
<div class="forge-card">
  <div class="forge-card__title">{{title}}</div>
  <table class="forge-table">
    {{#col1}}<thead><tr><th>{{col1}}</th><th>{{col2}}</th></tr></thead>{{/col1}}
    <tbody>
      {{#rows}}
      <tr><td>{{label}}</td><td>{{value}}</td></tr>
      {{/rows}}
    </tbody>
  </table>
</div>
```

**CSS**
```css
.forge-card {
  font-family: var(--forge-font, system-ui, -apple-system, sans-serif);
  color: var(--forge-text, #1a1a1f);
  background: var(--forge-surface, #ffffff);
  border: 1px solid var(--forge-border, #e3e3e8);
  border-radius: 12px; overflow: hidden; max-width: 560px;
}
.forge-card__title {
  font-weight: 600; font-size: 14px; padding: 12px 16px;
  border-bottom: 1px solid var(--forge-border, #e3e3e8);
}
.forge-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.forge-table th, .forge-table td {
  text-align: left; padding: 10px 16px;
  border-bottom: 1px solid var(--forge-border-soft, #f0f0f3);
}
.forge-table th {
  font-weight: 600; color: var(--forge-muted, #6b6b76);
  background: var(--forge-header, #fafafb);
}
.forge-table tbody tr:last-child td { border-bottom: none; }
.forge-table tbody tr:hover { background: var(--forge-hover, #f7f7f9); }
```

**Sample props** (drives the live preview)
```json
{
  "title": "Weather — London",
  "col1": "Day",
  "col2": "Forecast",
  "rows": [
    { "label": "Mon", "value": "Sunny · 24°C" },
    { "label": "Tue", "value": "Cloudy · 21°C" },
    { "label": "Wed", "value": "Rain · 18°C" }
  ]
}
```
```
