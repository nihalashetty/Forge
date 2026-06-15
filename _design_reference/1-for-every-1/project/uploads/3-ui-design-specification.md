# AI Agent Platform — UI/UX Design Specification
**Version:** 1.0 · **Date:** June 2026 · **Audience:** Claude Design and frontend engineers · **Codename:** Forge

> Document 3 of 3. Pairs with Doc 2 (Technical Design). This spec defines the product's look, feel, structure, and every key screen. It commits to a **specific aesthetic** — follow it rather than defaulting to generic dashboard styling. Where a screen maps to backend objects, the object names match Doc 2.

---

## 0. How to use this document
- The **design tokens** in §3 are authoritative. Implement them as CSS variables; never hardcode colors/spacing.
- Each screen section gives: **purpose**, **layout**, **components**, **states**, **interactions**.
- The **node design language** (§7) and **config-form pattern** (§8) are the two highest-leverage systems — most of the product is built from them.
- Build the canvas with **React Flow v12 (`@xyflow/react`)**, components with **shadcn/ui + Tailwind**, but re-skinned to the tokens below (do *not* ship default shadcn slate).

---

## 1. Design philosophy & personality

Forge is a **precision instrument for building thinking machines.** It should feel like a piece of professional engineering equipment — a modular synth rack, a flight console, a high-end audio interface — not a consumer SaaS dashboard. The user is doing serious, intricate work (wiring agents, shaping payloads, controlling token budgets). The UI's job is to make enormous configurability feel **calm, legible, and under control**, never cluttered or toy-like.

Three personality words: **Precise. Composed. Powerful.**

- **Precise** — sharp edges, exact alignment, monospace for anything technical (IDs, tokens, JSON paths, counts). Numbers matter; show them.
- **Composed** — dark-first, generous breathing room around dense controls, one confident accent, restrained motion. Complexity is revealed progressively, never dumped.
- **Powerful** — the canvas is the hero; nothing important is more than two clicks away; power users can keyboard-drive everything.

Anti-goals: purple-gradient-on-white "AI slop," rounded pill everything, cartoon mascots, dense walls of unstyled forms, hidden capability.

---

## 2. Aesthetic direction (committed)

**"Forge console" — warm-signal-on-cool-graphite, dark-first.**

The product is graphite and near-black (the cool, calm substrate), with a single **molten accent** (the heat of the forge) used sparingly for primary actions, active states, and energy. A **cool cyan** secondary handles links/selection so the warm accent stays special (warm dominant + sharp cool accent). Surfaces are layered, subtly elevated, with hairline borders and faint inner glows on active elements — like backlit equipment. The workflow canvas reads like a circuit board / patch bay: a fine dotted grid, glowing typed connection cables, and chip-like nodes.

Light theme exists (for long reading/config sessions) but the **canvas and playground are dark-first**; the marketing site and docs may be light.

---

## 3. Design tokens

### 3.1 Color — Dark (default)
```css
:root[data-theme="dark"] {
  /* Substrate */
  --bg-0:        #0A0C0F;   /* app background, canvas base */
  --bg-1:        #0F1318;   /* panels, sidebar */
  --bg-2:        #151A21;   /* cards, node bodies */
  --bg-3:        #1C232C;   /* raised: inputs, popovers, node headers */
  --bg-hover:    #222A34;
  --line:        #2A323D;   /* hairline borders */
  --line-strong: #3A4452;

  /* Text */
  --fg-0:        #EEF2F6;   /* primary */
  --fg-1:        #AAB4C0;   /* secondary */
  --fg-2:        #6E7884;   /* muted, captions, placeholders */
  --fg-on-accent:#1A0E08;

  /* Molten accent (primary / heat) */
  --accent:      #FF6A3D;
  --accent-hi:   #FF norm; /* use #FF norm? no -> */
  --accent-bright:#FF855E;
  --accent-dim:  #C24E2B;
  --accent-glow: rgba(255,106,61,0.22);

  /* Cool secondary (links / selection / "signal") */
  --signal:      #34D6C6;
  --signal-dim:  #1F8C82;
  --signal-glow: rgba(52,214,198,0.18);

  /* Semantic */
  --ok:    #46C97E;  --ok-bg:  rgba(70,201,126,0.12);
  --warn:  #F0B23C;  --warn-bg:rgba(240,178,60,0.12);
  --err:   #F2615B;  --err-bg: rgba(242,97,91,0.12);
  --info:  #5B9BF2;  --info-bg:rgba(91,155,242,0.12);

  /* Port / IOType colors (used on handles, edges, type chips) */
  --io-messages:  #34D6C6;   /* teal   */
  --io-text:      #8A93A0;   /* slate  */
  --io-json:      #A78BFA;   /* violet */
  --io-tool:      #FF6A3D;   /* amber/molten */
  --io-vector:    #EC6FE0;   /* magenta (embedding/vector) */
  --io-control:   #6E7884;   /* neutral */
  --io-any:       #C0C7D0;   /* light gray */
}
```
> Note: replace the stray `--accent-hi` line; the real accent ramp is `--accent-dim / --accent / --accent-bright`.

