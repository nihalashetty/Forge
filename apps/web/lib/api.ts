/* Forge API client. Calls are proxied through Next (/api/forge/* -> backend) so the
   app and API share an origin in dev (see next.config.mjs). */

const BASE = "/api/forge";
const DIRECT_API = (process.env.NEXT_PUBLIC_FORGE_API_URL || "").replace(/\/$/, "");

function isLocalWebHost(hostname: string) {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

function sseBase() {
  if (DIRECT_API) return DIRECT_API;
  if (typeof window !== "undefined" && isLocalWebHost(window.location.hostname)) {
    return "http://127.0.0.1:8000";
  }
  return BASE;
}

function sseUrl(path: string) {
  return `${sseBase()}${path}`;
}

export interface Project {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  status: string;
  config?: Record<string, unknown>;
}

export interface Workflow {
  id: string;
  project_id: string;
  name: string;
  description?: string | null;
  status: string;
  active_version: number;
  executable: Record<string, any>;
  canvas: Record<string, any>;
}

export interface ValidateResult {
  valid: boolean;
  errors: { pointer: string; message: string; node_id?: string }[];
}

export interface Tool {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  enabled: boolean;
  version: number;
  auth_provider_id?: string | null;
  last_tested?: string | null;
  config: Record<string, any>;
}

export interface ComponentT {
  id: string;
  name: string;
  title?: string | null;
  description: string;
  props_schema: Record<string, any>;
  html: string;
  css: string;
  actions: Record<string, any>[];
  sample_props: Record<string, any>;
  kind: string;
  enabled: boolean;
  version: number;
}

export interface RedirectInfo {
  followed: boolean;
  status?: number;
  final_status?: number;
  requested_url?: string;
  final_url?: string;
  location?: string | null;
  chain?: string[];
  note?: string;
}

export interface ToolTestResult {
  ok: boolean;
  error?: string;
  status?: number;
  latency_ms?: number;
  raw?: any;
  projected?: any;
  raw_tokens?: number;
  projected_tokens?: number;
  final_url?: string;
  redirect?: RedirectInfo | null;
}

export interface AuthProviderT {
  id: string;
  project_id: string;
  name: string;
  kind: string;
  credentials_ref?: string | null;
  config: Record<string, any>;
}

export interface Agent {
  id: string;
  project_id: string;
  name: string;
  version: number;
  config: Record<string, any>;
  created_by?: string | null;
  created_by_email?: string | null;
}

export interface KbSource { id: string; project_id: string; kind: string; name: string; folder?: string; uri?: string | null; status: string; chunks: number; embedding_model?: string | null; chunking_strategy?: string | null; chunk_size?: number | null; chunk_overlap?: number | null; }
export interface RechunkSettings { chunking_strategy?: string; chunk_size?: number; chunk_overlap?: number; }
export interface QaPair { id: string; question: string; answer: string; kind: string; tags: string[]; upvotes: number; }
export interface SearchHit { text: string; score: number; source_id?: string; }
// Chunk-map visualizer (POST /knowledge/map): a 2-D (PCA) projection of the stored chunk vectors.
export interface ChunkPoint { id: string; x: number; y: number; source_id?: string | null; chunk_idx?: number | null; parent_id?: string | null; preview: string; retrieved?: number; }
export interface ChunkMapResult { points: ChunkPoint[]; sources: { id: string; name: string }[]; query_point: [number, number] | null; query: string | null; total: number; truncated: boolean; }
export interface ChunkDetail { id: string; text: string; source_id?: string | null; chunk_idx?: number | null; parent_id?: string | null; }
export interface Trace { id: string; run_id: string; workflow_id?: string | null; name: string; status: string; started_at?: string | null; ended_at?: string | null; latency_ms: number; total_tokens: number; total_cost_usd: number; }
export interface Span { id: string; parent_span_id?: string | null; name: string; kind: string; latency_ms: number; input?: any; output?: any; model?: string | null; input_tokens: number; output_tokens: number; cost_usd: number; error?: string | null; }
export interface Conversation { thread_id: string; actor: string; source: string; end_user_id?: string | null; workflow_id?: string | null; turns: number; total_tokens: number; total_cost_usd: number; started_at?: string | null; last_activity?: string | null; status: string; preview: string; }
export interface Turn { trace_id: string; run_id: string; source: string; user_message?: string | null; ai_response?: string | null; status: string; error?: string | null; latency_ms: number; total_tokens: number; total_cost_usd: number; started_at?: string | null; }
export interface ConversationDetail { conversation: Conversation; turns: Turn[]; }
export interface Facets { actors: string[]; sources: string[]; }
export interface Secret { id: string; name: string; kind: string; version: number; }

export interface StatRollup { runs: number; tokens: number; cost_usd: number; avg_latency_ms: number; }
export interface ReportRow extends StatRollup { label: string; kind: "workflow" | "assistant" | "other"; }
export interface ProjectStats {
  totals: StatRollup;
  last_7d: StatRollup;
  assistant: StatRollup & { turns: number };
  reports: ReportRow[];
}

export interface DashboardStats {
  runs_7d: number;
  total_runs: number;
  success_rate: number;
  avg_latency_ms: number;
  spend_7d: number;
  recent: { id: string; workflow: string; project: string; status: string; tokens: number; latency_ms: number; cost_usd: number; started_at: string | null }[];
  projects: Record<string, { workflows: number; tools: number; runs_7d: number }>;
  reports: (StatRollup & { project_id: string; project: string; assistant_cost_usd: number; assistant_turns: number })[];
  totals: StatRollup;
}

export interface NodeType {
  type: string;
  category: string;
  label: string;
  description: string;
  schema_id: string;
  allows_cycle: boolean;
  input_ports: { id: string; io_type: string; direction: string }[];
  output_ports: { id: string; io_type: string; direction: string }[];
}

/* ---- auth token storage (JWT). Sent as a Bearer header on every request. ---- */
const TOKEN_KEY = "forge_access_token";
const REFRESH_KEY = "forge_refresh_token";

export function getToken(): string | null {
  return typeof window !== "undefined" ? window.localStorage.getItem(TOKEN_KEY) : null;
}
export function setTokens(access: string, refresh?: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(TOKEN_KEY, access);
  if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh);
}
export function clearTokens() {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_KEY);
}
export function authHeader(): Record<string, string> {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}
export const UNAUTHORIZED_EVENT = "forge:unauthorized";

