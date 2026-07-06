/* Forge mock data - generic, plausible SaaS content. Ported from the design handoff.
   Screens not yet wired to the live API render from this; the Dashboard merges live
   projects from the backend when available (see lib/api.ts). */

export const spark = (n: number, base: number, amp: number): number[] =>
  Array.from({ length: n }, (_, i) =>
    Math.max(0, Math.round(base + Math.sin(i * 0.8) * amp + (Math.random() - 0.5) * amp * 0.8)),
  );

/* Format a USD cost. Cheap models cost fractions of a cent per run, so a flat
   $0.00 reads as "broken" even when tracking works - show small amounts with
   enough precision (down to a sub-cent floor) so non-zero cost is visible. */
export const fmtUSD = (v: number | null | undefined): string => {
  const n = v || 0;
  if (n <= 0) return "$0.00";
  if (n >= 0.01) return `$${n.toFixed(2)}`;
  if (n >= 0.0001) return `$${n.toFixed(4)}`;
  return "<$0.0001";
};

// Real, currently-available models (the design docs used fictional 2026 names).
export const MODELS = [
  { id: "openai:gpt-4o-mini", name: "gpt-4o-mini", provider: "OpenAI", ctx: "128k", tools: true, vision: true },
  { id: "openai:gpt-4o", name: "gpt-4o", provider: "OpenAI", ctx: "128k", tools: true, vision: true },
  { id: "openai:gpt-4.1-mini", name: "gpt-4.1-mini", provider: "OpenAI", ctx: "1M", tools: true, vision: true },
  { id: "anthropic:claude-3-5-sonnet-latest", name: "claude-3-5-sonnet", provider: "Anthropic", ctx: "200k", tools: true, vision: true },
  { id: "anthropic:claude-3-5-haiku-latest", name: "claude-3-5-haiku", provider: "Anthropic", ctx: "200k", tools: true, vision: false },
  { id: "google_genai:gemini-1.5-flash", name: "gemini-1.5-flash", provider: "Google", ctx: "1M", tools: true, vision: true },
  { id: "fake:echo", name: "fake (offline test)", provider: "Local", ctx: "-", tools: true, vision: false },
];

export const NODE_CATALOG = [
  { group: "Flow", color: "var(--io-control)", items: [
    { type: "start", icon: "n_start", label: "Start", desc: "Entry marker" },
    { type: "end", icon: "n_end", label: "End", desc: "Terminal node" },
    { type: "router", icon: "n_router", label: "Router", desc: "Conditional branch" },
    { type: "loop", icon: "n_loop", label: "Loop", desc: "Bounded iteration" },
    { type: "parallel_fanout", icon: "n_fanout", label: "Fan-out", desc: "Map over a list" },
    { type: "join", icon: "n_join", label: "Join", desc: "Wait-for-all / reduce" },
  ]},
  { group: "Agents", color: "var(--accent)", items: [
    { type: "agent", icon: "n_agent", label: "Agent", desc: "ReAct tool loop" },
    { type: "deep_agent", icon: "n_deepagent", label: "Deep Agent", desc: "Planning + subagents harness" },
  ]},
  { group: "Model & Tools", color: "var(--io-json)", items: [
    { type: "llm", icon: "n_llm", label: "LLM", desc: "Single model call" },
    { type: "classifier", icon: "n_router", label: "Classifier", desc: "Intent classification" },
    { type: "tool_call", icon: "n_tool", label: "Tool Call", desc: "Run a specific tool" },
    { type: "transform", icon: "n_transform", label: "Transform", desc: "JMESPath data map" },
    { type: "code", icon: "n_code", label: "Code", desc: "Sandboxed transform" },
  ]},
  { group: "Knowledge", color: "var(--io-vector)", items: [
    { type: "retrieval", icon: "n_retrieval", label: "Retrieval", desc: "RAG + Q&A" },
  ]},
  { group: "Human", color: "var(--warn)", items: [
    { type: "human_input", icon: "n_human", label: "Human Input", desc: "HITL pause via interrupt" },
  ]},
  { group: "Integrations", color: "var(--signal)", items: [
    { type: "subworkflow", icon: "n_subworkflow", label: "Subworkflow", desc: "Embed another graph" },
    { type: "webhook_out", icon: "n_webhook", label: "Webhook", desc: "Call external URL" },
    { type: "emit_event", icon: "n_emit", label: "Emit Event", desc: "Push custom SSE frame" },
  ]},
];