### 3.2 Color — Light (config-heavy screens, optional)
```css
:root[data-theme="light"] {
  --bg-0:#F6F7F9; --bg-1:#FFFFFF; --bg-2:#FFFFFF; --bg-3:#F0F2F5; --bg-hover:#E9ECF1;
  --line:#E2E6EC; --line-strong:#CDD4DD;
  --fg-0:#11161C; --fg-1:#46505C; --fg-2:#7A848F; --fg-on-accent:#FFFFFF;
  --accent:#E8541F; --accent-bright:#FF6A3D; --accent-dim:#B8420F; --accent-glow:rgba(232,84,31,0.14);
  --signal:#0E9C90; --signal-glow:rgba(14,156,144,0.12);
  --ok:#1F9D57; --warn:#B9821C; --err:#D23A34; --info:#2F6FD0;
  /* io-* same hues, slightly darkened for contrast on light */
}
```

### 3.3 Typography
Use distinctive, characterful fonts — **not** Inter/Roboto/Arial. Provide fallbacks so Claude Design can substitute if a face is unavailable.
```css
--font-display: "Hubot Sans", "Schibsted Grotesk", system-ui, sans-serif; /* headings, nav, node titles */
--font-ui:      "Hanken Grotesk", "Hubot Sans", system-ui, sans-serif;     /* body, labels, buttons */
--font-mono:    "Commit Mono", "IBM Plex Mono", ui-monospace, monospace;   /* IDs, tokens, JSON paths, counts, code */
```
Scale (UI is compact — this is a tool, not a landing page):
```
display-lg 28/34 -0.02em   display 22/28 -0.01em   h1 18/24   h2 15/20   h3 13/18
body 14/20   body-sm 13/18   caption 12/16   micro 11/14 (uppercase, +0.06em tracking, --fg-2)
mono-sm 12.5/18   mono 13/20
```
Headings and node titles use `--font-display` at medium/semibold. Everything technical (IDs, token counts, `data.totals.subtotal`, model strings) uses `--font-mono`.

### 3.4 Spacing, radius, elevation, motion
```css
/* 4px base scale */ --s1:4px --s2:8px --s3:12px --s4:16px --s5:20px --s6:24px --s8:32px --s10:40px --s12:48px
/* radius — tight & technical, not pill */ --r-xs:4px --r-sm:6px --r-md:8px --r-lg:10px --r-xl:14px  (pill only for status dots/toggles)
/* elevation */
--sh-1: 0 1px 0 rgba(0,0,0,.4), 0 1px 2px rgba(0,0,0,.3);
--sh-2: 0 4px 16px rgba(0,0,0,.45);
--sh-pop: 0 12px 40px rgba(0,0,0,.55);
--glow-accent: 0 0 0 1px var(--accent), 0 0 18px var(--accent-glow);
--glow-signal: 0 0 0 1px var(--signal), 0 0 16px var(--signal-glow);
/* motion */ --ease: cubic-bezier(.2,.8,.2,1); --dur-fast:120ms --dur:180ms --dur-slow:260ms
/* canvas pan/zoom uses spring; reveals use staggered animation-delay */
```
Borders are hairline (`1px solid var(--line)`). Active/selected = swap to `--glow-accent` or `--glow-signal`. Reduce motion when `prefers-reduced-motion`.

