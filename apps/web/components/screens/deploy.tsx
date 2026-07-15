"use client";
/* Connect (MCP) screen - expose this project's tools as an MCP server for external clients.
   (Consuming external MCP servers lives in the BUILD → External MCP tab.) */
import { ReactNode, useEffect, useState } from "react";
import { Icon } from "../icons";
import { CodeBlock, Field } from "../primitives";
import { api } from "@/lib/api";
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
const CONN_SECTIONS: { id: ConnSection; label: string; icon: string; sub: string }[] = [
  { id: "run", label: "Run API", icon: "bolt", sub: "Call this project's workflow from your backend over one endpoint." },
  { id: "reference", label: "Integration reference", icon: "traces", sub: "The wire format for streaming and non-streaming responses." },
  { id: "mcp", label: "MCP server", icon: "connect", sub: "Expose this project's tools to Claude Desktop, Cursor, or VS Code." },
  { id: "embed", label: "Embed", icon: "grid", sub: "Drop this project's chatbot into any website as a widget." },
];

/* ============ CONNECT (MCP) ============ */
export function ConnectScreen({ project }: { project: any }) {
  const [section, setSection] = useState<ConnSection>("run");
  const [tools, setTools] = useState<any[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  // Run-API panel: pick the workflow this project's API runs (a saved setting) + the
  // backend-facing base URL, then show the one ready-to-copy endpoint.
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [wfId, setWfId] = useState("");
  const [apiSave, setApiSave] = useState<"idle" | "saving" | "saved">("idle");
  const [apiBase, setApiBase] = useState(
    (process.env.NEXT_PUBLIC_FORGE_API_URL || "http://localhost:8000").replace(/\/$/, ""),
  );
  useEffect(() => { if (project?.id) api.listTools(project.id).then(setTools).catch(() => {}); }, [project?.id]);
  useEffect(() => {
    if (!project?.id) return;
    Promise.all([api.getProject(project.id), api.listWorkflows(project.id)]).then(([p, ws]) => {
      setApiKey((p.config as any)?.mcp_api_key || "");
      setWorkflows(ws);
      // Default the picker to the saved API workflow; else the active one, else the first.
      const saved = (p.config as any)?.api_workflow_id;
      const chosen = ws.find((w: any) => w.id === saved) || ws.find((w: any) => w.status === "active") || ws[0];
      setWfId(chosen ? chosen.id : "");
    }).catch(() => {});
  }, [project?.id]);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const url = `${origin}/api/forge/v1/mcp/${project?.id || "<project>"}`;
  const claudeConfig = JSON.stringify({ mcpServers: { [project?.slug || "forge"]: { url, headers: { Authorization: `Bearer ${apiKey || "<set-an-api-key>"}` } } } }, null, 2);

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
  const genKey = () => saveKey("fmcp_" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2));

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
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", height: 34, padding: "0 10px", marginBottom: 1, borderRadius: 7, border: "none", cursor: "pointer", textAlign: "left", color: on ? "var(--accent)" : "var(--fg-1)", fontSize: 13, fontWeight: on ? 600 : 500, fontFamily: "var(--font-ui)" }}>
                <Icon name={s.icon as any} size={16} style={{ flex: "none" }} />
                <span className="grow truncate">{s.label}</span>
              </button>
            );
          })}
        </nav>

        {/* content */}
        <div className="scroll-y grow" style={{ minWidth: 0 }}>
          <div className="fade-up" style={{ maxWidth: 820, margin: "0 auto", padding: "24px 28px" }}>
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
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>MCP endpoint</div>
          <CodeBlock code={url} />
          <div className="field-help">JSON-RPC over HTTP (initialize / tools/list / tools/call) · authenticate with the API key below.</div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row spread" style={{ marginBottom: 8 }}><div className="t-h3">API key</div><span className="t-caption fg-2">{save === "saving" ? "Saving…" : save === "saved" ? "Saved ✓" : ""}</span></div>
          <div className="row gap2">
            <input className="input mono" style={{ flex: 1 }} type="text" value={apiKey} onChange={(e) => setApiKey(e.target.value)} onBlur={(e) => saveKey(e.target.value)} placeholder="Set a key to expose the server" />
            <button className="btn btn-secondary btn-sm" onClick={genKey}><Icon name="refresh" size={13} />Generate</button>
          </div>
          <div className="field-help">Required: clients send it as <span className="mono-sm">Authorization: Bearer &lt;key&gt;</span>. Without a key the endpoint is closed.</div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Claude Desktop / Cursor config</div>
          <CodeBlock code={claudeConfig} />
        </div>
        <div className="card" style={{ padding: 16 }}>
          <div className="t-h3" style={{ marginBottom: 10 }}>Exposed tools ({tools.filter((t) => t.enabled).length})</div>
          <div className="col gap2">
            {tools.map((t) => (
              <div key={t.id} className="row gap2"><Icon name="tools" size={14} style={{ color: "var(--fg-2)" }} /><span className="mono-sm">{t.name}</span><span className="typechip">{t.kind}</span>{!t.enabled && <span className="pill pill-muted" style={{ height: 16 }}>disabled</span>}</div>
            ))}
            {tools.length === 0 && <div className="fg-2 t-caption">No tools to expose yet - add some on the Tools screen.</div>}
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