export const NODE_META: Record<string, any> = {};
NODE_CATALOG.forEach((g) => g.items.forEach((it) => (NODE_META[it.type] = { ...it, group: g.group, color: g.color })));

/* Hover help for the canvas palette: what each node is for + a tiny concrete example.
   Keep these in plain product language - they're the first thing a new user reads. */
export const NODE_HELP: Record<string, { what: string; example: string }> = {
  start: {
    what: "The entry point. Every run begins here - wire it to your first real step.",
    example: "Start → Retrieval → Agent → End",
  },
  end: {
    what: "Marks where the run finishes. A workflow can have several Ends (one per branch).",
    example: "FAQ hit → End (answered early), miss → Agent → End",
  },
  router: {
    what: "Branches the flow based on a value already in state - no model call. One labeled connector per case, plus Else. With 'multi' on, a list value (multi-label Classifier) runs EVERY matching branch in parallel. Always set a Default - without one, an unmatched value ends the run silently.",
    example: "intent = 'refund' → refund_agent · 'cancel' → retention_agent · Else → general_agent",
  },
  classifier: {
    what: "Calls the model once (structured output) to pick a label from your list and writes it to state (default: intent). Multi-label mode writes EVERY applicable label (a list) - pair with a multi Router so two-part questions reach both specialists. Put a Router after it to branch.",
    example: "labels: return_item, cancel, question - “I want my money back” → return_item",
  },
  agent: {
    what: "A model with tools and a system prompt that loops reason → act until it can answer (ReAct). The workhorse node for answering users.",
    example: "Support agent with a weather tool + knowledge-base grounding",
  },
  deep_agent: {
    what: "An agent plus planning (write_todos), a virtual filesystem, and subagents - for long, multi-step tasks that need decomposition.",
    example: "“Research our top 3 competitors and draft a comparison”",
  },
  llm: {
    what: "One single model call - no tools, no loop. A cheap text step for rewriting, summarizing, or extracting.",
    example: "“Summarize the conversation so far into 2 sentences”",
  },
  transform: {
    what: "Reshapes state with a JMESPath expression - pure data, no model. Reads input_key, writes output_key.",
    example: "messages[-1].content → question",
  },
  tool_call: {
    what: "Invokes one specific project tool directly with fixed arguments - no model deciding whether to call it. Result lands in a state key.",
    example: "Always fetch get_weather before the agent answers",
  },
  human_input: {
    what: "PAUSES the run with a real interrupt until a person approves or rejects in the Playground. Use for irreversible or sensitive steps.",
    example: "Agent drafts a refund email → human approves → it goes out",
  },
  webhook_out: {
    what: "Sends data from the run to an external URL (POST/PUT/…) - push results into your own systems.",
    example: "POST the final answer to your Slack webhook",
  },
  emit_event: {
    what: "Emits a named custom event into the run's live stream - for UI badges, metrics, or integrations listening to the run.",
    example: "Emit 'escalated' when the agent hands off to a human",
  },
  retrieval: {
    what: "Pulls the most relevant knowledge into context for the user's question - place it right before a grounded agent. Toggle DOCUMENTS (RAG over your chunks) and Q&A PAIRS independently: use either or both. Tip: for multi-part questions, give the agent a knowledge_search TOOL instead, so it can search per sub-question.",
    example: "KB says “returns within 30 days” → agent answers with that policy",
  },
  // --- flow ---
  loop: {
    what: "Repeats a section of the graph until a condition is false or a max-iteration cap is hit. It increments _loop_count and writes _loop = continue/done - wire a Router on _loop and point the 'continue' branch back to this node.",
    example: "Refine a draft up to 3 times: loop → agent → loop (until good enough)",
  },
  parallel_fanout: {
    what: "Maps over a list in state: runs a child node ONCE PER ITEM, all in parallel (LangGraph Send). Each child reads its item from the chosen state key. Children write to an add-reducer key so results aggregate.",
    example: "over: tickets → run a summarizer per ticket, in parallel",
  },
  join: {
    what: "A convergence point where parallel branches (e.g. a Parallel Fanout's children) meet before the flow continues. Results aggregate via an add-reducer state key.",
    example: "Fanout → (summarize each) → Join → final agent composes the digest",
  },
  subworkflow: {
    what: "Runs ANOTHER workflow in this project as a reusable component (shares the messages state). Build a flow once - 'verify identity', 'look up order' - and drop it into many workflows.",
    example: "Support flow → Subworkflow: 'verify_identity' → continue",
  },
  handoff: {
    what: "Escalates the conversation to a HUMAN: pauses the run and opens a ticket in the Agent inbox. When an agent replies there, their message becomes the assistant's answer and is delivered over the channel.",
    example: "Agent can't resolve → Handoff → a person replies from the inbox",
  },
  // --- triggers (entry points) ---
  webhook_in: {
    what: "Starts the workflow when an external system POSTs to this workflow's hook URL (shown on the Triggers screen after publish). Optionally verify an HMAC signature. Map the JSON body to the message with a JMESPath.",
    example: "Your app POSTs {text: '…'} → the workflow runs and replies",
  },
  schedule: {
    what: "Runs the workflow on a recurring schedule - every N minutes or a cron expression. Sends a fixed message into the flow each time.",
    example: "Every weekday 9am → 'Summarize overnight tickets'",
  },
  email_in: {
    what: "Starts the workflow when an email arrives in the connected mailbox (configure the Email channel under Connect → Channels). Optionally replies to the sender with the answer.",
    example: "support@yourco.com receives a question → agent replies by email",
  },
  chat_in: {
    what: "Starts the workflow from a chat surface - Microsoft Teams (configured under Channels). The user's message flows straight into the graph.",
    example: "A teammate messages the Teams bot → grounded agent answers",
  },
  app_event: {
    what: "Polls an external source on an interval and runs the workflow once PER NEW item (deduped by a key you choose). Turns any API/feed into an event source.",
    example: "Poll the issues API every 5 min → triage each new issue",
  },
};