---

## 4. Information architecture & navigation

```
Workspace (tenant)
├─ Dashboard            (all projects, recent runs, usage)
├─ Project
│   ├─ Overview          (health, quick actions, recent activity)
│   ├─ Workflows         (list → Canvas editor)        ◄ primary surface
│   ├─ Agents            (reusable agent presets)
│   ├─ Tools             (REST/GraphQL/code/MCP/builtin)
│   ├─ Auth Providers
│   ├─ Knowledge         (sources, chunks, Q&A pairs)
│   ├─ Playground        (test any workflow: graph + chat)
│   ├─ Traces            (runs, spans, cost)
│   ├─ Widget            (configure + preview + embed code)
│   ├─ Connect (MCP)     (expose as MCP server, API keys)
│   └─ Settings          (project config, budgets, secrets, members)
└─ Account / Workspace settings (billing, members, API keys, security)
```
Two-level nav: a slim **global rail** (workspace switch, dashboard, search, help, the Build Assistant, avatar) + a **project sidebar** (the list above) that appears inside a project. The Build Assistant is reachable from everywhere (rail icon + `⌘K` → "Ask the assistant").

---

## 5. App shell / global layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ▌Forge   [project ▾]                    ⌘K search       ◐ theme   ◇ help  ●│  topbar 52px, --bg-1, hairline bottom
├──┬───────────────────────────────────────────────────────────────────────┤
│  │                                                                         │
│R │  Project sidebar (240px, --bg-1)        Main content (--bg-0)           │
│a │  • Overview                                                             │
│i │  • Workflows                                                            │
│l │  • Agents                                                               │
│  │  • Tools            ...                                                 │
│56│                                                                         │
│px│                                                                         │
└──┴───────────────────────────────────────────────────────────────────────┘
```
- **Global rail (56px):** workspace logo/switcher, Dashboard, Search (`⌘K`), Assistant (✦), Help, spacer, Avatar/menu. Icons only, tooltip on hover, active = molten left-bar + glow.
- **Topbar (52px):** breadcrumb (`Project / Workflows / Support Router`), centered command/search, theme toggle, help, notifications, presence.
- **Project sidebar (240px, collapsible to icons):** grouped nav; each item shows a tiny count chip where relevant (Tools 12, Auth 3). Bottom: project status (active/draft) + token usage sparkline.
- **Density:** compact. Row height 36px in lists, 32px in dense tables.

---

## 6. Screen specifications

### 6.1 Auth & onboarding
- **Purpose:** sign in / sign up; first-run sets up the first project.
- **Layout:** centered card on `--bg-0` with a subtle forge-ember background (faint radial `--accent-glow` bottom, fine grid). Card `--bg-2`, `--r-xl`, `--sh-2`.
- **Onboarding wizard (first run):** 3 steps — name workspace → create first project (template chooser: "Blank", "Support agent", "Data Q&A", "Multi-agent") → optional "add your first model key." Big primary buttons in molten accent. Progress as a thin segmented bar.
- **States:** validation inline; SSO buttons if configured; loading = button spinner + disabled.

### 6.2 Dashboard (projects)
- **Purpose:** overview of all projects + workspace health.
- **Layout:** header "Projects" + `+ New project`. Grid of **project cards** (3-up desktop). Above the grid: 3 KPI tiles (Runs today, Tokens this month w/ sparkline, Spend this month). Below: "Recent runs" table (project, workflow, status pill, duration, tokens, time).
- **Project card:** name (display font), slug (mono, `--fg-2`), status dot, mini stats (workflows, tools, runs 7d sparkline), last-edited. Hover = lift + hairline → `--accent` edge. Click → Project Overview.
- **Empty state:** ember illustration + "Forge your first project" + template chooser + "Ask the assistant to build one."

### 6.3 Project Overview
- **Purpose:** project home; fast paths into the work.
- **Layout:** title + status; **Quick actions** row (Open canvas, Add tool, Configure widget, Open playground). **Health strip:** active workflow, # enabled tools, knowledge size, last run status, budget remaining (progress bar; molten when low). **Recent activity** feed (edits, runs, ingests). **Pinned workflows** cards.

### 6.4 Tools
- **List:** table — name (mono), kind badge (REST/GraphQL/Code/MCP/Builtin with distinct icon+tint), auth provider chip, enabled toggle, last-tested status, version. Filter by kind/auth. `+ New tool`.
- **Tool builder (the important one):** a focused editor, **split 60/40** — left = config, right = **Test panel** (sticky).
  - **Left, sectioned (collapsible):**
    1. **Identity** — name (mono input), description (this is what the model sees — show a "model's view" hint), kind selector.
    2. **Request** — method + URL template with `{param}` highlighting; **Fields table**: each row = `path`, `type`, `in` (path/query/header/body), `required`, `llm_visible` toggle, `description`, `default`. Add/reorder rows. A header sub-table.
    3. **Response shaping (token control)** — two tabs:
       - *Field map*: a tree of the sample response (from a test call) where each leaf has a `include_in_llm` checkbox + an editable description. Excluded leaves dim.
       - *Projection*: a JMESPath input with live preview + a **token meter** comparing **Raw vs Projected** ("Raw ≈ 1,240 tok → Projected ≈ 90 tok", molten bar shrinking). This visual is the product's signature token-cost moment — make it satisfying.
    4. **Auth** — pick an Auth Provider (or "None"); inline "Test auth."
    5. **Reliability** — rate limit, timeout, retry, cache TTL.
  - **Right, Test panel:** an args form generated from the LLM-visible fields + a context editor (paste a CSRF/session for testing) → "Run test" → tabs: **Raw response**, **Projected (model sees)**, **Headers**, **Timing**, **Token delta**. Errors render with status + body.
- **States:** untested (neutral), pass (ok), fail (err with detail); unsaved-changes bar.

### 6.5 Auth Providers
- **List:** name, kind badge (CSRF+Session / OAuth2 / Bearer / Basic / API key / Custom), last-tested, used-by count.
- **Editor (split with Test panel):**
  - **Kind** selector switches the form. For **CSRF+Session**: Token-fetch (method/URL/headers/body with `{{cred.*}}` and `{{ctx.*}}` token chips), **Extract** rows (from header/cookie/json-path, name, mark TTL), **Inject** rows (to header/cookie/query, name, value with `{{extracted.*}}` chips), cache TTL, refresh-on codes.
  - **Credentials:** reference picker to a secret (write-only); never shows plaintext.
  - **Custom script:** a Monaco editor (RestrictedPython) with a red "advanced — audited" banner.
  - **Test panel:** runs the fetch, shows extracted values **masked** (`csrf ••••a91`, `session ••••…`), TTL, and the exact headers/cookies that *would* be injected.

### 6.6 Agents (presets) & the Agent config (also used inside the canvas inspector)
This config appears both as a standalone preset editor and as the **inspector** for an `agent`/`deep_agent` node. **It is the most important config surface** — it must make the middleware stack feel powerful and calm.
- **Layout:** vertical sections:
  1. **Flavor** — segmented `Agent` | `Deep Agent`. Choosing Deep Agent reveals harness sections (Planning, Subagents, Filesystem, Sandbox, Skills, Permissions).
  2. **Model** — model picker (searchable, grouped by provider, capability chips from `.profile`: tools ✓, vision ✓, ctx 200k). Temperature/max-tokens/timeout in an "Advanced" disclosure. A **Dynamic model** toggle reveals a rules builder (`when len(messages) > 10 → gpt-5.4`, default `gpt-5.4-mini`).
  3. **Instructions** — system prompt editor (Markdown, token counter). "Dynamic prompt" toggle → rule builder by context (role/locale). Anthropic prompt-caching toggle when model = Claude.
  4. **Tools** — multi-select chips of the project's tools + MCP tools; reorder; per-tool inline "limit" shortcut. "LLM tool selector" toggle when >8 tools.
  5. **Output** — Freeform | Structured. Structured opens a schema builder (fields + types) → compiles to `response_format`.
  6. **Middleware stack (signature UI)** — a **vertical stack of cards**, each a middleware (Summarization, HITL, Tool/Model limits, PII, Retry, Context editing, Fallback, Moderation, Budget, Guardrail, …). `+ Add middleware` opens a categorized catalog (Memory & Context / Safety & Guardrails / Reliability / Cost & Limits / Human oversight / Provider-specific / Advanced). Each card: icon, name, one-line summary of its effect ("Summarize when > 4,000 tokens, keep last 20 msgs"), enabled toggle, drag handle (order = execution order, shown as an "onion" hint), expand → its schema-driven form. Cards are color-keyed by category. **This stack is how the user expresses "every customization."**
  7. **Memory** — long-term memory toggle (store namespace), short-term state extensions (custom fields + reducer).
  8. **Deep-Agent-only:** Planning (to-do on/off), **Subagents** (cards: name, description, prompt, tools, model — or "use a workflow as a subagent" picker), Filesystem backend (State / Disk / Store / Composite with `/memories/` route), **Sandbox** (None / Docker / Remote provider), Skills, Permissions (read/write path rules).
- **Right rail (optional):** a live "what the model sees" preview — assembled system prompt + tool list + active middleware order — so users understand the compiled agent.

### 6.7 Workflow canvas (the hero) — see §7 for the node language
- **Purpose:** build the graph visually; compile to executable on save.
- **Layout:** full-bleed canvas (`--bg-0` with dotted grid) + floating UI:
  - **Left: Node palette** (collapsible) — search + categorized node types (generated from the registry): Flow (start/end/router/loop/fanout/join), Agents (agent/deep_agent), Model & Tools (llm/tool_call/transform/code), Knowledge (retrieval/qa_lookup), Human (human_input), Integrations (webhook_out/subworkflow/emit_event). Drag onto canvas or click to drop at center.
  - **Right: Inspector** — config for the selected node (uses §8 schema forms; for agent nodes, §6.6). Empty selection → workflow-level config (state schema editor, global middleware, error policy, timeout) + validation summary.
  - **Top-center toolbar:** workflow name (inline edit), Save state (auto-saved dot / saving / error), **Validate**, **Run** (opens playground docked), Version menu, Undo/Redo, zoom %, "Tidy" (dagre auto-layout).
  - **Bottom-right:** MiniMap + zoom controls (React Flow), in console-glass styling.
- **Edges (cables):** bezier, 2px, colored by source port `IOType`, with a faint moving dash when a run is live and that edge is active. Invalid drag target → edge turns `--err` and snaps back. Hover edge → delete affordance.
- **Validation:** invalid nodes get an `--err` hairline + a badge with count; clicking jumps the inspector to the offending field. A bottom "Problems" tray lists all issues.
- **Live run overlay:** during a playground run, nodes light up in execution order (active node = `--glow-accent` + subtle pulse), token/latency counters tick on each node, the active edge animates. This makes the abstract concrete.
- **Interactions:** marquee select, multi-drag, copy/paste nodes, `⌘+drag` to duplicate, `Del` to remove (confirm if connected), `⌘Z/⇧⌘Z`, space-drag to pan, scroll to zoom, `F` to fit.

### 6.8 Knowledge
- **Sources tab:** table — name, kind (file/url/s3/text/api), status (queued/processing/ready/error with progress), chunks, size, embedding model, updated. `+ Add source` (upload / URL / paste / S3). Row → drawer with chunk preview + re-ingest.
- **Q&A pairs tab:** table — question (truncated), answer (truncated), kind (FAQ / Error→Workaround badge), tags, upvotes, last used. Inline add/edit (two textareas + tags + kind). Import CSV.
- **Search debugger:** a query box → ranked chunks with scores (vector + FTS + fused), to tune top_k/hybrid.
- **Empty:** "Feed your agents knowledge" + add-source CTA.

### 6.9 Playground (test)
Two modes, toggle top-right; both stream over SSE.
- **Chat mode (business view):** clean chat — message bubbles, streaming tokens, tool-call chips ("Called get_quote → 90 tok"), inline approval cards when an `interrupt` arrives (Approve / Edit / Reject), suggested prompts. Context editor (set user_external_id, locale, paste CSRF/session for auth testing). A "dry-run with emulated tools" toggle (uses `LLMToolEmulator`).
- **Graph mode (developer view):** the canvas (read-only) with the **live run overlay** (§6.7) on the left + a **step inspector** on the right showing the current node's state, inputs/outputs, the exact model messages, tool I/O, and token/cost per step. A timeline scrubber to step through supersteps (time-travel feel).
- **Shared:** run controls (Run / Stop / Resume), thread selector (resume past threads), token + cost meter for the run.

### 6.10 Traces
- **Runs list:** table — workflow, status pill, started, duration, tokens, cost, trigger (playground/widget/MCP/API). Filters: status, workflow, time range, cost range. Click → trace detail.
- **Trace detail:** a **span waterfall** (left: tree of spans by kind with color-keyed bars; right: timeline with durations). Selecting a span shows input/output JSON (collapsible), model, token in/out, cost, errors. Header: totals + a "replay in playground" button. Errored spans flagged `--err`.
- **Make cost legible:** per-span cost and a roll-up; a small donut of cost-by-node.

### 6.11 Widget configurator
- **Layout:** split — **left config**, **right live preview** (an actual embedded widget instance against a mock host page).
  - **Appearance:** theme tokens (primary `--w-primary` color picker, surface, radius, font), launcher (icon, position, label), greeting, suggested prompts, avatar/logo, light/dark/auto.
  - **Behavior:** which workflow drives it, streaming on, show tool activity?, allow file upload?, rate limit.
  - **Host variables (advanced):** rows mapping host-page values → run context: source (meta tag / cookie / JS expression), name, selector/expression. A red "evaluates JS on the host page" warning + audit note. This is how CSRF/session reach the backend.
  - **Security:** allowed origins list; identity verification (HMAC) toggle + key.
  - **Embed:** copy-paste `<script>` snippet + npm option; "Test on a URL" sandbox.
- **Preview** updates live as tokens change.

### 6.12 Connect (Project as MCP)
- Toggle "Expose this project as an MCP server." Show the **MCP URL** (`/mcp/v1/<project>`), transport, and a generated **project API key** (copy, rotate, revoke). A checklist of which tools/workflows are exposed. Copy-paste configs for Claude Desktop / Cursor / VS Code. A "Test connection" button.

### 6.13 Settings & Secrets
- **Project config:** default model, allowed models, budgets (tokens/$/run with alerts), default middleware (same stack UI), data region, feature flags (code nodes, remote sandbox), tracing retention.
- **Secrets:** table — name, kind, version, last used; `+ Add secret` (write-only; values never shown, masked everywhere). Reference scheme hint (`secret://proj/<name>`).
- **Members & roles**, **API keys**, **Audit log** (searchable table of secret reads, publishes, destructive ops).

