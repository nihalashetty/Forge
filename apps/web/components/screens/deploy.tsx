"use client";
/* Connect (MCP) screen - expose this project's tools as an MCP server for external clients.
   (Consuming external MCP servers lives in the BUILD → External MCP tab.) */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { CodeBlock } from "../primitives";
import { api } from "@/lib/api";

/* ============ CONNECT (MCP) ============ */
export function ConnectScreen({ project }: { project: any }) {
  const [tools, setTools] = useState<any[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  useEffect(() => { if (project?.id) api.listTools(project.id).then(setTools).catch(() => {}); }, [project?.id]);
  useEffect(() => { if (project?.id) api.getProject(project.id).then((p) => setApiKey((p.config as any)?.mcp_api_key || "")).catch(() => {}); }, [project?.id]);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const url = `${origin}/api/forge/v1/mcp/${project?.id || "<project>"}`;
  const claudeConfig = JSON.stringify({ mcpServers: { [project?.slug || "forge"]: { url, headers: { Authorization: `Bearer ${apiKey || "<set-an-api-key>"}` } } } }, null, 2);

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
        <div className="t-display" style={{ marginBottom: 4 }}>Connect (MCP)</div>
        <div className="fg-1" style={{ marginBottom: 18 }}>Expose this project as an MCP server so Claude Desktop, Cursor, and VS Code can call its tools.</div>
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
