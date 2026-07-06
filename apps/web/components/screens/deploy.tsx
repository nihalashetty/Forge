"use client";
/* Connect (MCP) screen - expose this project's tools as an MCP server for external clients.
   (Consuming external MCP servers lives in the BUILD → External MCP tab.) */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { CodeBlock, Field } from "../primitives";
import { api } from "@/lib/api";

/* ============ CONNECT (MCP) ============ */
export function ConnectScreen({ project }: { project: any }) {
  const [tools, setTools] = useState<any[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  // Run-API panel: pick a workflow + the backend-facing base URL, then show ready-to-copy endpoints.
  const [workflows, setWorkflows] = useState<any[]>([]);
  const [wfId, setWfId] = useState("");
  const [apiBase, setApiBase] = useState(
    (process.env.NEXT_PUBLIC_FORGE_API_URL || "http://localhost:8000").replace(/\/$/, ""),
  );
  useEffect(() => { if (project?.id) api.listTools(project.id).then(setTools).catch(() => {}); }, [project?.id]);
  useEffect(() => { if (project?.id) api.getProject(project.id).then((p) => setApiKey((p.config as any)?.mcp_api_key || "")).catch(() => {}); }, [project?.id]);
  useEffect(() => {
    if (!project?.id) return;
    api.listWorkflows(project.id).then((ws) => {
      setWorkflows(ws);
      const active = ws.find((w: any) => w.status === "active") || ws[0];
      setWfId(active ? active.id : "");
    }).catch(() => {});
  }, [project?.id]);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const url = `${origin}/api/forge/v1/mcp/${project?.id || "<project>"}`;
  const claudeConfig = JSON.stringify({ mcpServers: { [project?.slug || "forge"]: { url, headers: { Authorization: `Bearer ${apiKey || "<set-an-api-key>"}` } } } }, null, 2);

  // Run-API endpoints (server-to-server): a backend hits the Forge API DIRECTLY, not the web proxy.
  const base = (apiBase || "http://localhost:8000").replace(/\/$/, "");
  const pid = project?.id || "<projectId>";
  const wid = wfId || "<workflowId>";
  const runsBase = `${base}/v1/projects/${pid}/workflows/${wid}/runs`;
  const createUrl = runsBase;
  const streamUrl = `${runsBase}/{run_id}/stream`;
  const resumeUrl = `${runsBase}/{run_id}/resume`;
  const curl = [
    "# 1) create a run (identity only - never secrets in the body)",
    `curl -s -X POST "${createUrl}" \\`,
    `  -H "Authorization: Bearer $FORGE_SERVICE_API_TOKEN" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{"input":{"messages":[{"role":"user","content":"hello"}]},"end_user":{"id":"user-123"}}'`,
    '# -> {"id":"<run_id>","thread_id":"..."}',
    "",
    "# 2) stream it - pass the caller's per-user session/CSRF for on-behalf-of tool calls",
    `curl -sN "${runsBase}/<run_id>/stream" \\`,
    `  -H "Authorization: Bearer $FORGE_SERVICE_API_TOKEN" \\`,
    `  -H 'X-Forge-Context: {"jsessionid":"<user session>","csrf":"<user csrf>"}' \\`,
    '  -H "Accept: text/event-stream"',
  ].join("\n");

  async function saveKey(next: string) {
    setApiKey(next); setSave("saving");
    const p = await api.getProject(project.id);
    await api.updateProject(project.id, { config: { ...(p.config || {}), mcp_api_key: next || undefined } });
    setSave("saved"); setTimeout(() => setSave("idle"), 1200);
  }
  const genKey = () => saveKey("fmcp_" + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2));

  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div className="fade-up" style={{ maxWidth: 820, margin: "0 auto" }}>
        <div className="t-display" style={{ marginBottom: 4 }}>Connect</div>
        <div className="fg-1" style={{ marginBottom: 18 }}>Integrate this project: call its workflows from your backend over the run API, or expose its tools over MCP.</div>

        {/* ---- Run API: call a workflow from your backend (server-to-server) ---- */}
        <div className="t-h2" style={{ margin: "4px 0 10px" }}>Run a workflow from your backend</div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="row gap3" style={{ flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 240 }}>
              <Field label="Forge API base URL" help="Where your backend reaches the Forge API directly (NOT the web console). Dev: http://localhost:8000. From another container on Forge's network: http://api:8000.">
                <input className="input mono" value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="http://localhost:8000" />
              </Field>
            </div>
            <div style={{ flex: 1, minWidth: 240 }}>
              <Field label="Workflow" help="The workflow your chatbot runs.">
                <select className="select" value={wfId} onChange={(e) => setWfId(e.target.value)}>
                  {workflows.length === 0 && <option value="">No workflows yet</option>}
                  {workflows.map((w) => <option key={w.id} value={w.id}>{w.name}{w.status !== "active" ? ` (${w.status})` : ""}</option>)}
                </select>
              </Field>
            </div>
          </div>
          <div className="row gap3" style={{ flexWrap: "wrap" }}>
            <div style={{ flex: 1, minWidth: 240 }}><Field label="Project ID"><CodeBlock code={pid} /></Field></div>
            <div style={{ flex: 1, minWidth: 240 }}><Field label="Workflow ID"><CodeBlock code={wid} /></Field></div>
          </div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 14 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Endpoints</div>
          <Field label="Create run · POST"><CodeBlock code={createUrl} /></Field>
          <Field label="Stream run · GET (SSE)"><CodeBlock code={streamUrl} /></Field>
          <Field label="Resume run · POST (human-in-the-loop)"><CodeBlock code={resumeUrl} /></Field>
          <div className="field-help">Authenticate every call with <span className="mono-sm">Authorization: Bearer &lt;FORGE_SERVICE_API_TOKEN&gt;</span> (the value set in Forge&apos;s .env). Pass the caller&apos;s per-user session/CSRF as an <span className="mono-sm">X-Forge-Context</span> header on the stream/resume calls so tools act on their behalf - never put secrets in the run body.</div>
        </div>
        <div className="card" style={{ padding: 16, marginBottom: 24 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Example (curl)</div>
          <CodeBlock code={curl} />
        </div>

        {/* ---- MCP server ---- */}
        <div className="t-h2" style={{ margin: "4px 0 10px" }}>MCP server (Claude Desktop / Cursor / VS Code)</div>
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
              <div key={t.id} className="row gap2"><Icon name="tools" size={14} style={{ color: "var(--io-tool)" }} /><span className="mono-sm">{t.name}</span><span className="typechip">{t.kind}</span>{!t.enabled && <span className="pill pill-muted" style={{ height: 16 }}>disabled</span>}</div>
            ))}
            {tools.length === 0 && <div className="fg-2 t-caption">No tools to expose yet - add some on the Tools screen.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