---

## 7. Node design language (core system)

Nodes are **chips on a circuit board.** Consistency here makes the whole canvas legible.

### 7.1 Anatomy
```
        ●in_messages (teal handle, left)
   ┌───────────────────────────────────────┐
   │ ◈  Billing Agent            agent  ⋯   │ header: --bg-3, icon + title(display) + type chip + menu
   ├───────────────────────────────────────┤
   │ claude-sonnet-4-6                       │ body: --bg-2, key config summary (mono for model)
   │ 2 tools · 3 middleware                  │ secondary line --fg-2
   │ ▸ summarization ▸ tool-limit ▸ HITL     │ middleware mini-pills (color-keyed)
   └───────────────────────────────────────┘
        ●out_messages (teal handle, right)
```
- **Width:** 240px default; grows with content to max 320px. **Radius:** `--r-lg`. **Border:** hairline `--line`; selected = `--glow-accent`.
- **Header tint by category** (left 3px bar + icon color): Flow = `--io-control`, Agents = `--accent`, Model/Tools = `--io-json`, Knowledge = `--io-vector`, Human = `--warn`, Integrations = `--signal`.
- **Type chip:** mono micro, uppercase, in header right.
- **Body:** shows the 2–3 most important config values as a glanceable summary (model, tool count, middleware order). Never show raw JSON on the node.