export const CAT_BY_TYPE: Record<string, string> = {
  start: "control", end: "control", router: "control", loop: "control", parallel_fanout: "control", join: "control",
  agent: "agent", deep_agent: "agent",
  llm: "json", tool_call: "json", transform: "json", code: "json",
  retrieval: "vector",
  human_input: "human",
  subworkflow: "signal", webhook_out: "signal", emit_event: "signal",
};

export const workflowNodes = [
  { id: "start", type: "start", position: { x: 40, y: 300 }, data: {}, summary: [] as string[] },
  { id: "faq_deflect", type: "retrieval", position: { x: 220, y: 286 }, data: { top_k: 5, include_qa: true }, title: "Knowledge", summary: ["docs top_k 5", "+ Q&A"] },
  { id: "intent_router", type: "router", position: { x: 430, y: 280 }, data: {}, title: "Intent Router", summary: ["expression · state.intent", "billing · technical · default"], cases: ["billing", "technical", "default"] },
  { id: "kb_search", type: "retrieval", position: { x: 700, y: 110 }, data: {}, title: "Help Docs", summary: ["3 sources · top_k 5", "hybrid + rerank"] },
  { id: "billing_agent", type: "agent", position: { x: 690, y: 270 }, data: {}, title: "Billing Agent", summary: ["claude-sonnet-4-6", "2 tools · 4 middleware"], mw: ["summarization", "tool_call_limit", "human_in_the_loop", "pii"] },
  { id: "tech_agent", type: "deep_agent", position: { x: 690, y: 470 }, data: {}, title: "Tech Agent", summary: ["gpt-5.4 · deep agent", "subagents 2 · planning on"], mw: ["summarization", "context_editing"] },
  { id: "approve_refund", type: "human_input", position: { x: 980, y: 270 }, data: {}, title: "Approve Refund", summary: ['"Approve this refund?"', "approve · edit · reject"] },
  { id: "end", type: "end", position: { x: 1210, y: 320 }, data: {}, summary: [] as string[] },
];
export const workflowEdges = [
  { id: "e1", source: "start", target: "faq_deflect", io: "control" },
  { id: "e2", source: "faq_deflect", target: "intent_router", io: "messages" },
  { id: "e3", source: "intent_router", target: "billing_agent", io: "control", label: "billing" },
  { id: "e4", source: "intent_router", target: "tech_agent", io: "control", label: "technical" },
  { id: "e5", source: "kb_search", target: "billing_agent", io: "json" },
  { id: "e6", source: "billing_agent", target: "approve_refund", io: "messages" },
  { id: "e7", source: "approve_refund", target: "end", io: "messages" },
  { id: "e8", source: "tech_agent", target: "end", io: "messages" },
];
export const runOrder = ["start", "faq_deflect", "intent_router", "kb_search", "billing_agent", "approve_refund", "end"];

