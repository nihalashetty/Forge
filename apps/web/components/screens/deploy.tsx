"use client";
/* Connect (MCP) screen - expose this project's tools as an MCP server for external clients.
   (Consuming external MCP servers lives in the BUILD → External MCP tab.) */
import { ReactNode, useEffect, useState } from "react";
import { Icon } from "../icons";
import { CodeBlock, Field, Segmented, Toggle } from "../primitives";
import { api, type McpToken, type ToolSet } from "@/lib/api";
import { EmbedPanel } from "./embed";

/* Collapsible detail section - keeps the deep integration reference tucked away so the
   Connect screen stays scannable; expand only what you need. */
function Collapse({ title, sub, defaultOpen = false, children }: { title: string; sub?: string; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="card" style={{ padding: 0, marginBottom: 10, overflow: "hidden" }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="row spread"
        style={{ width: "100%", background: "none", border: "none", cursor: "pointer", padding: "12px 16px", textAlign: "left", fontFamily: "var(--font-ui)", color: "inherit" }}
      >
        <div>
          <div className="t-h3">{title}</div>
          {sub && <div className="t-caption fg-2" style={{ marginTop: 2 }}>{sub}</div>}
        </div>
        <Icon name={open ? "chevdown" : "chevright"} size={16} style={{ color: "var(--fg-2)", flex: "none", marginLeft: 12 }} />
      </button>
      {open && <div style={{ padding: "0 16px 16px" }}>{children}</div>}
    </div>
  );
}

/* One SSE frame documented in the streaming reference: event name + what its data carries. */
function FrameRow({ event, children }: { event: string; children: ReactNode }) {
  return (
    <div className="row gap2" style={{ alignItems: "baseline", padding: "5px 0", borderTop: "1px solid var(--line)" }}>
      <span className="mono-sm" style={{ minWidth: 92, flex: "none", color: "var(--fg-2)" }}>{event}</span>
      <span className="t-caption fg-1">{children}</span>
    </div>
  );
}

/* Left secondary-nav sections (mirrors the Settings screen layout) so the Connect screen
   is navigable instead of one long scroll. */
type ConnSection = "run" | "reference" | "mcp" | "embed";
const CONN_SECTIONS: { id: ConnSection; label: string; icon: string; sub: string; child?: boolean }[] = [
  { id: "run", label: "Run API", icon: "bolt", sub: "Call this project's workflow from your backend over one endpoint." },
  { id: "reference", label: "Integration reference", icon: "traces", sub: "The wire format for streaming and non-streaming responses.", child: true },
  { id: "mcp", label: "MCP server", icon: "connect", sub: "Expose this project's tools to Claude Desktop, Cursor, or VS Code." },
  { id: "embed", label: "Embed", icon: "grid", sub: "Drop this project's chatbot into any website as a widget." },
];

/* ============ CONNECT (MCP) ============ */
export function ConnectScreen({ project }: { project: any }) {
  const [section, setSection] = useState<ConnSection>("run");
  const [tools, setTools] = useState<any[]>([]);
  const [toolSets, setToolSets] = useState<ToolSet[]>([]);
  const [tsSave, setTsSave] = useState<"idle" | "saving" | "saved">("idle");
  const [excluded, setExcluded] = useState<string[]>([]);
  const [openSets, setOpenSets] = useState<Set<string>>(new Set());
  const [mcpTokens, setMcpTokens] = useState<McpToken[]>([]);
  const [newToken, setNewToken] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  const [credTab, setCredTab] = useState<"key" | "pat">("key"); // which credential to paste - it's either/or
  // Project-level MCP tools (mirror the server's project.config flags in mcp_server.py): the whole
  // workflow as one tool, plus knowledge-base + curated-Q&A search. Independent of toolsets.
  const [exposeWf, setExposeWf] = useState(false);
  const [wfToolName, setWfToolName] = useState("run_workflow");
  const [exposeKnowledge, setExposeKnowledge] = useState(false);
  const [exposeFaq, setExposeFaq] = useState(false);
  const [cfgSave, setCfgSave] = useState<"idle" | "saving" | "saved">("idle");
  // Run-API panel: pick the workflow this project's API runs (a saved setting) + the
  // backend-facing base URL, then show the one ready-to-copy endpoint.
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [wfId, setWfId] = useState("");
  const [apiSave, setApiSave] = useState<"idle" | "saving" | "saved">("idle");
  const [apiBase, setApiBase] = useState(
    (process.env.NEXT_PUBLIC_FORGE_API_URL || "http://localhost:8000").replace(/\/$/, ""),
  );
  useEffect(() => {
    if (!project?.id) return;
    api.listTools(project.id).then(setTools).catch(() => {});
    api.listToolSets(project.id).then(setToolSets).catch(() => {});
    api.listMcpTokens(project.id).then(setMcpTokens).catch(() => {});
  }, [project?.id]);
  useEffect(() => {
    if (!project?.id) return;
    Promise.all([api.getProject(project.id), api.listWorkflows(project.id)]).then(([p, ws]) => {
      setApiKey((p.config as any)?.mcp_api_key || "");
      setExcluded(((p.config as any)?.mcp_excluded_tools as string[]) || []);
      const cfg = (p.config as any) || {};
      setExposeWf(!!cfg.mcp_expose_workflow);
      setWfToolName(cfg.mcp_workflow_tool_name || "run_workflow");
      setExposeKnowledge(!!cfg.mcp_expose_knowledge);
      setExposeFaq(!!cfg.mcp_expose_faq);
      setWorkflows(ws);
      // Default the picker to the saved API workflow; else the active one, else the first.
      const saved = (p.config as any)?.api_workflow_id;
      const chosen = ws.find((w: any) => w.id === saved) || ws.find((w: any) => w.status === "active") || ws[0];
      setWfId(chosen ? chosen.id : "");
    }).catch(() => {});
  }, [project?.id]);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const url = `${origin}/api/forge/v1/mcp/${project?.id || "<project>"}`;
  const claudeConfig = JSON.stringify({ mcpServers: { [project?.slug || "forge"]: { url, headers: { Authorization: "Bearer <PAT or API key>" } } } }, null, 2);
  // The MCP surface = enabled tools of EXPOSED sets, minus individually excluded ones (mirrors
  // the server; see mcp_server.py._exposed_names).
  const toolById = new Map(tools.map((t) => [t.id, t]));
  const exposedToolIds = new Set(toolSets.filter((s) => s.exposed).flatMap((s) => s.tool_ids).filter((id) => !excluded.includes(id)));
  const exposedTools = tools.filter((t) => t.enabled && exposedToolIds.has(t.id));
  // Project-level tools published alongside the toolset tools (see mcp_server.py._capability_tools
  // + _workflow_tool_name). Shown in the "Currently exposed" summary so the whole surface is visible.
  const projectTools = [
    ...(exposeWf ? [{ name: wfToolName || "run_workflow", kind: "workflow" }] : []),
    ...(exposeKnowledge ? [{ name: "search_knowledge_base", kind: "knowledge" }] : []),
    ...(exposeFaq ? [{ name: "lookup_faq", kind: "qa" }] : []),
  ];

  // Run API (server-to-server): a backend hits the Forge API DIRECTLY, not the web proxy.
  // ONE endpoint per project - it runs the workflow chosen above; `stream` is the only
  // per-request knob, and HITL flows through the same call (workflow-driven).
  const base = (apiBase || "http://localhost:8000").replace(/\/$/, "");
  const pid = project?.id || "<projectId>";
  const runUrl = `${base}/v1/projects/${pid}/run`;
  const curl = [
    "# ONE endpoint. Auth with the service token; pass the caller's per-user secrets in",
    "# X-Forge-Context (used by tools as {{ctx.*}}) - never put secrets in the body.",
    `curl -sN "${runUrl}" \\`,
    `  -H "Authorization: Bearer $FORGE_SERVICE_API_TOKEN" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H 'X-Forge-Context: {"jsessionid":"<user session>","csrf":"<user csrf>"}' \\`,
    `  -H "Accept: text/event-stream" \\`,
    `  -d '{"input":{"messages":[{"role":"user","content":"hello"}]},"end_user":{"id":"user-123"},"stream":true}'`,
    '# -> SSE frames; the "ready" frame gives you a thread_id to continue the conversation.',
    "",
    "# stream:false returns a single JSON reply instead of SSE.",
    "# answer a human-in-the-loop step the workflow raised (reuse the thread_id):",
    `#   -d '{"thread_id":"<thread>","resume":{"value":"approve"}}'`,
  ].join("\n");

  // --- Reference payloads for the "How the integration works" section ---
  // A trimmed SSE transcript: one `ready` frame, a couple of token deltas, then `done`.
  const sampleStream = [
    "event: ready",
    'data: {"run_id":"run_a1b2","thread_id":"thr_x9"}',
    "",
    "event: node_start",
    'data: {"node":"assistant"}',
    "",
    "event: messages",
    'data: {"content":"Hi","type":"AIMessageChunk","node":"assistant"}',
    "",
    "event: messages",
    'data: {"content":" there!","type":"AIMessageChunk","node":"assistant"}',
    "",
    "event: done",
    'data: {"status":"done","answer":"Hi there!","total_tokens":812,"total_cost_usd":0.0021}',
  ].join("\n");
  // The single object returned when stream:false (thread_id is added by the endpoint).
  const sampleJson = JSON.stringify(
    {
      run_id: "run_a1b2", thread_id: "thr_x9", status: "done",
      answer: "Hi there!", components: [], interrupted: false, interrupts: [],
      total_tokens: 812, total_cost_usd: 0.0021,
    },
    null,
    2,
  );

  async function saveApiWorkflow(next: string) {
    setWfId(next);
    setApiSave("saving");
    const p = await api.getProject(project.id);
    await api.updateProject(project.id, { config: { ...(p.config || {}), api_workflow_id: next || undefined } });
    setApiSave("saved");
    setTimeout(() => setApiSave("idle"), 1200);
  }

  async function saveKey(next: string) {
    setApiKey(next); setSave("saving");
    const p = await api.getProject(project.id);
    await api.updateProject(project.id, { config: { ...(p.config || {}), mcp_api_key: next || undefined } });
    setSave("saved"); setTimeout(() => setSave("idle"), 1200);
  }
  // Merge a patch into project.config (undefined values drop the key). Used by the project-tool
  // toggles; re-reads config first so concurrent edits to other keys aren't clobbered.
  async function saveCfg(patch: Record<string, unknown>) {
    setCfgSave("saving");
    const p = await api.getProject(project.id);
    await api.updateProject(project.id, { config: { ...(p.config || {}), ...patch } });
    setCfgSave("saved"); setTimeout(() => setCfgSave("idle"), 1200);
  }
  const genKey = () => {
    // A shared MCP API key is a credential, so use the CSPRNG (crypto.getRandomValues),
    // never Math.random() - the latter is predictable and unsafe for secret material.
    const bytes = new Uint8Array(24);
    crypto.getRandomValues(bytes);
    saveKey("fmcp_" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(""));
  };

  // Publish a subset of tool sets on the base MCP endpoint (project.config.mcp_published_toolsets,
  // a list of set slugs). Empty => the base endpoint exposes every enabled tool (prior behavior).
  async function toggleExposed(ts: ToolSet) {
    setTsSave("saving");
    const updated = await api.updateToolSet(project.id, ts.id, { exposed: !ts.exposed });
    setToolSets((prev) => prev.map((x) => (x.id === ts.id ? updated : x)));
    setTsSave("saved");
    setTimeout(() => setTsSave("idle"), 1200);
  }
  async function saveExcluded(next: string[]) {
    setExcluded(next);
    setTsSave("saving");
    const p = await api.getProject(project.id);
    await api.updateProject(project.id, { config: { ...(p.config || {}), mcp_excluded_tools: next.length ? next : undefined } });
    setTsSave("saved");
    setTimeout(() => setTsSave("idle"), 1200);
  }
  const toggleToolExcluded = (tid: string) =>
    saveExcluded(excluded.includes(tid) ? excluded.filter((x) => x !== tid) : [...excluded, tid]);
  const toggleOpenSet = (id: string) =>
    setOpenSets((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  async function genToken() {
    const t = await api.createMcpToken(project.id, {});
    setNewToken(t.token || "");
    api.listMcpTokens(project.id).then(setMcpTokens).catch(() => {});
  }
  async function revokeToken(id: string) {
    await api.revokeMcpToken(project.id, id);
    setMcpTokens((prev) => prev.filter((t) => t.id !== id));
  }

  const activeMeta = CONN_SECTIONS.find((s) => s.id === section)!;
  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        {/* secondary nav (same pattern as Settings) */}
        <nav className="scroll-y" style={{ width: 224, flex: "none", borderRight: "1px solid var(--line)", background: "var(--bg-1)", padding: 10 }}>
          <div className="t-micro" style={{ padding: "6px 8px 8px" }}>Connect</div>
          {CONN_SECTIONS.map((s) => {
            const on = section === s.id;
            return (
              <button key={s.id} onClick={() => setSection(s.id)} className={"sidenav-item" + (on ? " active" : "")}
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", height: 34, padding: "0 10px", paddingLeft: s.child ? 20 : 10, marginBottom: 1, borderRadius: 7, border: "none", cursor: "pointer", textAlign: "left", color: on ? "var(--accent)" : "var(--fg-1)", fontSize: 13, fontWeight: on ? 600 : 500, fontFamily: "var(--font-ui)" }}>
                {s.child && <span aria-hidden style={{ color: "var(--fg-2)", fontSize: 13, lineHeight: 1, marginRight: -4, flex: "none" }}>└</span>}
                <Icon name={s.icon as any} size={16} style={{ flex: "none" }} />
                <span className="grow truncate">{s.label}</span>
              </button>
            );
          })}
        </nav>

        {/* content */}
        <div className="scroll-y grow" style={{ minWidth: 0 }}>
          <div className="fade-up" style={{ maxWidth: 960, margin: "0 auto", padding: "24px 28px" }}>
            <div style={{ marginBottom: 18 }}>
              <div className="t-display">{activeMeta.label}</div>
              <div className="fg-1" style={{ marginTop: 3 }}>{activeMeta.sub}</div>
            </div>

            {section === "run" && (
              <>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row gap3" style={{ flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 240 }}>
              <Field label="Forge API base URL" help="Where your backend reaches the Forge API directly (NOT the web console). Dev: http://localhost:8000. From another container on Forge's network: http://api:8000.">
                <input className="input mono" value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://localhost:8000" />
              </Field>
            </div>
            <div style={{ flex: 1, minWidth: 240 }}>
              <Field label="Workflow this API runs" help="Saved on the project. The /run endpoint always executes this workflow — callers never pick one.">
                <div className="row gap2" style={{ alignItems: "center" }}>
                  <select className="select" style={{ flex: 1 }} value={wfId} onChange={(e) => saveApiWorkflow(e.target.value)}>
                    {workflows.length === 0 && <option value="">No workflows yet</option>}
                    {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}{w.status !== "active" ? ` (${w.status})` : ""}</option>)}
                  </select>
                  <span className="t-caption fg-2" style={{ minWidth: 52 }}>{apiSave === "saving" ? "Saving…" : apiSave === "saved" ? "Saved ✓" : ""}</span>
                </div>
              </Field>
            </div>
          </div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Endpoint · POST</div>
          <CodeBlock code={runUrl} />
          <div className="field-help" style={{ marginTop: 8 }}>
            One call does everything. Send <span className="mono-sm">{"{ input, stream }"}</span> for a new turn (reuse the returned <span className="mono-sm">thread_id</span> to continue a conversation), or <span className="mono-sm">{"{ thread_id, resume }"}</span> to answer a human-in-the-loop step the workflow raised. <span className="mono-sm">stream: true</span> streams SSE (tokens, steps, tools); <span className="mono-sm">false</span> returns one JSON reply. Authenticate with <span className="mono-sm">Authorization: Bearer &lt;FORGE_SERVICE_API_TOKEN&gt;</span>; pass the caller&apos;s per-user session/CSRF as <span className="mono-sm">X-Forge-Context</span> so tools act on their behalf — never put secrets in the body.
          </div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Example (curl)</div>
          <CodeBlock code={curl} />
        </div>
              </>
            )}

            {section === "reference" && (
              <>
        <div className="fg-2 t-caption" style={{ marginBottom: 10 }}>The same endpoint responds two ways depending on the <span className="mono-sm">stream</span> flag. Expand a section for the wire format.</div>

        <Collapse title="Streaming — stream: true" sub="Server-Sent Events (text/event-stream): tokens, steps and tool activity as they happen." defaultOpen>
          <div className="field-help" style={{ margin: "10px 0" }}>
            The response stays open and emits <span className="mono-sm">event:</span> / <span className="mono-sm">data:</span> frames (data is JSON). Read it with any SSE client and keep the connection until a <span className="mono-sm">done</span>, <span className="mono-sm">error</span> or <span className="mono-sm">interrupt</span> frame arrives. Build the reply by concatenating each <span className="mono-sm">messages</span> frame&apos;s <span className="mono-sm">content</span> in order; the final <span className="mono-sm">done</span> frame also carries the whole <span className="mono-sm">answer</span> (authoritative — it covers non-LLM steps that never stream tokens).
          </div>
          <div style={{ margin: "10px 0" }}>
            <FrameRow event="ready">First frame. <span className="mono-sm">{"{ run_id, thread_id }"}</span> — save <span className="mono-sm">thread_id</span> to continue this conversation.</FrameRow>
            <FrameRow event="node_start">A workflow step began. <span className="mono-sm">{"{ node }"}</span>.</FrameRow>
            <FrameRow event="messages">Assistant answer token delta. <span className="mono-sm">{"{ content, type, node }"}</span> — concatenate <span className="mono-sm">content</span>.</FrameRow>
            <FrameRow event="updates">Top-level step output/state change (nested sub-steps are omitted to keep it clean).</FrameRow>
            <FrameRow event="custom">App-emitted data a node chose to stream (e.g. rich components).</FrameRow>
            <FrameRow event="interrupt">A human-in-the-loop step is waiting. Answer it with <span className="mono-sm">{"{ thread_id, resume }"}</span>.</FrameRow>
            <FrameRow event="node_error">A step failed. <span className="mono-sm">{"{ node, message }"}</span>.</FrameRow>
            <FrameRow event="done">Terminal. <span className="mono-sm">{"{ status, answer, total_tokens, total_cost_usd }"}</span>.</FrameRow>
            <FrameRow event="error">Terminal error. <span className="mono-sm">{"{ message }"}</span>.</FrameRow>
          </div>
          <CodeBlock code={sampleStream} />
        </Collapse>

        <Collapse title="Non-streaming — stream: false" sub="One JSON object, returned once the run finishes.">
          <div className="field-help" style={{ margin: "10px 0" }}>
            Simplest to consume: a normal <span className="mono-sm">application/json</span> response after the run completes. <span className="mono-sm">status</span> is one of <span className="mono-sm">done · interrupted · error · busy</span>. When <span className="mono-sm">interrupted</span>, <span className="mono-sm">interrupts</span> holds the human-in-the-loop payload — answer it by re-calling with <span className="mono-sm">{"{ thread_id, resume }"}</span>. <span className="mono-sm">answer</span> is the full reply; <span className="mono-sm">components</span> carries any structured UI a node produced.
          </div>
          <CodeBlock code={sampleJson} />
        </Collapse>

        <Collapse title="Continue a conversation & human-in-the-loop" sub="Reuse thread_id across turns; resume interrupts on the same thread.">
          <div className="field-help" style={{ margin: "10px 0" }}>
            Chat memory is keyed by <span className="mono-sm">thread_id</span>. Take it from the <span className="mono-sm">ready</span> frame (streaming) or the JSON reply (non-streaming) and send it back in the next request&apos;s body to keep context across turns — omit it to start fresh. To answer an interrupt the workflow raised, POST the same endpoint with the interrupted thread and a resume value:
          </div>
          <CodeBlock code={'{ "thread_id": "thr_x9", "resume": { "value": "approve" } }'} />
        </Collapse>

        <Collapse title="Per-user identity — X-Forge-Context" sub="Pass the caller's secrets out-of-band; tools read them as {{ctx.*}}.">
          <div className="field-help" style={{ margin: "10px 0" }}>
            Authenticate the call itself with the service token (<span className="mono-sm">Authorization: Bearer &lt;FORGE_SERVICE_API_TOKEN&gt;</span>). Anything the workflow&apos;s tools need to act <em>as the end user</em> — a session cookie, CSRF token, downstream bearer — goes in the <span className="mono-sm">X-Forge-Context</span> header as a JSON object, and tools reference it with <span className="mono-sm">{"{{ctx.*}}"}</span>. It is never written to the body, never persisted, and never echoed back.
          </div>
          <CodeBlock code={"X-Forge-Context: {\"jsessionid\":\"<user session>\",\"csrf\":\"<user csrf>\"}"} />
          <div className="field-help" style={{ marginTop: 8 }}>Identify the end user for quotas/analytics with <span className="mono-sm">{"{ \"end_user\": { \"id\": \"user-123\" } }"}</span> in the body.</div>
        </Collapse>

              </>
            )}

            {section === "mcp" && (
              <>
        <Collapse title="How to use this MCP server" sub="Publish toolsets, connect a client, and pick how each user authenticates.">
          <ol className="field-help" style={{ margin: "10px 0", paddingLeft: 18, lineHeight: 1.75 }}>
            <li><b>Curate what&apos;s exposed.</b> Everything is published by default over the single endpoint below — under <b>Toolsets</b>, untick a whole set, or open a set and untick individual tools, to leave them out. (MCP shows the client a flat tool list; toolsets are just how you organize it.)</li>
            <li><b>Set an API key</b> below — the shared server-to-server credential (<span className="mono-sm">Authorization: Bearer &lt;key&gt;</span>). Without a key the server is closed.</li>
            <li><b>Add the endpoint</b> to your MCP client (Claude Desktop, Cursor, VS Code) with the config block below — that one URL is all a client needs.</li>
            <li><b>Authenticate each user</b> — pick one: a <b>personal access token</b> (each user generates one below and pastes it into their client); <b>OAuth 2.1</b> (when enabled, the client discovers Forge and the user logs in — nothing to copy); or <b>your own backend</b> (mint a session token / use Connect and pass the user&apos;s session in <span className="mono-sm">X-Forge-Context</span>).</li>
            <li><b>Act as the user downstream.</b> So a tool calls <em>your</em> app as that user, connect each user&apos;s account under <b>Auth providers</b> (Forge stores a per-user credential) or inject their session via <span className="mono-sm">{"{{ctx.*}}"}</span>. The MCP token itself is never forwarded. Your app owns its users &amp; sessions; Forge only carries the identity.</li>
          </ol>
        </Collapse>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>MCP endpoint</div>
          <CodeBlock code={url} />
          <div className="field-help">JSON-RPC over HTTP (initialize / tools/list / tools/call) · authenticate with the API key below.</div>
        </div>
        {/* Authentication: the API key and personal access token are alternatives - a client
            pastes ONE of them as its Bearer token - so they live behind a two-tab switch. */}
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row spread" style={{ marginBottom: 10, alignItems: "center" }}>
            <div className="t-h3">Authentication</div>
            <Segmented options={[{ value: "key", label: "API key" }, { value: "pat", label: "Personal access token" }]} value={credTab} onChange={(v) => setCredTab(v as "key" | "pat")} />
          </div>
          <div className="field-help" style={{ marginBottom: 12 }}>Use <b>one</b> of these as the <span className="mono-sm">Bearer</span> token in the config below — the shared <b>API key</b> (server-to-server, one identity) <b>or</b> your own <b>personal access token</b> (acts as you).</div>
          {credTab === "key" ? (
            <div className="fade-in">
              <div className="row gap2">
                <input className="input mono" style={{ flex: 1 }} type="text" value={apiKey} onChange={(e) => setApiKey(e.target.value)} onBlur={(e) => saveKey(e.target.value)} placeholder="Set a key to expose the server" />
                <button className="btn btn-secondary btn-sm" onClick={genKey}><Icon name="refresh" size={13} />Generate</button>
                <span className="t-caption fg-2" style={{ alignSelf: "center", whiteSpace: "nowrap" }}>{save === "saving" ? "Saving…" : save === "saved" ? "Saved ✓" : ""}</span>
              </div>
              <div className="field-help">Shared server-to-server credential. Without a key the endpoint is closed to everyone.</div>
            </div>
          ) : (
            <div className="fade-in">
              <div className="field-help" style={{ marginBottom: 10 }}>
                A per-user token to paste into your own MCP client instead of the shared key — the server then acts as <b>you</b> (your entitlements). Shown once on creation; store it safely.
              </div>
              {newToken && (
                <div className="card" style={{ padding: 10, marginBottom: 10, background: "var(--bg-2)" }}>
                  <div className="t-caption fg-2" style={{ marginBottom: 4 }}>New token — copy now, it won&apos;t be shown again:</div>
                  <CodeBlock code={newToken} />
                </div>
              )}
              <div className="row gap2" style={{ marginBottom: 10 }}>
                <button className="btn btn-secondary btn-sm" onClick={genToken}><Icon name="plus" size={13} />Generate token</button>
              </div>
              <div className="col gap1">
                {mcpTokens.map((t) => (
                  <div key={t.id} className="row spread" style={{ padding: "6px 0", borderTop: "1px solid var(--line)" }}>
                    <div className="row gap2" style={{ alignItems: "center", minWidth: 0 }}>
                      <Icon name="auth" size={13} style={{ color: "var(--fg-2)" }} />
                      <span className="mono-sm truncate">{t.name}</span>
                      <span className="typechip">{t.prefix}…</span>
                      {t.status !== "active" && <span className="pill pill-muted" style={{ height: 16 }}>{t.status}</span>}
                    </div>
                    {t.status === "active" && <button className="iconbtn" title="Revoke token" onClick={() => revokeToken(t.id)}><Icon name="trash" size={14} /></button>}
                  </div>
                ))}
                {mcpTokens.length === 0 && <div className="fg-2 t-caption">No personal tokens yet.</div>}
              </div>
            </div>
          )}
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Claude Desktop / Cursor config</div>
          <CodeBlock code={claudeConfig} />
        </div>
        {/* Project-level tools: the whole workflow, knowledge-base search, and curated Q&A lookup,
            each a project.config flag on the server (mcp_server.py). Independent of toolsets. */}
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row spread" style={{ marginBottom: 8 }}>
            <div className="t-h3">Project tools</div>
            <span className="t-caption fg-2">{cfgSave === "saving" ? "Saving…" : cfgSave === "saved" ? "Saved ✓" : ""}</span>
          </div>
          <div className="field-help" style={{ marginBottom: 12 }}>
            Publish this project&apos;s built-in capabilities as MCP tools, independent of toolsets. Knowledge and Q&amp;A search this project&apos;s knowledge base; the workflow tool runs the workflow chosen under <b>Run API</b>.
          </div>
          <div className="col gap1">
            <div className="row spread" style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 9, gap: 12, alignItems: "flex-start" }}>
              <div className="col" style={{ gap: 2, minWidth: 0 }}>
                <span className="mono-sm" style={{ fontWeight: 700, color: exposeWf ? "var(--fg-0)" : "var(--fg-2)" }}>{wfToolName || "run_workflow"}</span>
                <span className="t-caption fg-2">Run the whole configured workflow as a single tool.</span>
                {exposeWf && (
                  <div className="row gap2" style={{ marginTop: 6, alignItems: "center" }}>
                    <span className="t-caption fg-2">Tool name</span>
                    <input className="input mono" style={{ maxWidth: 240, height: 30 }} value={wfToolName}
                      onChange={(e) => setWfToolName(e.target.value)}
                      onBlur={(e) => { const v = e.target.value.trim().replace(/[^a-zA-Z0-9_-]/g, "_"); setWfToolName(v || "run_workflow"); saveCfg({ mcp_workflow_tool_name: v && v !== "run_workflow" ? v : undefined }); }}
                      placeholder="run_workflow" />
                  </div>
                )}
              </div>
              <Toggle on={exposeWf} onChange={(v) => { setExposeWf(v); saveCfg({ mcp_expose_workflow: v || undefined }); }} />
            </div>
            <div className="row spread" style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 9, gap: 12, alignItems: "flex-start" }}>
              <div className="col" style={{ gap: 2, minWidth: 0 }}>
                <span className="mono-sm" style={{ fontWeight: 700, color: exposeKnowledge ? "var(--fg-0)" : "var(--fg-2)" }}>search_knowledge_base</span>
                <span className="t-caption fg-2">Vector search over this project&apos;s knowledge-base documents.</span>
              </div>
              <Toggle on={exposeKnowledge} onChange={(v) => { setExposeKnowledge(v); saveCfg({ mcp_expose_knowledge: v || undefined }); }} />
            </div>
            <div className="row spread" style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 9, gap: 12, alignItems: "flex-start" }}>
              <div className="col" style={{ gap: 2, minWidth: 0 }}>
                <span className="mono-sm" style={{ fontWeight: 700, color: exposeFaq ? "var(--fg-0)" : "var(--fg-2)" }}>lookup_faq</span>
                <span className="t-caption fg-2">Semantic match over this project&apos;s curated Q&amp;A / FAQ pairs.</span>
              </div>
              <Toggle on={exposeFaq} onChange={(v) => { setExposeFaq(v); saveCfg({ mcp_expose_faq: v || undefined }); }} />
            </div>
          </div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row spread" style={{ marginBottom: 8 }}>
            <div className="t-h3">Toolsets</div>
            <span className="t-caption fg-2">{tsSave === "saving" ? "Saving…" : tsSave === "saved" ? "Saved ✓" : ""}</span>
          </div>
          <div className="field-help" style={{ marginBottom: 10 }}>
            Everything is exposed by default over the single MCP endpoint above — untick a toolset, or open one and untick individual tools, to leave them out. Create and fill sets on the <b>Tools</b> screen.
          </div>
          {toolSets.length === 0 && <div className="fg-2 t-caption">No tool sets yet — create one on the Tools screen.</div>}
          <div className="col gap1">
            {toolSets.map((ts) => {
              const open = openSets.has(ts.id);
              const members = ts.tool_ids.map((id) => toolById.get(id)).filter(Boolean) as typeof tools;
              const shown = ts.exposed ? members.filter((t) => !excluded.includes(t.id)).length : 0;
              return (
                <div key={ts.id} className="card" style={{ padding: 0, overflow: "hidden" }}>
                  <div className="row spread" style={{ padding: "8px 10px", cursor: "pointer" }} onClick={() => toggleOpenSet(ts.id)}>
                    <div className="row gap2" style={{ alignItems: "center", minWidth: 0 }}>
                      <Icon name={open ? "chevdown" : "chevright"} size={14} style={{ color: "var(--fg-2)", flex: "none" }} />
                      <span className="mono-sm truncate">{ts.name}</span>
                      <span className="t-caption fg-2">{shown}/{members.length}</span>
                      {!ts.exposed && <span className="pill pill-muted" style={{ height: 16 }}>excluded</span>}
                    </div>
                    <label className="row gap1" style={{ alignItems: "center", cursor: "pointer", flex: "none" }} onClick={(e) => e.stopPropagation()} title="Expose this whole toolset over MCP">
                      <input type="checkbox" checked={ts.exposed} onChange={() => toggleExposed(ts)} />
                      <span className="t-caption fg-2">Expose</span>
                    </label>
                  </div>
                  {open && (
                    <div className="col gap1" style={{ padding: "6px 12px 10px 30px", borderTop: "1px solid var(--line)" }}>
                      {members.length === 0 && <div className="t-caption fg-2">No tools in this set yet.</div>}
                      {members.map((t) => (
                        <label key={t.id} className="row gap2" style={{ alignItems: "center", cursor: ts.exposed ? "pointer" : "default", opacity: ts.exposed ? 1 : 0.5 }}>
                          <input type="checkbox" disabled={!ts.exposed} checked={ts.exposed && !excluded.includes(t.id)} onChange={() => toggleToolExcluded(t.id)} />
                          <span className="mono-sm">{t.name}</span><span className="typechip">{t.kind}</span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div className="t-h3" style={{ marginBottom: 10 }}>Currently exposed tools ({exposedTools.length + projectTools.length})</div>
          <div className="col gap2">
            {projectTools.map((t) => (
              <div key={t.name} className="row gap2"><Icon name="connect" size={14} style={{ color: "var(--accent)" }} /><span className="mono-sm">{t.name}</span><span className="typechip">{t.kind}</span></div>
            ))}
            {exposedTools.map((t) => (
              <div key={t.id} className="row gap2"><Icon name="tools" size={14} style={{ color: "var(--fg-2)" }} /><span className="mono-sm">{t.name}</span><span className="typechip">{t.kind}</span></div>
            ))}
            {exposedTools.length === 0 && projectTools.length === 0 && <div className="fg-2 t-caption">Nothing exposed yet — enable a project tool above, or put tools in a set and toggle it on.</div>}
          </div>
        </div>
              </>
            )}

            {section === "embed" && <EmbedPanel project={project} />}
          </div>
        </div>
      </div>
    </div>
  );
}