### 7.2 Handles (ports) & typing
- Circular handles, 10px, filled with the **IOType color** (§3.1). Multiple ports stack vertically with tiny labels on hover.
- A connection is valid only when source/target `IOType` are compatible (`any` matches all; `control` only to `control`). Invalid attempt → target handles dim, dragged cable turns `--err`.
- Handle hover → enlarge + glow in its type color; show the port name tooltip.

### 7.3 States
- **Default / Hover (lift + brighter border) / Selected (`--glow-accent`) / Invalid (`--err` border + badge) / Disabled (50% + diagonal hatch) / Running (`--glow-accent` + soft pulse, live token/latency counter chip) / Done (brief `--ok` ring) / Errored (`--err` ring + ! badge).**
- **Interrupt/HITL node** paused → `--warn` ring + "Waiting for approval" chip.

### 7.4 Per-type specifics
- **agent/deep_agent:** as above; deep_agent shows a small "subagents: 2" + planning indicator.
- **router:** diamond-ish header accent; body lists cases → targets; one control-out handle per case (labeled).
- **tool_call:** shows the tool name (mono) + a tiny kind icon.
- **retrieval/qa_lookup:** magenta accent; shows source filter + top_k.
- **human_input:** warn accent; shows the prompt preview + decisions.
- **code:** json accent; shows language + a `</>` glyph + sandbox tier badge.
- **parallel_fanout/join:** show the "over" key / reducer; multiple control handles rendered as a fan.
- **subworkflow:** double-border to signal "contains a graph"; shows the referenced workflow name + version.