export const TOOLS = [
  { id: "t_get_order", name: "get_order", kind: "rest_api", auth: "orders_session", enabled: true, tested: "pass", version: 4, desc: "Fetch an order by ID from the commerce API, including line items and totals.", method: "GET", url: "https://api.acme.dev/v2/orders/{order_id}", rawTok: 1240, projTok: 92 },
  { id: "t_get_invoice", name: "get_invoice", kind: "rest_api", auth: "orders_session", enabled: true, tested: "pass", version: 2, desc: "Retrieve a customer invoice and its payment status.", method: "GET", url: "https://api.acme.dev/v2/invoices/{invoice_id}", rawTok: 880, projTok: 64 },
  { id: "t_search_kb", name: "search_catalog", kind: "graphql", auth: null, enabled: true, tested: "pass", version: 1, desc: "Query the product catalog via GraphQL.", method: "POST", url: "https://api.acme.dev/graphql", rawTok: 2100, projTok: 140 },
  { id: "t_refund", name: "submit_refund", kind: "rest_api", auth: "orders_session", enabled: true, tested: "fail", version: 3, desc: "Issue a refund against an order. Requires human approval.", method: "POST", url: "https://api.acme.dev/v2/orders/{order_id}/refunds", rawTok: 320, projTok: 48 },
  { id: "t_geo", name: "geocode_address", kind: "code", auth: null, enabled: true, tested: "untested", version: 1, desc: "Normalize and geocode a postal address using a sandboxed Python function.", rawTok: 0, projTok: 0 },
  { id: "t_jira", name: "create_ticket", kind: "mcp", auth: "jira_oauth", enabled: true, tested: "pass", version: 1, desc: "Create an issue in the project tracker over MCP.", rawTok: 540, projTok: 70 },
  { id: "t_web", name: "web_search", kind: "builtin", auth: null, enabled: false, tested: "untested", version: 1, desc: "Search the public web (Tavily).", rawTok: 0, projTok: 0 },
];

export const AUTH_PROVIDERS = [
  { id: "orders_session", name: "orders_session", kind: "csrf_session", tested: "pass", usedBy: 3, ttl: 1800 },
  { id: "jira_oauth", name: "jira_oauth", kind: "oauth2_client_credentials", tested: "pass", usedBy: 1, ttl: 3600 },
  { id: "stripe_bearer", name: "stripe_bearer", kind: "bearer", tested: "pass", usedBy: 2, ttl: 0 },
  { id: "legacy_basic", name: "legacy_basic", kind: "basic", tested: "untested", usedBy: 0, ttl: 0 },
];