function on401() {
  if (typeof window !== "undefined") {
    clearTokens();
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT));
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...authHeader(), ...(init?.headers || {}) },
    ...init,
  });
  if (res.status === 401) on401();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${path}`);
  return res.json() as Promise<T>;
}

/** Fired after any create/delete of a counted resource so the project sidebar can
 *  refresh its badge counts without a page reload. */
export const COUNTS_CHANGED_EVENT = "forge:counts-changed";

function notifyCounts<T>(p: Promise<T>): Promise<T> {
  return p.then((v) => {
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(COUNTS_CHANGED_EVENT));
    return v;
  });
}

export const api = {
  listProjects: () => json<Project[]>("/v1/projects"),
  getProject: (id: string) => json<Project>(`/v1/projects/${id}`),
  createProject: (body: { name: string; slug?: string; description?: string; config?: Record<string, unknown> }) =>
    json<Project>("/v1/projects", { method: "POST", body: JSON.stringify(body) }),
  listWorkflows: (pid: string) => json<Workflow[]>(`/v1/projects/${pid}/workflows`),
  getWorkflow: (pid: string, wid: string) => json<Workflow>(`/v1/projects/${pid}/workflows/${wid}`),
  validateExecutable: (pid: string, executable: Record<string, unknown>) =>
    json<ValidateResult>(`/v1/projects/${pid}/workflows/validate`, {
      method: "POST",
      body: JSON.stringify({ executable }),
    }),
  createRun: (pid: string, wid: string, input: Record<string, unknown>, threadId?: string, endUser?: Record<string, unknown> | null) =>
    json<{ id: string; status: string; thread_id: string }>(
      `/v1/projects/${pid}/workflows/${wid}/runs`,
      { method: "POST", body: JSON.stringify({ input, ...(threadId ? { thread_id: threadId } : {}), ...(endUser ? { end_user: endUser } : {}) }) },
    ),
  resumeRun: (pid: string, wid: string, rid: string, value: unknown) =>
    json<{ status?: string; messages?: any[]; interrupted?: boolean; error?: string }>(
      `/v1/projects/${pid}/workflows/${wid}/runs/${rid}/resume`,
      { method: "POST", body: JSON.stringify({ value }) },
    ),
  createWorkflow: (pid: string, body: { name: string; description?: string; executable?: Record<string, unknown>; canvas?: Record<string, unknown> }) =>
    notifyCounts(json<Workflow>(`/v1/projects/${pid}/workflows`, { method: "POST", body: JSON.stringify(body) })),
  updateWorkflow: (pid: string, wid: string, body: { name?: string; description?: string }) =>
    json<Workflow>(`/v1/projects/${pid}/workflows/${wid}`, { method: "PATCH", body: JSON.stringify(body) }),
  saveCanvas: (pid: string, wid: string, canvas: Record<string, unknown>, executable: Record<string, unknown>) =>
    json<ValidateResult>(`/v1/projects/${pid}/workflows/${wid}/canvas`, { method: "PUT", body: JSON.stringify({ canvas, executable }) }),
  publishWorkflow: (pid: string, wid: string) =>
    json<Workflow>(`/v1/projects/${pid}/workflows/${wid}/publish`, { method: "POST" }),
  deleteWorkflow: (pid: string, wid: string) =>
    notifyCounts(fetch(`${BASE}/v1/projects/${pid}/workflows/${wid}`, { method: "DELETE", headers: authHeader() })),
  dashboardStats: () => json<DashboardStats>("/v1/stats/dashboard"),
  projectStats: (pid: string) => json<ProjectStats>(`/v1/stats/projects/${pid}`),
  listAgents: (pid: string) => json<Agent[]>(`/v1/projects/${pid}/agents`),
  getAgent: (pid: string, aid: string) => json<Agent>(`/v1/projects/${pid}/agents/${aid}`),
  createAgent: (pid: string, body: { name: string; config: Record<string, unknown> }) =>
    notifyCounts(json<Agent>(`/v1/projects/${pid}/agents`, { method: "POST", body: JSON.stringify(body) })),
  updateAgent: (pid: string, aid: string, body: { name?: string; config?: Record<string, unknown> }) =>
    json<Agent>(`/v1/projects/${pid}/agents/${aid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteAgent: (pid: string, aid: string) =>
    notifyCounts(fetch(`${BASE}/v1/projects/${pid}/agents/${aid}`, { method: "DELETE", headers: authHeader() })),
  listTools: (pid: string) => json<Tool[]>(`/v1/projects/${pid}/tools`),
  getTool: (pid: string, tid: string) => json<Tool>(`/v1/projects/${pid}/tools/${tid}`),
  createTool: (pid: string, body: { name: string; kind: string; config: Record<string, unknown>; auth_provider_id?: string }) =>
    notifyCounts(json<Tool>(`/v1/projects/${pid}/tools`, { method: "POST", body: JSON.stringify(body) })),
  updateTool: (pid: string, tid: string, body: { name?: string; config?: Record<string, unknown>; auth_provider_id?: string | null; enabled?: boolean }) =>
    json<Tool>(`/v1/projects/${pid}/tools/${tid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteTool: (pid: string, tid: string) =>
    notifyCounts(fetch(`${BASE}/v1/projects/${pid}/tools/${tid}`, { method: "DELETE", headers: authHeader() })),
  testTool: (pid: string, tid: string, args: Record<string, unknown>, context?: Record<string, unknown>) =>
    json<ToolTestResult>(`/v1/projects/${pid}/tools/${tid}/test`, {
      method: "POST",
      body: JSON.stringify({ args, context }),
    }),
  // components (Feature 2 - generative UI widgets)
  listComponents: (pid: string) => json<ComponentT[]>(`/v1/projects/${pid}/components`),
  getComponent: (pid: string, cid: string) => json<ComponentT>(`/v1/projects/${pid}/components/${cid}`),
  createComponent: (pid: string, body: Record<string, unknown> & { name: string }) =>
    notifyCounts(json<ComponentT>(`/v1/projects/${pid}/components`, { method: "POST", body: JSON.stringify(body) })),
  updateComponent: (pid: string, cid: string, body: Record<string, unknown>) =>
    json<ComponentT>(`/v1/projects/${pid}/components/${cid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteComponent: (pid: string, cid: string) =>
    notifyCounts(fetch(`${BASE}/v1/projects/${pid}/components/${cid}`, { method: "DELETE", headers: authHeader() })),
  listAuthProviders: (pid: string) => json<AuthProviderT[]>(`/v1/projects/${pid}/auth-providers`),
  createAuthProvider: (pid: string, body: { name: string; kind: string; config: Record<string, unknown>; credentials_ref?: string }) =>
    notifyCounts(json<AuthProviderT>(`/v1/projects/${pid}/auth-providers`, { method: "POST", body: JSON.stringify(body) })),
  updateAuthProvider: (pid: string, aid: string, body: { name?: string; kind?: string; config?: Record<string, unknown>; credentials_ref?: string }) =>
    json<AuthProviderT>(`/v1/projects/${pid}/auth-providers/${aid}`, { method: "PATCH", body: JSON.stringify(body) }),
  testAuthProvider: (pid: string, aid: string, context?: Record<string, unknown>) =>
    json<any>(`/v1/projects/${pid}/auth-providers/${aid}/test`, { method: "POST", body: JSON.stringify({ context }) }),
  listMcpClients: (pid: string) => json<McpClientT[]>(`/v1/projects/${pid}/mcp-clients`),
  createMcpClient: (pid: string, body: { name: string; transport?: string; url?: string; command?: string; args?: any; headers_ref?: string }) =>
    json<McpClientT>(`/v1/projects/${pid}/mcp-clients`, { method: "POST", body: JSON.stringify(body) }),
  updateMcpClient: (pid: string, cid: string, body: Partial<{ name: string; enabled: boolean; disabled_tools: string[]; url: string; headers_ref: string }>) =>
    json<McpClientT>(`/v1/projects/${pid}/mcp-clients/${cid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteMcpClient: (pid: string, cid: string) =>
    json<{ ok: boolean }>(`/v1/projects/${pid}/mcp-clients/${cid}`, { method: "DELETE" }),
  discoverMcpTools: (pid: string, cid: string) =>
    json<{ ok: boolean; tools?: { name: string; description?: string }[]; error?: string }>(`/v1/projects/${pid}/mcp-clients/${cid}/tools`),
  oauthStart: (pid: string, aid: string) =>
    json<{ authorize_url: string }>(`/v1/projects/${pid}/auth-providers/${aid}/oauth/start`, { method: "POST" }),
  oauthStatus: (pid: string, aid: string) =>
    json<{ connected: boolean; expires_at?: number | null; scope?: string | null; has_refresh?: boolean }>(`/v1/projects/${pid}/auth-providers/${aid}/oauth/status`),
  deleteAuthProvider: (pid: string, aid: string) =>
    notifyCounts(fetch(`${BASE}/v1/projects/${pid}/auth-providers/${aid}`, { method: "DELETE", headers: authHeader() })),
  // knowledge
  listSources: (pid: string) => json<KbSource[]>(`/v1/projects/${pid}/knowledge/sources`),
  listFolders: (pid: string) => json<string[]>(`/v1/projects/${pid}/knowledge/folders`),
  addSource: (pid: string, body: { kind: string; name: string; folder?: string; uri?: string; text?: string; chunking_strategy?: string }) =>
    notifyCounts(json<KbSource>(`/v1/projects/${pid}/knowledge/sources`, { method: "POST", body: JSON.stringify(body) })),
  uploadSource: async (pid: string, file: globalThis.File, folder?: string, chunkingStrategy?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (folder) fd.append("folder", folder);
    if (chunkingStrategy) fd.append("chunking_strategy", chunkingStrategy);
    const res = await fetch(`${BASE}/v1/projects/${pid}/knowledge/sources/upload`, { method: "POST", body: fd, headers: authHeader() });
    if (!res.ok) {
      const detail = await res.json().then((d) => d?.detail).catch(() => null);
      throw new Error(detail || `${res.status} ${res.statusText} on upload`);
    }
    if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(COUNTS_CHANGED_EVENT));
    return res.json() as Promise<KbSource>;
  },
  moveSource: (pid: string, sid: string, folder: string) =>
    json<KbSource>(`/v1/projects/${pid}/knowledge/sources/${sid}`, { method: "PATCH", body: JSON.stringify({ folder }) }),
  reingestSource: (pid: string, sid: string, settings?: RechunkSettings) =>
    notifyCounts(json<{ id: string; status: string; chunks: number }>(`/v1/projects/${pid}/knowledge/sources/${sid}/reingest`, { method: "POST", body: JSON.stringify(settings || {}) })),
  rechunkSources: (pid: string, source_ids: string[], settings: RechunkSettings) =>
    notifyCounts(json<{ id: string; status: string; chunks: number }[]>(`/v1/projects/${pid}/knowledge/sources/rechunk`, { method: "POST", body: JSON.stringify({ source_ids, ...settings }) })),
  embeddingHealth: (pid: string) =>
    json<{ current_model: string; current_dim: number; sources: number; needs_reembed: boolean; mismatched: { id: string; name: string; embedded_with: string; dim: number }[] }>(`/v1/projects/${pid}/knowledge/health`),
  deleteSource: (pid: string, sid: string) => notifyCounts(fetch(`${BASE}/v1/projects/${pid}/knowledge/sources/${sid}`, { method: "DELETE", headers: authHeader() })),
  searchKnowledge: (pid: string, query: string, top_k = 5, folders?: string[], hybrid = false, rerank = false) =>
    json<SearchHit[]>(`/v1/projects/${pid}/knowledge/search`, { method: "POST", body: JSON.stringify({ query, top_k, hybrid, rerank, ...(folders?.length ? { folders } : {}) }) }),
  chunkMap: (pid: string, body: { query?: string; folders?: string[]; source_ids?: string[]; limit?: number; hybrid?: boolean; rerank?: boolean; top_k?: number }) =>
    json<ChunkMapResult>(`/v1/projects/${pid}/knowledge/map`, { method: "POST", body: JSON.stringify(body) }),
  // Full text of one chunk, fetched on demand for the chunk-map detail panel (the map response
  // itself carries only a short preview, so the payload stays lean at large point budgets).
  chunkDetail: (pid: string, chunkId: string) =>
    json<ChunkDetail>(`/v1/projects/${pid}/knowledge/chunk?chunk_id=${encodeURIComponent(chunkId)}`),
  dedupeChunks: (pid: string) =>
    json<{ removed: number; groups: number; sources_affected: number; remaining: number }>(`/v1/projects/${pid}/knowledge/dedupe`, { method: "POST" }),
  listQa: (pid: string) => json<QaPair[]>(`/v1/projects/${pid}/qa-pairs`),
  listQaKinds: (pid: string) => json<string[]>(`/v1/projects/${pid}/qa-pairs/kinds`),
  addQa: (pid: string, body: { question: string; answer: string; kind?: string; tags?: string[] }) =>
    json<QaPair>(`/v1/projects/${pid}/qa-pairs`, { method: "POST", body: JSON.stringify(body) }),
  deleteQa: (pid: string, qid: string) => fetch(`${BASE}/v1/projects/${pid}/qa-pairs/${qid}`, { method: "DELETE", headers: authHeader() }),
  // traces + conversations (Traces view)
  listTraces: (pid: string) => json<Trace[]>(`/v1/projects/${pid}/traces`),
  getTrace: (pid: string, trid: string) => json<{ trace: Trace; spans: Span[] }>(`/v1/projects/${pid}/traces/${trid}`),
  listConversations: (pid: string, opts?: { actor?: string; source?: string; status?: string }) => {
    const q = new URLSearchParams();
    if (opts?.actor) q.set("actor", opts.actor);
    if (opts?.source) q.set("source", opts.source);
    if (opts?.status) q.set("status", opts.status);
    const qs = q.toString();
    return json<Conversation[]>(`/v1/projects/${pid}/conversations${qs ? `?${qs}` : ""}`);
  },
  getConversation: (pid: string, threadId: string) =>
    json<ConversationDetail>(`/v1/projects/${pid}/conversations/${encodeURIComponent(threadId)}`),
  conversationFacets: (pid: string) => json<Facets>(`/v1/projects/${pid}/conversations/facets`),
  purgeConversations: (pid: string, olderThanDays: number) =>
    json<{ removed: number }>(`/v1/projects/${pid}/conversations/purge?older_than_days=${olderThanDays}`, { method: "POST" }),
  // secrets
  listSecrets: (pid: string) => json<Secret[]>(`/v1/projects/${pid}/secrets`),
  createSecret: (pid: string, body: { name: string; value: unknown; kind?: string }) =>
    json<Secret>(`/v1/projects/${pid}/secrets`, { method: "POST", body: JSON.stringify(body) }),
  secretUsage: (pid: string, name: string) =>
    json<{ count: number; references: { type: string; label: string }[] }>(`/v1/projects/${pid}/secrets/${encodeURIComponent(name)}/usage`),
  deleteSecret: (pid: string, name: string, force = false) =>
    fetch(`${BASE}/v1/projects/${pid}/secrets/${encodeURIComponent(name)}${force ? "?force=true" : ""}`, { method: "DELETE", headers: authHeader() }),
  // project
  updateProject: (pid: string, body: { name?: string; description?: string; config?: Record<string, unknown> }) =>
    json<Project>(`/v1/projects/${pid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteProject: async (pid: string) => {
    const res = await fetch(`${BASE}/v1/projects/${pid}`, { method: "DELETE", headers: authHeader() });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText} on /v1/projects/${pid}`);
  },
  listNodeTypes: () => json<NodeType[]>("/v1/node-types"),
  runStreamUrl: (pid: string, wid: string, runId: string) =>
    sseUrl(`/v1/projects/${pid}/workflows/${wid}/runs/${runId}/stream`),
  assistantStreamUrl: (pid: string) => sseUrl(`/v1/projects/${pid}/assistant/stream`),
  assistantResumeUrl: (pid: string) => sseUrl(`/v1/projects/${pid}/assistant/resume`),
  // auth + team
  register: (email: string, password: string, workspace_name?: string) =>
    json<AuthResult>("/v1/auth/register", { method: "POST", body: JSON.stringify({ email, password, workspace_name }) }),
  login: (email: string, password: string) =>
    json<AuthResult>("/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  me: () => json<MeResult>("/v1/auth/me"),
  inviteInfo: (token: string) => json<{ email: string; role: string }>(`/v1/auth/invite-info?token=${encodeURIComponent(token)}`),
  acceptInvite: (token: string, password: string) =>
    json<AuthResult>("/v1/auth/accept-invite", { method: "POST", body: JSON.stringify({ token, password }) }),
  listTeam: () => json<TeamMember[]>("/v1/team/members"),
  listAudit: (projectId?: string) => json<AuditEntry[]>(`/v1/audit${projectId ? `?project_id=${projectId}` : ""}`),
  listPricing: () => json<Record<string, { input_per_1m: number; output_per_1m: number }>>("/v1/pricing"),
  setPricing: (model: string, body: { input_per_1m: number; output_per_1m: number }) =>
    json<any>(`/v1/pricing/${encodeURIComponent(model)}`, { method: "PUT", body: JSON.stringify(body) }),
  inviteMember: (body: { email: string; role?: string; password?: string }) =>
    json<InviteResult>("/v1/team/members", { method: "POST", body: JSON.stringify(body) }),
  updateMember: (uid: string, body: { role?: string; status?: string }) =>
    json<TeamMember>(`/v1/team/members/${uid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deactivateMember: (uid: string) =>
    json<{ ok: boolean }>(`/v1/team/members/${uid}`, { method: "DELETE" }),
  // channels
  listChannels: (pid: string) => json<Channel[]>(`/v1/projects/${pid}/channels`),
  createChannel: (pid: string, body: { type: string; name: string; workflow_id?: string; config?: Record<string, any> }) =>
    json<Channel>(`/v1/projects/${pid}/channels`, { method: "POST", body: JSON.stringify(body) }),
  updateChannel: (pid: string, cid: string, body: { name?: string; workflow_id?: string; config?: Record<string, any>; enabled?: boolean }) =>
    json<Channel>(`/v1/projects/${pid}/channels/${cid}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteChannel: (pid: string, cid: string) =>
    json<{ ok: boolean }>(`/v1/projects/${pid}/channels/${cid}`, { method: "DELETE" }),
  // triggers
  listTriggers: (pid: string) => json<Trigger[]>(`/v1/projects/${pid}/triggers`),
  // datasets / eval
  listDatasets: (pid: string) => json<Dataset[]>(`/v1/projects/${pid}/datasets`),
  createDataset: (pid: string, body: { name: string; workflow_id?: string; score_mode?: string; items?: any[] }) =>
    json<Dataset>(`/v1/projects/${pid}/datasets`, { method: "POST", body: JSON.stringify(body) }),
  runDataset: (pid: string, did: string) =>
    json<EvalReport>(`/v1/projects/${pid}/datasets/${did}/run`, { method: "POST" }),
  deleteDataset: (pid: string, did: string) =>
    json<{ ok: boolean }>(`/v1/projects/${pid}/datasets/${did}`, { method: "DELETE" }),
  // handoff inbox
  listHandoffs: (pid: string, status = "open") => json<Handoff[]>(`/v1/projects/${pid}/handoffs?status=${status}`),
  replyHandoff: (pid: string, hid: string, message: string) =>
    json<{ ok: boolean }>(`/v1/projects/${pid}/handoffs/${hid}/reply`, { method: "POST", body: JSON.stringify({ message }) }),
  // embed (widget)
  getEmbed: (pid: string) => json<EmbedSettings>(`/v1/projects/${pid}/embed`),
  setEmbed: (pid: string, body: { enabled: boolean; allowed_origins: string[]; workflow_id?: string | null }) =>
    json<EmbedSettings>(`/v1/projects/${pid}/embed`, { method: "PUT", body: JSON.stringify(body) }),
};

export interface InviteResult extends TeamMember { email_sent: boolean; invite_url?: string; }
export interface Channel { id: string; type: string; name: string; workflow_id?: string | null; enabled: boolean; config: Record<string, any>; key?: string | null; inbound_url?: string; messaging_endpoint?: string; }
export interface Trigger { id: string; workflow_id: string; node_id: string; kind: string; enabled: boolean; config: Record<string, any>; webhook_url?: string; last_fired_at?: string | null; }
export interface Dataset { id: string; name: string; workflow_id?: string | null; score_mode: string; items: any[]; n_items: number; last_pass_rate?: number | null; }
export interface EvalReport { summary: { total: number; passed: number; pass_rate: number }; results: { input: string; expected: string; answer: string; passed: boolean; reason?: string | null }[]; }
export interface Handoff { id: string; run_id: string; workflow_id?: string | null; customer?: string | null; customer_message?: string | null; reason?: string | null; status: string; at?: string | null; }
export interface EmbedSettings { enabled: boolean; allowed_origins: string[]; workflow_id?: string | null; publishable_key?: string | null; embed_src?: string | null; }

export interface MeResult { id: string; email: string | null; role: string; tenant_id: string; is_fallback: boolean; }
export interface AuthResult { access_token: string; refresh_token: string; user: { id: string; email: string; role: string }; }
export interface TeamMember { id: string; email: string; role: string; status: string; tenant_id: string; }
export interface McpClientT { id: string; name: string; transport: string; url?: string | null; command?: string | null; args?: any; headers_ref?: string | null; enabled: boolean; disabled_tools?: string[]; }
export interface AuditEntry { id: string; action: string; actor_email?: string | null; resource_type?: string | null; ip?: string | null; status: string; at?: string | null; }

export interface SSEFrame {
  event: string;
  data: any;
}

/** Open an SSE stream and invoke `onFrame` per event. Supports GET (default) or POST
 *  (pass init.method/body) - the backend assistant endpoint streams over POST. */
export async function openSSE(
  url: string,
  onFrame: (frame: SSEFrame) => void,
  init?: RequestInit,
): Promise<void> {
  const res = await fetch(url, {
    ...init,
    cache: "no-store",
    headers: { Accept: "text/event-stream", "Cache-Control": "no-cache", ...authHeader(), ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} on ${url}`);
  if (!res.body) throw new Error("No response body for SSE");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // SSE frames are separated by a blank line - handle both \n\n and \r\n\r\n.
    const chunks = buffer.split(/\r?\n\r?\n/);
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of chunk.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length) {
        const raw = dataLines.join("\n");
        let data: any = raw;
        try {
          data = JSON.parse(raw);
        } catch {
          /* keep raw string */
        }
        onFrame({ event, data });
      }
    }
  }
}