---

## 8. Config-form pattern (schema-driven)

Every node inspector, tool form, auth form, and settings panel is **generated from a JSON Schema** (the same schema the backend validates against). Implement a `<SchemaForm schema={...} value={...} onChange={...} />` with these field renderers:

| Schema type / hint | Renderer |
|---|---|
| string | text input (mono if `format: id|path|token`) |
| string + enum | segmented (≤3) or select |
| number/integer | stepper with unit suffix |
| boolean | toggle (pill) with inline effect summary |
| string + `format: code` | Monaco mini-editor |
| array of objects | editable, reorderable **rows table** (Fields, Extract/Inject, Middleware) |
| `format: model` | model picker w/ capability chips |
| `format: tool-ref` / `secret-ref` / `workflow-ref` | searchable reference picker |
| `format: jmespath` | input + live preview + token meter |
| `oneOf` (by `kind`) | a "kind" selector that swaps the sub-form |

Conventions: collapsible sections with summaries; **inline effect text** under controls ("Summarize when > 4,000 tokens"); token chips (`{{cred.*}}`, `{{ctx.*}}`, `{{extracted.*}}`) as styled, insertable pills in template fields; validation inline + an unsaved-changes bar; "model's view" hints wherever the LLM sees the value (tool/agent descriptions). Forms are dense but grouped — never a flat wall.