export const AGENTS = [
  { id: "a_billing", name: "billing_agent", flavor: "agent", model: "anthropic:claude-sonnet-4-6", tools: 2, mw: 4, updated: "2h ago" },
  { id: "a_tech", name: "tech_agent", flavor: "deep_agent", model: "openai:gpt-5.4", tools: 5, mw: 3, updated: "1d ago" },
  { id: "a_triage", name: "triage_agent", flavor: "agent", model: "openai:gpt-5.4-mini", tools: 1, mw: 2, updated: "3d ago" },
  { id: "a_research", name: "research_agent", flavor: "deep_agent", model: "google_genai:gemini-3.1-pro-preview", tools: 3, mw: 5, updated: "5d ago" },
];

export const MIDDLEWARE_CATALOG = [
  { cat: "Memory & Context", color: "var(--signal)", items: [
    { type: "summarization", name: "Summarization", desc: "Summarize older messages near a token limit." },
    { type: "context_editing", name: "Context Editing", desc: "Clear old tool outputs past a threshold." },
    { type: "todo", name: "Planning (To-do)", desc: "Add a write_todos planning tool." },
  ]},
  { cat: "Safety & Guardrails", color: "var(--err)", items: [
    { type: "pii", name: "PII Handling", desc: "Detect & redact/mask/block PII." },
    { type: "guardrail_regex", name: "Regex Guardrail", desc: "Block or flag matched patterns." },
    { type: "openai_moderation", name: "Moderation", desc: "OpenAI moderation on input/output." },
  ]},
  { cat: "Reliability", color: "var(--info)", items: [
    { type: "tool_retry", name: "Tool Retry", desc: "Retry failed tool calls with backoff." },
    { type: "model_retry", name: "Model Retry", desc: "Retry failed model calls." },
    { type: "model_fallback", name: "Model Fallback", desc: "Failover across providers." },
  ]},
  { cat: "Cost & Limits", color: "var(--warn)", items: [
    { type: "model_call_limit", name: "Model Call Limit", desc: "Cap model calls per run/thread." },
    { type: "tool_call_limit", name: "Tool Call Limit", desc: "Cap tool calls, global or per-tool." },
    { type: "tenant_budget", name: "Budget Cap", desc: "Stop when cost/tokens exceed a cap." },
    { type: "llm_tool_selector", name: "Tool Selector", desc: "Pre-select relevant tools (saves tokens)." },
  ]},
  { cat: "Human Oversight", color: "var(--accent)", items: [
    { type: "human_in_the_loop", name: "Human-in-the-loop", desc: "Pause for approval on sensitive tools." },
  ]},
  { cat: "Provider-specific", color: "var(--io-json)", items: [
    { type: "anthropic_prompt_caching", name: "Prompt Caching", desc: "Cache the system prompt (Anthropic)." },
  ]},
  { cat: "Advanced", color: "var(--io-vector)", items: [
    { type: "dynamic_model_by_state", name: "Dynamic Model", desc: "Switch model at runtime by state." },
    { type: "tool_filter_by_context", name: "Tool Filter", desc: "Show/hide tools by context/role." },
  ]},
];
export const MW_META: Record<string, any> = {};
MIDDLEWARE_CATALOG.forEach((c) => c.items.forEach((it) => (MW_META[it.type] = { ...it, cat: c.cat, color: c.color })));

export const AGENT_MW_STACK = [
  { type: "summarization", enabled: true, summary: "Summarize when > 4,000 tok · keep last 20 msgs" },
  { type: "tool_call_limit", enabled: true, summary: "get_order · max 3 calls per run" },
  { type: "human_in_the_loop", enabled: true, summary: "submit_refund → approve · edit · reject" },
  { type: "pii", enabled: false, summary: "email → redact (input)" },
];

