/* Forge mock data — generic, plausible SaaS content. window.DATA */
(function () {
  const spark = (n, base, amp) => Array.from({ length: n }, (_, i) =>
    Math.max(0, Math.round(base + Math.sin(i * 0.8) * amp + (Math.random() - 0.5) * amp * 0.8)));

  const MODELS = [
    { id: 'anthropic:claude-sonnet-4-6', name: 'claude-sonnet-4-6', provider: 'Anthropic', ctx: '200k', tools: true, vision: true },
    { id: 'anthropic:claude-haiku-4-2', name: 'claude-haiku-4-2', provider: 'Anthropic', ctx: '200k', tools: true, vision: true },
    { id: 'openai:gpt-5.4', name: 'gpt-5.4', provider: 'OpenAI', ctx: '400k', tools: true, vision: true },
    { id: 'openai:gpt-5.4-mini', name: 'gpt-5.4-mini', provider: 'OpenAI', ctx: '256k', tools: true, vision: true },
    { id: 'google_genai:gemini-3.1-pro-preview', name: 'gemini-3.1-pro-preview', provider: 'Google', ctx: '1M', tools: true, vision: true },
    { id: 'google_genai:gemini-3.5-flash', name: 'gemini-3.5-flash', provider: 'Google', ctx: '1M', tools: true, vision: false },
  ];

  // node categories -> palette (generated from "registry")
  const NODE_CATALOG = [
    { group: 'Flow', color: 'var(--io-control)', items: [
      { type: 'start', icon: 'n_start', label: 'Start', desc: 'Entry marker' },
      { type: 'end', icon: 'n_end', label: 'End', desc: 'Terminal node' },
      { type: 'router', icon: 'n_router', label: 'Router', desc: 'Conditional branch' },
      { type: 'loop', icon: 'n_loop', label: 'Loop', desc: 'Bounded iteration' },
      { type: 'parallel_fanout', icon: 'n_fanout', label: 'Fan-out', desc: 'Map over a list' },
      { type: 'join', icon: 'n_join', label: 'Join', desc: 'Wait-for-all / reduce' },
    ]},
    { group: 'Agents', color: 'var(--accent)', items: [
      { type: 'agent', icon: 'n_agent', label: 'Agent', desc: 'ReAct tool loop' },
      { type: 'deep_agent', icon: 'n_deepagent', label: 'Deep Agent', desc: 'Planning + subagents harness' },
    ]},
    { group: 'Model & Tools', color: 'var(--io-json)', items: [
      { type: 'llm', icon: 'n_llm', label: 'LLM', desc: 'Single model call' },
      { type: 'tool_call', icon: 'n_tool', label: 'Tool Call', desc: 'Run a specific tool' },
      { type: 'transform', icon: 'n_transform', label: 'Transform', desc: 'JMESPath data map' },
      { type: 'code', icon: 'n_code', label: 'Code', desc: 'Sandboxed transform' },
    ]},
    { group: 'Knowledge', color: 'var(--io-vector)', items: [
      { type: 'retrieval', icon: 'n_retrieval', label: 'Retrieval', desc: 'RAG query' },
      { type: 'qa_lookup', icon: 'n_qa', label: 'Q&A Lookup', desc: 'Semantic pair match' },
    ]},
    { group: 'Human', color: 'var(--warn)', items: [
      { type: 'human_input', icon: 'n_human', label: 'Human Input', desc: 'HITL pause via interrupt' },
    ]},
    { group: 'Integrations', color: 'var(--signal)', items: [
      { type: 'subworkflow', icon: 'n_subworkflow', label: 'Subworkflow', desc: 'Embed another graph' },
      { type: 'webhook_out', icon: 'n_webhook', label: 'Webhook', desc: 'Call external URL' },
      { type: 'emit_event', icon: 'n_emit', label: 'Emit Event', desc: 'Push custom SSE frame' },
    ]},
  ];
  const NODE_META = {};
  NODE_CATALOG.forEach(g => g.items.forEach(it => NODE_META[it.type] = { ...it, group: g.group, color: g.color }));

  // category color keys for header tints
  const CAT_BY_TYPE = {
    start: 'control', end: 'control', router: 'control', loop: 'control', parallel_fanout: 'control', join: 'control',
    agent: 'agent', deep_agent: 'agent',
    llm: 'json', tool_call: 'json', transform: 'json', code: 'json',
    retrieval: 'vector', qa_lookup: 'vector',
    human_input: 'human',
    subworkflow: 'signal', webhook_out: 'signal', emit_event: 'signal',
  };

  // ---- The hero workflow: "Support Router" ----
  const workflowNodes = [
    { id: 'start', type: 'start', position: { x: 40, y: 300 }, data: {}, summary: [] },
    { id: 'faq_deflect', type: 'qa_lookup', position: { x: 220, y: 286 }, data: { threshold: 0.85, kind: 'faq' },
      title: 'FAQ Deflect', summary: ['threshold ≥ 0.85', 'kind · faq'] },
    { id: 'intent_router', type: 'router', position: { x: 430, y: 280 }, data: {},
      title: 'Intent Router', summary: ['expression · state.intent', 'billing · technical · default'],
      cases: ['billing', 'technical', 'default'] },
    { id: 'kb_search', type: 'retrieval', position: { x: 700, y: 110 }, data: {},
      title: 'Help Docs', summary: ['3 sources · top_k 5', 'hybrid + rerank'] },
    { id: 'billing_agent', type: 'agent', position: { x: 690, y: 270 }, data: {},
      title: 'Billing Agent', summary: ['claude-sonnet-4-6', '2 tools · 4 middleware'],
      mw: ['summarization', 'tool_call_limit', 'human_in_the_loop', 'pii'] },
    { id: 'tech_agent', type: 'deep_agent', position: { x: 690, y: 470 }, data: {},
      title: 'Tech Agent', summary: ['gpt-5.4 · deep agent', 'subagents 2 · planning on'],
      mw: ['summarization', 'context_editing'] },
    { id: 'approve_refund', type: 'human_input', position: { x: 980, y: 270 }, data: {},
      title: 'Approve Refund', summary: ['"Approve this refund?"', 'approve · edit · reject'] },
    { id: 'end', type: 'end', position: { x: 1210, y: 320 }, data: {}, summary: [] },
  ];
  const workflowEdges = [
    { id: 'e1', source: 'start', target: 'faq_deflect', io: 'control' },
    { id: 'e2', source: 'faq_deflect', target: 'intent_router', io: 'messages' },
    { id: 'e3', source: 'intent_router', target: 'billing_agent', io: 'control', label: 'billing' },
    { id: 'e4', source: 'intent_router', target: 'tech_agent', io: 'control', label: 'technical' },
    { id: 'e5', source: 'kb_search', target: 'billing_agent', io: 'json' },
    { id: 'e6', source: 'billing_agent', target: 'approve_refund', io: 'messages' },
    { id: 'e7', source: 'approve_refund', target: 'end', io: 'messages' },
    { id: 'e8', source: 'tech_agent', target: 'end', io: 'messages' },
  ];
  // run order for the live-run overlay
  const runOrder = ['start', 'faq_deflect', 'intent_router', 'kb_search', 'billing_agent', 'approve_refund', 'end'];

  const TOOLS = [
    { id: 't_get_order', name: 'get_order', kind: 'rest_api', auth: 'orders_session', enabled: true, tested: 'pass', version: 4,
      desc: 'Fetch an order by ID from the commerce API, including line items and totals.', method: 'GET',
      url: 'https://api.acme.dev/v2/orders/{order_id}', rawTok: 1240, projTok: 92 },
    { id: 't_get_invoice', name: 'get_invoice', kind: 'rest_api', auth: 'orders_session', enabled: true, tested: 'pass', version: 2,
      desc: 'Retrieve a customer invoice and its payment status.', method: 'GET',
      url: 'https://api.acme.dev/v2/invoices/{invoice_id}', rawTok: 880, projTok: 64 },
    { id: 't_search_kb', name: 'search_catalog', kind: 'graphql', auth: null, enabled: true, tested: 'pass', version: 1,
      desc: 'Query the product catalog via GraphQL.', method: 'POST', url: 'https://api.acme.dev/graphql', rawTok: 2100, projTok: 140 },
    { id: 't_refund', name: 'submit_refund', kind: 'rest_api', auth: 'orders_session', enabled: true, tested: 'fail', version: 3,
      desc: 'Issue a refund against an order. Requires human approval.', method: 'POST',
      url: 'https://api.acme.dev/v2/orders/{order_id}/refunds', rawTok: 320, projTok: 48 },
    { id: 't_geo', name: 'geocode_address', kind: 'code', auth: null, enabled: true, tested: 'untested', version: 1,
      desc: 'Normalize and geocode a postal address using a sandboxed Python function.', rawTok: 0, projTok: 0 },
    { id: 't_jira', name: 'create_ticket', kind: 'mcp', auth: 'jira_oauth', enabled: true, tested: 'pass', version: 1,
      desc: 'Create an issue in the project tracker over MCP.', rawTok: 540, projTok: 70 },
    { id: 't_web', name: 'web_search', kind: 'builtin', auth: null, enabled: false, tested: 'untested', version: 1,
      desc: 'Search the public web (Tavily).', rawTok: 0, projTok: 0 },
  ];

  const AUTH_PROVIDERS = [
    { id: 'orders_session', name: 'orders_session', kind: 'csrf_session', tested: 'pass', usedBy: 3, ttl: 1800 },
    { id: 'jira_oauth', name: 'jira_oauth', kind: 'oauth2_client_credentials', tested: 'pass', usedBy: 1, ttl: 3600 },
    { id: 'stripe_bearer', name: 'stripe_bearer', kind: 'bearer', tested: 'pass', usedBy: 2, ttl: 0 },
    { id: 'legacy_basic', name: 'legacy_basic', kind: 'basic', tested: 'untested', usedBy: 0, ttl: 0 },
  ];

  const AGENTS = [
    { id: 'a_billing', name: 'billing_agent', flavor: 'agent', model: 'anthropic:claude-sonnet-4-6', tools: 2, mw: 4, updated: '2h ago' },
    { id: 'a_tech', name: 'tech_agent', flavor: 'deep_agent', model: 'openai:gpt-5.4', tools: 5, mw: 3, updated: '1d ago' },
    { id: 'a_triage', name: 'triage_agent', flavor: 'agent', model: 'openai:gpt-5.4-mini', tools: 1, mw: 2, updated: '3d ago' },
    { id: 'a_research', name: 'research_agent', flavor: 'deep_agent', model: 'google_genai:gemini-3.1-pro-preview', tools: 3, mw: 5, updated: '5d ago' },
  ];

  const MIDDLEWARE_CATALOG = [
    { cat: 'Memory & Context', color: 'var(--signal)', items: [
      { type: 'summarization', name: 'Summarization', desc: 'Summarize older messages near a token limit.' },
      { type: 'context_editing', name: 'Context Editing', desc: 'Clear old tool outputs past a threshold.' },
      { type: 'todo', name: 'Planning (To-do)', desc: 'Add a write_todos planning tool.' },
    ]},
    { cat: 'Safety & Guardrails', color: 'var(--err)', items: [
      { type: 'pii', name: 'PII Handling', desc: 'Detect & redact/mask/block PII.' },
      { type: 'guardrail_regex', name: 'Regex Guardrail', desc: 'Block or flag matched patterns.' },
      { type: 'openai_moderation', name: 'Moderation', desc: 'OpenAI moderation on input/output.' },
    ]},
    { cat: 'Reliability', color: 'var(--info)', items: [
      { type: 'tool_retry', name: 'Tool Retry', desc: 'Retry failed tool calls with backoff.' },
      { type: 'model_retry', name: 'Model Retry', desc: 'Retry failed model calls.' },
      { type: 'model_fallback', name: 'Model Fallback', desc: 'Failover across providers.' },
      { type: 'request_signing', name: 'Request Signing', desc: 'Inject signed creds into tool calls.' },
    ]},
    { cat: 'Cost & Limits', color: 'var(--warn)', items: [
      { type: 'model_call_limit', name: 'Model Call Limit', desc: 'Cap model calls per run/thread.' },
      { type: 'tool_call_limit', name: 'Tool Call Limit', desc: 'Cap tool calls, global or per-tool.' },
      { type: 'tenant_budget', name: 'Budget Cap', desc: 'Stop when cost/tokens exceed a cap.' },
      { type: 'llm_tool_selector', name: 'Tool Selector', desc: 'Pre-select relevant tools (saves tokens).' },
    ]},
    { cat: 'Human Oversight', color: 'var(--accent)', items: [
      { type: 'human_in_the_loop', name: 'Human-in-the-loop', desc: 'Pause for approval on sensitive tools.' },
    ]},
    { cat: 'Provider-specific', color: 'var(--io-json)', items: [
      { type: 'anthropic_prompt_caching', name: 'Prompt Caching', desc: 'Cache the system prompt (Anthropic).' },
    ]},
    { cat: 'Advanced', color: 'var(--io-vector)', items: [
      { type: 'dynamic_model_by_state', name: 'Dynamic Model', desc: 'Switch model at runtime by state.' },
      { type: 'tool_filter_by_context', name: 'Tool Filter', desc: 'Show/hide tools by context/role.' },
    ]},
  ];
  const MW_META = {};
  MIDDLEWARE_CATALOG.forEach(c => c.items.forEach(it => MW_META[it.type] = { ...it, cat: c.cat, color: c.color }));

  // billing_agent's actual middleware stack (for the agent config screen)
  const AGENT_MW_STACK = [
    { type: 'summarization', enabled: true, summary: 'Summarize when > 4,000 tok · keep last 20 msgs' },
    { type: 'tool_call_limit', enabled: true, summary: 'get_order · max 3 calls per run' },
    { type: 'human_in_the_loop', enabled: true, summary: 'submit_refund → approve · edit · reject' },
    { type: 'pii', enabled: false, summary: 'email → redact (input)' },
  ];

  const PROJECTS = [
    { id: 'p_support', name: 'Customer Support', slug: 'customer-support', status: 'active', workflows: 4, tools: 7, runs7d: 1840, spark: spark(14, 60, 30), edited: '12m ago' },
    { id: 'p_ops', name: 'Internal Ops Bot', slug: 'internal-ops-bot', status: 'active', workflows: 2, tools: 5, runs7d: 620, spark: spark(14, 28, 16), edited: '3h ago' },
    { id: 'p_sales', name: 'Sales Assistant', slug: 'sales-assistant', status: 'active', workflows: 3, tools: 9, runs7d: 980, spark: spark(14, 40, 22), edited: '1d ago' },
    { id: 'p_research', name: 'Research Copilot', slug: 'research-copilot', status: 'draft', workflows: 1, tools: 3, runs7d: 40, spark: spark(14, 6, 6), edited: '2d ago' },
    { id: 'p_data', name: 'Data Q&A', slug: 'data-qa', status: 'active', workflows: 2, tools: 4, runs7d: 410, spark: spark(14, 20, 12), edited: '4d ago' },
    { id: 'p_archived', name: 'Legacy Triage', slug: 'legacy-triage', status: 'draft', workflows: 1, tools: 2, runs7d: 0, spark: spark(14, 2, 2), edited: '3w ago' },
  ];

  const RECENT_RUNS = [
    { id: 'r1', project: 'Customer Support', workflow: 'Support Router', status: 'done', dur: '4.2s', tokens: '12.4k', trigger: 'widget', time: '2m ago' },
    { id: 'r2', project: 'Sales Assistant', workflow: 'Lead Qualifier', status: 'done', dur: '2.1s', tokens: '6.1k', trigger: 'api', time: '5m ago' },
    { id: 'r3', project: 'Customer Support', workflow: 'Support Router', status: 'interrupted', dur: '1.8s', tokens: '3.2k', trigger: 'widget', time: '8m ago' },
    { id: 'r4', project: 'Internal Ops Bot', workflow: 'PR Summarizer', status: 'error', dur: '0.9s', tokens: '1.1k', trigger: 'mcp', time: '14m ago' },
    { id: 'r5', project: 'Data Q&A', workflow: 'Metrics Explainer', status: 'done', dur: '3.6s', tokens: '9.8k', trigger: 'playground', time: '21m ago' },
    { id: 'r6', project: 'Customer Support', workflow: 'Refund Flow', status: 'done', dur: '5.5s', tokens: '15.2k', trigger: 'widget', time: '33m ago' },
  ];

  const KB_SOURCES = [
    { id: 'k1', name: 'Help Center (acme.dev/help)', kind: 'url', status: 'ready', chunks: 482, size: '3.1 MB', model: 'text-embedding-3-small', updated: '1h ago' },
    { id: 'k2', name: 'Billing FAQ.pdf', kind: 'file', status: 'ready', chunks: 96, size: '740 KB', model: 'text-embedding-3-small', updated: '1d ago' },
    { id: 'k3', name: 'API Reference.pdf', kind: 'file', status: 'processing', prog: 62, chunks: 210, size: '2.4 MB', model: 'text-embedding-3-small', updated: 'now' },
    { id: 'k4', name: 's3://acme-docs/policies', kind: 's3', status: 'ready', chunks: 154, size: '1.2 MB', model: 'text-embedding-3-small', updated: '3d ago' },
    { id: 'k5', name: 'Onboarding notes', kind: 'text', status: 'error', chunks: 0, size: '12 KB', model: '—', updated: '5d ago' },
  ];
  const QA_PAIRS = [
    { id: 'q1', q: 'How do I reset my password?', a: 'Go to Settings → Security → Reset password. A link is emailed to you.', kind: 'faq', tags: ['account'], upvotes: 42, used: '3m ago' },
    { id: 'q2', q: 'Why is my order stuck in "processing"?', a: 'Processing clears within 30 min. If longer, the payment hold failed — retry the card.', kind: 'error_workaround', tags: ['orders', 'billing'], upvotes: 31, used: '18m ago' },
    { id: 'q3', q: 'Can I change my plan mid-cycle?', a: 'Yes. Upgrades are prorated immediately; downgrades apply next cycle.', kind: 'faq', tags: ['billing'], upvotes: 27, used: '1h ago' },
    { id: 'q4', q: 'Error E-4012 on checkout', a: 'E-4012 means an expired CSRF token. Refresh the page and retry.', kind: 'error_workaround', tags: ['errors'], upvotes: 19, used: '2h ago' },
  ];
  const SEARCH_HITS = [
    { title: 'Billing FAQ.pdf · §3 Refunds', vec: 0.91, fts: 0.74, fused: 0.88, text: 'Refunds are issued to the original payment method within 5–7 business days…' },
    { title: 'Help Center · Cancel an order', vec: 0.86, fts: 0.81, fused: 0.85, text: 'You can cancel an order before it ships from the Orders page…' },
    { title: 'API Reference.pdf · POST /refunds', vec: 0.83, fts: 0.62, fused: 0.79, text: 'Creates a refund object. Requires an order in a refundable state…' },
    { title: 'Policies · Returns window', vec: 0.71, fts: 0.55, fused: 0.68, text: 'Items may be returned within 30 days of delivery for a full refund…' },
  ];

  const TRACE_RUNS = [
    { id: 'tr1', workflow: 'Support Router', status: 'done', started: '14:22:08', dur: '4.2s', tokens: '12.4k', cost: '$0.038', trigger: 'widget' },
    { id: 'tr2', workflow: 'Support Router', status: 'interrupted', started: '14:18:51', dur: '1.8s', tokens: '3.2k', cost: '$0.009', trigger: 'widget' },
    { id: 'tr3', workflow: 'Refund Flow', status: 'done', started: '14:10:33', dur: '5.5s', tokens: '15.2k', cost: '$0.047', trigger: 'api' },
    { id: 'tr4', workflow: 'PR Summarizer', status: 'error', started: '13:58:02', dur: '0.9s', tokens: '1.1k', cost: '$0.003', trigger: 'mcp' },
    { id: 'tr5', workflow: 'Metrics Explainer', status: 'done', started: '13:44:19', dur: '3.6s', tokens: '9.8k', cost: '$0.030', trigger: 'playground' },
  ];
  // span waterfall for the selected trace
  const SPANS = [
    { id: 's0', name: 'Support Router', kind: 'chain', depth: 0, start: 0, dur: 4200, tokens: '12.4k', cost: '$0.038' },
    { id: 's1', name: 'faq_deflect', kind: 'retriever', depth: 1, start: 40, dur: 210, tokens: '—', cost: '$0.000' },
    { id: 's2', name: 'intent_router', kind: 'node', depth: 1, start: 260, dur: 60, tokens: '—', cost: '$0.000' },
    { id: 's3', name: 'billing_agent', kind: 'agent', depth: 1, start: 330, dur: 3600, tokens: '11.9k', cost: '$0.036' },
    { id: 's4', name: 'model · claude-sonnet-4-6', kind: 'llm', depth: 2, start: 360, dur: 1400, tokens: '4.1k', cost: '$0.013' },
    { id: 's5', name: 'tool · get_order', kind: 'tool', depth: 2, start: 1780, dur: 320, tokens: '92', cost: '$0.000' },
    { id: 's6', name: 'model · claude-sonnet-4-6', kind: 'llm', depth: 2, start: 2130, dur: 1700, tokens: '7.7k', cost: '$0.023' },
    { id: 's7', name: 'approve_refund', kind: 'node', depth: 1, start: 3950, dur: 250, tokens: '—', cost: '$0.000' },
  ];
  const COST_BY_NODE = [
    { name: 'billing_agent', cost: 0.036, color: 'var(--accent)' },
    { name: 'tech_agent', cost: 0.0, color: 'var(--io-json)' },
    { name: 'kb_search', cost: 0.001, color: 'var(--io-vector)' },
    { name: 'router', cost: 0.0005, color: 'var(--io-control)' },
  ];

  const SECRETS = [
    { id: 'sec1', name: 'orders_api_creds', kind: 'csrf_session', version: 3, used: '2m ago' },
    { id: 'sec2', name: 'openai_key', kind: 'api_key', version: 1, used: '1m ago' },
    { id: 'sec3', name: 'anthropic_key', kind: 'api_key', version: 1, used: '1m ago' },
    { id: 'sec4', name: 'jira_client_secret', kind: 'oauth2', version: 2, used: '1h ago' },
    { id: 'sec5', name: 'stripe_secret', kind: 'bearer', version: 1, used: '3h ago' },
  ];
  const AUDIT = [
    { action: 'secret.read', actor: 'orders_session', resource: 'orders_api_creds', at: '14:22:09' },
    { action: 'workflow.publish', actor: 'you@acme.dev', resource: 'Support Router v7', at: '13:40:11' },
    { action: 'tool.test', actor: 'you@acme.dev', resource: 'submit_refund', at: '13:38:55' },
    { action: 'secret.write', actor: 'you@acme.dev', resource: 'stripe_secret', at: '11:02:30' },
  ];

  const PROJECT_NAV = [
    { id: 'overview', label: 'Overview', icon: 'overview' },
    { id: 'workflows', label: 'Workflows', icon: 'workflows', count: 4 },
    { id: 'agents', label: 'Agents', icon: 'agents', count: 4 },
    { id: 'tools', label: 'Tools', icon: 'tools', count: 7 },
    { id: 'auth', label: 'Auth Providers', icon: 'auth', count: 4 },
    { id: 'knowledge', label: 'Knowledge', icon: 'knowledge', count: 5 },
    { id: 'playground', label: 'Playground', icon: 'playground' },
    { id: 'traces', label: 'Traces', icon: 'traces' },
    { id: 'widget', label: 'Widget', icon: 'widget' },
    { id: 'connect', label: 'Connect (MCP)', icon: 'connect' },
    { id: 'settings', label: 'Settings', icon: 'settings' },
  ];

  window.DATA = {
    MODELS, NODE_CATALOG, NODE_META, CAT_BY_TYPE, workflowNodes, workflowEdges, runOrder,
    TOOLS, AUTH_PROVIDERS, AGENTS, MIDDLEWARE_CATALOG, MW_META, AGENT_MW_STACK,
    PROJECTS, RECENT_RUNS, KB_SOURCES, QA_PAIRS, SEARCH_HITS,
    TRACE_RUNS, SPANS, COST_BY_NODE, SECRETS, AUDIT, PROJECT_NAV, spark,
    IO_COLOR: { messages: 'var(--io-messages)', text: 'var(--io-text)', json: 'var(--io-json)', tool: 'var(--io-tool)', embedding: 'var(--io-vector)', vector: 'var(--io-vector)', any: 'var(--io-any)', control: 'var(--io-control)' },
    KIND_LABEL: { rest_api: 'REST', graphql: 'GraphQL', code: 'Code', mcp: 'MCP', builtin: 'Builtin' },
    KIND_ICON: { rest_api: 'k_rest', graphql: 'k_graphql', code: 'k_code', mcp: 'k_mcp', builtin: 'k_builtin' },
  };
})();
