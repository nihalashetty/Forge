"use client";
/* External MCP — register MCP servers, discover their tools, toggle which are live.
   Server-scoped: agents and workflow nodes consume a server's *enabled* tools. */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, Tile, Toggle } from "../primitives";
import { api, McpClientT } from "@/lib/api";

export function McpClientsScreen({ project }: { project: any }) {
  const [rows, setRows] = useState<McpClientT[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  // Session cache of discovered tools per server, so re-selecting one is instant
  // (the server is the source of truth — "Re-discover" forces a fresh fetch).
  const [toolCache, setToolCache] = useState<Record<string, { name: string; description?: string }[]>>({});

  const reload = useCallback(() => {
    if (!project?.id) return;
    api.listMcpClients(project.id).then((r) => { setRows(r); setSelId((s) => (s && r.some((x) => x.id === s) ? s : (r[0]?.id ?? null))); }).catch(() => {});
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  const sel = rows.find((r) => r.id === selId) || null;

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      {/* LEFT list */}
      <div style={{ width: 280, flex: "none", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", background: "var(--bg-1)" }}>
        <div className="row spread" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
          <div className="t-h1">External MCP</div>
          <button className="btn btn-primary btn-sm" onClick={() => setAddOpen(true)}><Icon name="plus" size={14} /></button>
        </div>
        <div className="scroll-y" style={{ flex: 1, padding: 8 }}>
          {rows.length === 0 && <div className="fg-2 t-caption" style={{ padding: 12 }}>No MCP servers yet. Click + to connect one (e.g. GitHub).</div>}
          {rows.map((m) => {
            const on = selId === m.id;
            return (
              <button key={m.id} onClick={() => setSelId(m.id)} className="col" style={{ width: "100%", textAlign: "left", padding: "11px 12px", borderRadius: 9, marginBottom: 4, border: "1px solid " + (on ? "var(--accent)" : "transparent"), background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer", gap: 4 }}>
                <div className="row spread"><span className="mono-sm" style={{ fontWeight: 700, color: "var(--fg-0)" }}>{m.name}</span><span className="typechip">{m.transport}</span></div>
                <div className="row spread">
                  <span className="truncate" style={{ fontSize: 11, color: "var(--fg-2)" }}>{m.url}</span>
                  <span
                    className="iconbtn" role="button" title="Remove server"
                    onClick={async (e) => {
                      e.stopPropagation();
                      if (!window.confirm(`Remove MCP server “${m.name}”? Agents and workflows using it will lose those tools.`)) return;
                      await api.deleteMcpClient(project.id, m.id);
                      reload();
                    }}
                  ><Icon name="trash" size={13} /></span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* RIGHT detail */}
      <div className="grow scroll-y" style={{ padding: 24, minWidth: 0 }}>
        {sel ? <ServerDetail key={sel.id} project={project} server={sel} onChanged={reload} cached={toolCache[sel.id] ?? null} onLoaded={(list) => setToolCache((c) => ({ ...c, [sel.id]: list }))} /> : (
          <div className="col center" style={{ height: "100%", gap: 8, color: "var(--fg-2)" }}><Tile icon="connect" color="var(--accent)" size={48} glow /><div className="t-h2">Connect an MCP server</div></div>
        )}
      </div>

      <AddServerModal open={addOpen} project={project} onClose={() => setAddOpen(false)} onAdded={(id) => { setAddOpen(false); reload(); setSelId(id); }} />
    </div>
  );
}

function ServerDetail({ project, server, onChanged, cached, onLoaded }: { project: any; server: McpClientT; onChanged: () => void; cached: { name: string; description?: string }[] | null; onLoaded: (list: { name: string; description?: string }[]) => void }) {
  const [tools, setTools] = useState<{ name: string; description?: string }[] | null>(cached);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [disabled, setDisabled] = useState<Set<string>>(new Set(server.disabled_tools || []));
  const [saving, setSaving] = useState(false);

  const discover = useCallback(() => {
    setLoading(true); setErr(null);
    api.discoverMcpTools(project.id, server.id)
      .then((r) => { if (r.ok) { const list = r.tools || []; setTools(list); onLoaded(list); } else setErr(r.error || "Could not list tools from that server."); })
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [project.id, server.id, onLoaded]);
  // Only fetch when this server's tools aren't already cached this session; the server is
  // the source of truth, so the "Re-discover" button forces a fresh fetch on demand.
  useEffect(() => { if (cached === null) discover(); }, [server.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function toggle(name: string) {
    const next = new Set(disabled);
    if (next.has(name)) next.delete(name); else next.add(name);
    setDisabled(next); setSaving(true);
    try { await api.updateMcpClient(project.id, server.id, { disabled_tools: [...next] }); onChanged(); }
    catch { setDisabled(new Set(server.disabled_tools || [])); }
    finally { setSaving(false); }
  }

  const enabledCount = tools ? tools.filter((t) => !disabled.has(t.name)).length : 0;

  return (
    <div style={{ maxWidth: 720 }}>
      <div className="row spread" style={{ marginBottom: 18 }}>
        <div className="row gap3">
          <Tile icon="connect" color="var(--accent)" size={40} glow />
          <div>
            <div className="t-display mono" style={{ fontSize: 18 }}>{server.name}</div>
            <div className="fg-2 t-caption">{server.transport} · {server.url}</div>
          </div>
        </div>
        <button className="btn btn-secondary" onClick={discover} disabled={loading}><Icon name="refresh" size={15} style={loading ? { animation: "spin 1s linear infinite" } : {}} />{loading ? "Connecting…" : "Re-discover"}</button>
      </div>

      <div className="card" style={{ padding: 18 }}>
        <div className="row spread" style={{ marginBottom: 12 }}>
          <div className="t-h2">Tools{tools ? ` · ${enabledCount}/${tools.length} enabled` : ""}</div>
          {saving && <span className="t-caption fg-2">Saving…</span>}
        </div>
        {err && <div className="card" style={{ padding: 12, color: "var(--err)", background: "var(--bg-3)" }}>{err}</div>}
        {!err && tools === null && <div className="fg-2 t-caption">Connecting to the server…</div>}
        {!err && tools && tools.length === 0 && <div className="fg-2 t-caption">This server exposes no tools.</div>}
        {!err && tools && tools.length > 0 && (
          <div className="col gap2">
            {tools.map((t) => {
              const on = !disabled.has(t.name);
              return (
                <div key={t.name} className="row spread" style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 9, gap: 12, alignItems: "flex-start" }}>
                  <div className="col" style={{ gap: 2, minWidth: 0 }}>
                    <span className="mono-sm" style={{ fontWeight: 700, color: on ? "var(--fg-0)" : "var(--fg-2)" }}>{t.name}</span>
                    {t.description && <span className="t-caption fg-2" style={{ whiteSpace: "normal" }}>{t.description}</span>}
                  </div>
                  <Toggle on={on} onChange={() => toggle(t.name)} />
                </div>
              );
            })}
          </div>
        )}
        <div className="fg-2 t-caption" style={{ marginTop: 12 }}>Disabled tools stay hidden from agents and workflow nodes that use this server.</div>
      </div>
    </div>
  );
}

function AddServerModal({ open, onClose, project, onAdded }: { open: boolean; onClose: () => void; project: any; onAdded: (id: string) => void }) {
  const [form, setForm] = useState({ name: "github", transport: "streamable_http", url: "", token: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { if (open) { setForm({ name: "github", transport: "streamable_http", url: "", token: "" }); setErr(null); } }, [open]);

  async function add() {
    if (!form.url.trim()) { setErr("Enter the server URL."); return; }
    setBusy(true); setErr(null);
    try {
      const name = (form.name || "mcp_server").trim().replace(/[^a-zA-Z0-9_-]/g, "_");
      let headers_ref: string | undefined;
      if (form.token.trim()) {
        const secName = `${name}_mcp_headers`;
        await api.createSecret(project.id, { name: secName, value: { Authorization: `Bearer ${form.token.trim()}` }, kind: "mcp_headers" });
        headers_ref = `secret://proj/${secName}`;
      }
      const created = await api.createMcpClient(project.id, { name, transport: form.transport, url: form.url.trim(), headers_ref });
      onAdded(created.id);
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  return (
    <Modal open={open} onClose={onClose} title="Connect MCP server" width={520}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={add} disabled={busy}>{busy ? "Connecting…" : "Add server"}</button></>}>
      <div className="row gap2">
        <Field label="Name"><input className="input mono" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="github" /></Field>
        <Field label="Transport">
          <select className="select" value={form.transport} onChange={(e) => setForm((f) => ({ ...f, transport: e.target.value }))}>
            {["streamable_http", "sse", "stdio"].map((t) => <option key={t}>{t}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Server URL" help="The MCP endpoint. GitHub's hosted server is https://api.githubcopilot.com/mcp/">
        <input className="input mono" value={form.url} onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))} placeholder="https://api.githubcopilot.com/mcp/" />
      </Field>
      <Field label="Bearer token" help="Optional. For servers that need auth (e.g. a GitHub PAT). Saved to Settings → Secrets (encrypted) and sent as the Authorization header.">
        <input className="input mono" type="password" value={form.token} onChange={(e) => setForm((f) => ({ ...f, token: e.target.value }))} placeholder="ghp_…" />
      </Field>
      {err && <div className="card" style={{ padding: 12, color: "var(--err)", marginTop: 4 }}>{err}</div>}
      <div className="fg-2 t-caption" style={{ marginTop: 8 }}>Forge connects and lists the server's tools; toggle which ones agents and workflows can use.</div>
    </Modal>
  );
}