export const PROJECTS = [
  { id: "p_support", name: "Customer Support", slug: "customer-support", status: "active", workflows: 4, tools: 7, runs7d: 1840, spark: spark(14, 60, 30), edited: "12m ago" },
  { id: "p_ops", name: "Internal Ops Bot", slug: "internal-ops-bot", status: "active", workflows: 2, tools: 5, runs7d: 620, spark: spark(14, 28, 16), edited: "3h ago" },
  { id: "p_sales", name: "Sales Assistant", slug: "sales-assistant", status: "active", workflows: 3, tools: 9, runs7d: 980, spark: spark(14, 40, 22), edited: "1d ago" },
  { id: "p_research", name: "Research Copilot", slug: "research-copilot", status: "draft", workflows: 1, tools: 3, runs7d: 40, spark: spark(14, 6, 6), edited: "2d ago" },
  { id: "p_data", name: "Data Q&A", slug: "data-qa", status: "active", workflows: 2, tools: 4, runs7d: 410, spark: spark(14, 20, 12), edited: "4d ago" },
  { id: "p_archived", name: "Legacy Triage", slug: "legacy-triage", status: "draft", workflows: 1, tools: 2, runs7d: 0, spark: spark(14, 2, 2), edited: "3w ago" },
];

export const RECENT_RUNS = [
  { id: "r1", project: "Customer Support", workflow: "Support Router", status: "done", dur: "4.2s", tokens: "12.4k", trigger: "teams", time: "2m ago" },
  { id: "r2", project: "Sales Assistant", workflow: "Lead Qualifier", status: "done", dur: "2.1s", tokens: "6.1k", trigger: "api", time: "5m ago" },
  { id: "r3", project: "Customer Support", workflow: "Support Router", status: "interrupted", dur: "1.8s", tokens: "3.2k", trigger: "teams", time: "8m ago" },
  { id: "r4", project: "Internal Ops Bot", workflow: "PR Summarizer", status: "error", dur: "0.9s", tokens: "1.1k", trigger: "mcp", time: "14m ago" },
  { id: "r5", project: "Data Q&A", workflow: "Metrics Explainer", status: "done", dur: "3.6s", tokens: "9.8k", trigger: "playground", time: "21m ago" },
  { id: "r6", project: "Customer Support", workflow: "Refund Flow", status: "done", dur: "5.5s", tokens: "15.2k", trigger: "teams", time: "33m ago" },
];

export const KB_SOURCES = [
  { id: "k1", name: "Help Center (acme.dev/help)", kind: "url", status: "ready", chunks: 482, size: "3.1 MB", model: "text-embedding-3-small", updated: "1h ago" },
  { id: "k2", name: "Billing FAQ.pdf", kind: "file", status: "ready", chunks: 96, size: "740 KB", model: "text-embedding-3-small", updated: "1d ago" },
  { id: "k3", name: "API Reference.pdf", kind: "file", status: "processing", prog: 62, chunks: 210, size: "2.4 MB", model: "text-embedding-3-small", updated: "now" },
  { id: "k4", name: "s3://acme-docs/policies", kind: "s3", status: "ready", chunks: 154, size: "1.2 MB", model: "text-embedding-3-small", updated: "3d ago" },
  { id: "k5", name: "Onboarding notes", kind: "text", status: "error", chunks: 0, size: "12 KB", model: "-", updated: "5d ago" },
];
export const QA_PAIRS = [
  { id: "q1", q: "How do I reset my password?", a: "Go to Settings → Security → Reset password. A link is emailed to you.", kind: "faq", tags: ["account"], upvotes: 42, used: "3m ago" },
  { id: "q2", q: 'Why is my order stuck in "processing"?', a: "Processing clears within 30 min. If longer, the payment hold failed - retry the card.", kind: "error_workaround", tags: ["orders", "billing"], upvotes: 31, used: "18m ago" },
  { id: "q3", q: "Can I change my plan mid-cycle?", a: "Yes. Upgrades are prorated immediately; downgrades apply next cycle.", kind: "faq", tags: ["billing"], upvotes: 27, used: "1h ago" },
  { id: "q4", q: "Error E-4012 on checkout", a: "E-4012 means an expired CSRF token. Refresh the page and retry.", kind: "error_workaround", tags: ["errors"], upvotes: 19, used: "2h ago" },
];
export const SEARCH_HITS = [
  { title: "Billing FAQ.pdf · §3 Refunds", vec: 0.91, fts: 0.74, fused: 0.88, text: "Refunds are issued to the original payment method within 5–7 business days…" },
  { title: "Help Center · Cancel an order", vec: 0.86, fts: 0.81, fused: 0.85, text: "You can cancel an order before it ships from the Orders page…" },
  { title: "API Reference.pdf · POST /refunds", vec: 0.83, fts: 0.62, fused: 0.79, text: "Creates a refund object. Requires an order in a refundable state…" },
  { title: "Policies · Returns window", vec: 0.71, fts: 0.55, fused: 0.68, text: "Items may be returned within 30 days of delivery for a full refund…" },
];