---

## 9. Embeddable chat widget (design + theming)

The widget is a separate, tiny, fast surface; it must look clean on *any* host site and be fully themeable.
- **Launcher:** floating bubble (configurable icon/position/label), `--w-primary` fill, subtle shadow, gentle hover scale.
- **Panel:** 384px wide, rounded `--r-xl`, `--sh-pop`; header (logo, title, minimize), message list, composer (input + send + optional file). Dark/light/auto per config.
- **Messages:** user (right, `--w-primary` tint), agent (left, surface). Streaming = token-by-token with a soft caret. Tool activity shown as a slim, collapsible chip ("Looking up your quote…"). Markdown + code blocks supported. Citations as small numbered chips linking sources.
- **HITL / elicitation:** inline card with the request + Approve / Edit / Reject (or a small form when the server elicits structured input).
- **Theme tokens (host-overridable):** `--w-primary, --w-bg, --w-surface, --w-fg, --w-radius, --w-font, --w-launcher-size`.
- **Trust:** never render host-page secrets; respects reduced motion; keyboard accessible; "Powered by Forge" (toggle on paid).

---

## 10. States: empty, loading, error

- **Empty states** are opportunities: a small ember/circuit illustration, a one-line value statement, a primary CTA, and an "Ask the assistant" secondary. (Projects, Tools, Workflows, Knowledge, Traces all need one.)
- **Loading:** skeletons that match final layout (rows, cards, the span waterfall); never spinners-on-blank for lists. Inline buttons → spinner + disabled. The canvas shows a faint "compiling…" shimmer on save.
- **Errors:** human, specific, recoverable. Inline field errors; a toast for transient failures with a Retry; full-page error only for hard failures (with "copy details" + retry). Tool/auth/run errors show the actual status + body in a `--err`-tinted code block.