export const TRACE_RUNS = [
  { id: "tr1", workflow: "Support Router", status: "done", started: "14:22:08", dur: "4.2s", tokens: "12.4k", cost: "$0.038", trigger: "teams" },
  { id: "tr2", workflow: "Support Router", status: "interrupted", started: "14:18:51", dur: "1.8s", tokens: "3.2k", cost: "$0.009", trigger: "teams" },
  { id: "tr3", workflow: "Refund Flow", status: "done", started: "14:10:33", dur: "5.5s", tokens: "15.2k", cost: "$0.047", trigger: "api" },
  { id: "tr4", workflow: "PR Summarizer", status: "error", started: "13:58:02", dur: "0.9s", tokens: "1.1k", cost: "$0.003", trigger: "mcp" },
  { id: "tr5", workflow: "Metrics Explainer", status: "done", started: "13:44:19", dur: "3.6s", tokens: "9.8k", cost: "$0.030", trigger: "playground" },
];
export const SPANS = [
  { id: "s0", name: "Support Router", kind: "chain", depth: 0, start: 0, dur: 4200, tokens: "12.4k", cost: "$0.038" },
  { id: "s1", name: "faq_deflect", kind: "retriever", depth: 1, start: 40, dur: 210, tokens: "-", cost: "$0.000" },
  { id: "s2", name: "intent_router", kind: "node", depth: 1, start: 260, dur: 60, tokens: "-", cost: "$0.000" },
  { id: "s3", name: "billing_agent", kind: "agent", depth: 1, start: 330, dur: 3600, tokens: "11.9k", cost: "$0.036" },
  { id: "s4", name: "model · claude-sonnet-4-6", kind: "llm", depth: 2, start: 360, dur: 1400, tokens: "4.1k", cost: "$0.013" },
  { id: "s5", name: "tool · get_order", kind: "tool", depth: 2, start: 1780, dur: 320, tokens: "92", cost: "$0.000" },
  { id: "s6", name: "model · claude-sonnet-4-6", kind: "llm", depth: 2, start: 2130, dur: 1700, tokens: "7.7k", cost: "$0.023" },
  { id: "s7", name: "approve_refund", kind: "node", depth: 1, start: 3950, dur: 250, tokens: "-", cost: "$0.000" },
];
export const COST_BY_NODE = [
  { name: "billing_agent", cost: 0.036, color: "var(--accent)" },
  { name: "tech_agent", cost: 0.0, color: "var(--io-json)" },
  { name: "kb_search", cost: 0.001, color: "var(--io-vector)" },
  { name: "router", cost: 0.0005, color: "var(--io-control)" },
];

export const SECRETS = [
  { id: "sec1", name: "orders_api_creds", kind: "csrf_session", version: 3, used: "2m ago" },
  { id: "sec2", name: "openai_key", kind: "api_key", version: 1, used: "1m ago" },
  { id: "sec3", name: "anthropic_key", kind: "api_key", version: 1, used: "1m ago" },
  { id: "sec4", name: "jira_client_secret", kind: "oauth2", version: 2, used: "1h ago" },
  { id: "sec5", name: "stripe_secret", kind: "bearer", version: 1, used: "3h ago" },
];
export const AUDIT = [
  { action: "secret.read", actor: "orders_session", resource: "orders_api_creds", at: "14:22:09" },
  { action: "workflow.publish", actor: "you@acme.dev", resource: "Support Router v7", at: "13:40:11" },
  { action: "tool.test", actor: "you@acme.dev", resource: "submit_refund", at: "13:38:55" },
  { action: "secret.write", actor: "you@acme.dev", resource: "stripe_secret", at: "11:02:30" },
];

// Sectioned nav: top-level leaves (Overview, Settings) plus collapsible groups
// (Build / Deploy / Observe). The sidebar renders a leaf as a button and a group as a
// labeled, collapsible section. `countKey` shows a live badge.
export type NavLeaf = { id: string; label: string; icon: string; help?: string; countKey?: string };
export type NavGroup = { section: string; items: NavLeaf[] };
export type NavEntry = NavLeaf | NavGroup;

export const PROJECT_NAV: NavEntry[] = [
  { id: "overview", label: "Overview", icon: "overview", help: "Project dashboard - usage, cost, and recent activity at a glance." },
  { section: "Build", items: [
    { id: "playground", label: "Playground", icon: "playground", help: "Chat with a workflow to test it live, with token + cost metering." },
    { id: "workflows", label: "Workflows", icon: "workflows", countKey: "workflows", help: "The visual canvas - wire nodes (agents, tools, routers, triggers) into a graph." },
    { id: "agents", label: "Agents", icon: "agents", countKey: "agents", help: "Reusable agent presets (model + prompt + tools + middleware) to drop into workflows." },
    { id: "tools", label: "Tools", icon: "tools", countKey: "tools", help: "Capabilities an agent can call: REST, GraphQL, Code, SQL, or built-ins." },
    { id: "components", label: "Components", icon: "grid", countKey: "components", help: "User-defined UI widgets (HTML/CSS) an agent can render in chat - tables, cards, forms, actions." },
    { id: "knowledge", label: "Knowledge", icon: "knowledge", countKey: "knowledge", help: "Documents + Q&A pairs that ground answers (RAG). Add text, URLs, files, or crawl a site." },
    { id: "auth", label: "Auth Providers", icon: "auth", countKey: "auth", help: "Reusable credential strategies (Bearer, API key, OAuth, CSRF) that tools attach to." },
    { id: "mcp", label: "External MCP", icon: "connect", help: "Connect external MCP servers (GitHub, Slack, …) and toggle which of their tools agents and workflows can use." },
  ] },
  { section: "Deploy", items: [
    { id: "channels", label: "Channels", icon: "msg", help: "Deploy a workflow to a surface: email or Microsoft Teams." },
    { id: "triggers", label: "Triggers", icon: "bolt", help: "Event-driven entry points - webhook URLs, schedules, and pollers that start runs." },
    { id: "connect", label: "Connect", icon: "connect", help: "Expose this project's tools as an MCP server, and register external MCP servers to consume." },
    { id: "embed", label: "Embed", icon: "grid", help: "Embed this project's chatbot as a widget on any website (publishable key + allowed origins)." },
  ] },
  { section: "Observe", items: [
    { id: "traces", label: "Traces", icon: "traces", help: "Per-run span waterfall with model calls, tokens, latency, and cost." },
    { id: "datasets", label: "Evaluations", icon: "validate", help: "Test datasets (input + expected) scored against a workflow to catch regressions." },
    { id: "handoff", label: "Agent inbox", icon: "user", help: "Live conversations escalated to a human - reply here to resume the run." },
  ] },
  { id: "settings", label: "Settings", icon: "settings", help: "Model defaults, provider keys, secrets, team & roles, and the audit log." },
];

export const IO_COLOR: Record<string, string> = {
  messages: "var(--io-messages)", text: "var(--io-text)", json: "var(--io-json)", tool: "var(--io-tool)",
  embedding: "var(--io-vector)", vector: "var(--io-vector)", any: "var(--io-any)", control: "var(--io-control)",
};
export const KIND_LABEL: Record<string, string> = { rest_api: "REST", graphql: "GraphQL", code: "Code", sql: "SQL", builtin: "Builtin" };
export const KIND_ICON: Record<string, string> = { rest_api: "k_rest", graphql: "k_graphql", code: "k_code", sql: "db", builtin: "k_builtin" };