---

## 11. Responsive & accessibility

- **Primary target: desktop (≥1280px).** The canvas is desktop-first.
- **≤1024px:** sidebar collapses to icons; inspector becomes a slide-over; canvas usable but palette/inspector are drawers.
- **≤768px (tablet/phone):** read/monitor only — view traces, chat in playground, toggle widget config; **canvas editing is gated** with a "best on a larger screen" notice. The embedded *widget itself* is fully responsive.
- **A11y:** WCAG 2.1 AA contrast (the dark tokens are tuned for it); full keyboard nav incl. canvas (arrow-move nodes, Tab through handles, `Enter` to open inspector); visible focus rings (`--glow-signal`); ARIA on all controls; `prefers-reduced-motion` disables pulses/dash animations; never color-only signaling (pair color with icon/label, important for the IOType system and status pills).

---

## 12. Build Assistant UI (the meta-agent)

A right-docked panel (or `⌘K` → "Ask the assistant"), available everywhere.
- **Panel:** chat with the assistant; it streams its plan as a **to-do checklist** (planning middleware) that ticks off as it works ("✓ Created auth provider ✓ Registered get_quote tool ◷ Wiring router…").
- **Live canvas mutation:** when on a workflow, the assistant's actions appear as **ghosted nodes/edges** materializing on the canvas in real time; the user can accept/adjust. Side-effectful steps (sending a test request, publishing, creating a secret) require an inline **confirm** card.
- **Diff preview:** before applying a multi-step build, show a compact summary ("will create 1 auth provider, 1 tool, 1 agent node, 3 middleware") with Apply / Modify.
- **Tone:** concise, technical, shows its reasoning as steps, asks one question at a time when it needs a decision (e.g., "Which field is the CSRF token in the login response?").

---

## 13. Component inventory (build these to the tokens)
Buttons (primary molten / secondary outline / ghost / danger), icon buttons, segmented control, toggle, select, combobox/reference-picker, model-picker, multi-select chips, inputs (text/number/mono/template-with-token-chips), Monaco mini-editor, rows-table (add/reorder/delete), tabs, accordion/disclosure, cards, KPI tiles, status pill, type chip, badge, tooltip, popover, dropdown menu, modal/dialog, slide-over drawer, toast, skeletons, progress bar, token meter, sparkline, span-waterfall, donut/bar mini-charts, code/JSON viewer (collapsible), the React-Flow node + handle + edge components, the chat surface (bubbles, streaming, tool chips, approval cards), empty-state, command palette (`⌘K`).

---

## 14. Iconography & illustration
- **Icons:** a single consistent line set (e.g., Lucide), 1.5px stroke, sized 16/18/20. Each node type and tool kind gets a stable icon. Avoid filled/duotone mixing.
- **Illustration:** sparse, abstract, on-brand — embers, circuit traces, patch cables, nodes. Used only in empty states and onboarding. No stock-art people, no 3D blobs, no purple gradients.

---

## 15. Voice & content
- **Microcopy:** precise and calm. Labels are nouns ("Auth Providers"), buttons are verbs ("Run test", "Add middleware"). Numbers shown with units (`4,000 tok`, `1.8s`, `$0.012`).
- **Helper text** explains *effect*, not mechanism ("Summarize older messages when the conversation grows past this size") and never references internal implementation.
- **Errors** name the thing and the fix. **Success** is quiet (a checkmark, a toast), not celebratory.

---

## 16. The one thing to remember
The product's signature is **making extreme configurability feel calm.** Two moments carry the brand: (1) the **canvas lighting up during a live run** (abstract → tangible), and (2) the **token meter shrinking** as the user shapes a tool's response projection (the user's cost obsession, made visceral). Get those two right, keep everything else precise and quiet, and Forge will feel like the professional instrument it is.
