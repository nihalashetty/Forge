"use client";
/* Agents: preset list + the Agent config (flavor · model · tools · middleware stack). */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../icons";
import { StatusPill, Tile } from "../primitives";
import { AgentConfig } from "../canvas/AgentConfig";
import { api, Agent, ComponentT, McpClientT, Tool } from "@/lib/api";

const NEW_AGENT_CONFIG = { flavor: "agent", model: "openai:gpt-4o-mini", system_prompt: "", tools: [], components: [], middleware: [] };

/* ============ AGENTS LIST ============ */
export function AgentsScreen({ project, onOpen }: { project: any; onOpen: (a: Agent) => void }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    if (!project?.id) return;
    api.listAgents(project.id).then((a) => { setAgents(a); setLoaded(true); }).catch(() => setLoaded(true));
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  async function create() {
    setBusy(true);
    try {
      const a = await api.createAgent(project.id, { name: "new_agent", config: NEW_AGENT_CONFIG });
      onOpen(a);
    } finally { setBusy(false); }
  }

  const [deleting, setDeleting] = useState<string | null>(null);
  async function del(e: React.MouseEvent, a: Agent) {
    e.stopPropagation();
    if (!window.confirm(`Delete agent "${a.name}"? This cannot be undone.`)) return;
    setDeleting(a.id);
    try {
      setAgents((prev) => prev.filter((x) => x.id !== a.id)); // optimistic
      await api.deleteAgent(project.id, a.id);
    } catch {
      reload();
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div className="fade-up" style={{ maxWidth: 1000, margin: "0 auto" }}>
        <div className="row spread" style={{ marginBottom: 18 }}>
          <div>
            <div className="t-display">Agents</div>
            <div className="fg-1" style={{ marginTop: 3 }}>Reusable agent presets - model, tools, and a middleware stack. Drop them into workflows.</div>
          </div>
          <button className="btn btn-primary" onClick={create} disabled={busy}><Icon name="plus" size={15} />{busy ? "Creating…" : "New agent"}</button>
        </div>
        {loaded && agents.length === 0 ? (
          <div className="card col center" style={{ padding: 48, gap: 12, textAlign: "center" }}>
            <Tile icon="agents" color="var(--accent)" size={52} glow />
            <div className="t-h1">No agent presets yet</div>
            <div className="fg-1" style={{ maxWidth: 360 }}>Create a reusable agent with its own model, tools, and middleware stack.</div>
            <button className="btn btn-primary btn-lg" onClick={create} disabled={busy}><Icon name="plus" size={16} />New agent</button>
          </div>
        ) : (
          <div className="col gap3">
            {agents.map((a) => {
              const c = a.config || {};
              const tools = (c.tools || []).length;
              const mw = (c.middleware || []).filter((m: any) => m.enabled !== false).length;
              return (
                <div key={a.id} className="card card-hover" style={{ padding: 14 }} onClick={() => onOpen(a)}>
                  <div className="row gap3">
                    <Tile icon={c.flavor === "deep_agent" ? "n_deepagent" : "n_agent"} color="var(--accent)" size={38} />
                    <div className="grow">
                      <div className="row gap2"><span className="t-h2 mono">{a.name}</span><span className="typechip">{c.flavor || "agent"}</span></div>
                      <div className="fg-2 t-caption mono" style={{ marginTop: 3 }}>{c.model || "-"} · {tools} tools · {mw} middleware{a.created_by_email ? ` · by ${a.created_by_email}` : ""}</div>
                    </div>
                    <button className="iconbtn" title="Delete agent" disabled={deleting === a.id} onClick={(e) => del(e, a)}><Icon name="trash" size={15} /></button>
                    <Icon name="chevright" size={16} style={{ color: "var(--fg-2)" }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/* ============ AGENT CONFIG ============ */
export function AgentConfigScreen({ project, agentId, onBack }: { project: any; agentId?: string; onBack: () => void }) {
  const [agent, setAgent] = useState<Agent | null>(null);
  const [config, setConfig] = useState<Record<string, any>>(NEW_AGENT_CONFIG);
  const [name, setName] = useState("");
  const [tools, setTools] = useState<Tool[]>([]);
  const [mcpServers, setMcpServers] = useState<McpClientT[]>([]);
  const [components, setComponents] = useState<ComponentT[]>([]);
  const [folders, setFolders] = useState<string[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");

  useEffect(() => {
    if (project?.id) api.listTools(project.id).then(setTools).catch(() => {});
    if (project?.id) api.listMcpClients(project.id).then(setMcpServers).catch(() => {});
    if (project?.id) api.listComponents(project.id).then(setComponents).catch(() => {});
    if (project?.id) api.listFolders(project.id).then(setFolders).catch(() => {});
    if (project?.id) api.listQaKinds(project.id).then(setKinds).catch(() => {});
    if (project?.id && agentId) api.getAgent(project.id, agentId).then((a) => { setAgent(a); setConfig(a.config || NEW_AGENT_CONFIG); setName(a.name); }).catch(() => {});
  }, [project?.id, agentId]);

  async function persist() {
    if (!agent) return;
    setSave("saving");
    await api.updateAgent(project.id, agent.id, { name, config });
    setSave("saved");
    setTimeout(() => setSave("idle"), 1400);
  }

  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div className="row spread" style={{ padding: "12px 20px", borderBottom: "1px solid var(--line)", background: "var(--bg-1)" }}>
        <div className="row gap2">
          <button className="iconbtn" onClick={onBack}><Icon name="chevleft" size={18} /></button>
          <Tile icon={config.flavor === "deep_agent" ? "n_deepagent" : "n_agent"} color="var(--accent)" size={30} />
          <input className="input mono" style={{ width: 220 }} value={name} onChange={(e) => setName(e.target.value)} placeholder="agent_name" />
          {agent?.created_by_email && (
            <span className="fg-2 t-caption row gap1" title={`Created by ${agent.created_by_email}`}>
              <Icon name="user" size={13} />Created by {agent.created_by_email}
            </span>
          )}
        </div>
        <button className="btn btn-primary btn-sm" onClick={persist} disabled={save === "saving"}>
          <Icon name={save === "saved" ? "check" : "save"} size={14} />{save === "saving" ? "Saving…" : save === "saved" ? "Saved" : "Save"}
        </button>
      </div>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        <div className="scroll-y grow" style={{ padding: 24 }}>
          <div style={{ maxWidth: 640, margin: "0 auto" }}>
            <AgentConfig config={config} onChange={setConfig} tools={tools} mcpServers={mcpServers} components={components} folders={folders} kinds={kinds} />
          </div>
        </div>
        <div className="scroll-y" style={{ width: 300, flex: "none", borderLeft: "1px solid var(--line)", background: "var(--bg-1)", padding: 16 }}>
          <div className="t-micro" style={{ marginBottom: 10 }}>What the model sees</div>
          <div className="card" style={{ padding: 12, marginBottom: 12 }}>
            <div className="t-caption fg-2">System prompt</div>
            <div className="t-body-sm" style={{ marginTop: 4, whiteSpace: "pre-wrap" }}>{config.system_prompt || <span className="fg-2">- none -</span>}</div>
          </div>
          <div className="card" style={{ padding: 12 }}>
            <div className="t-caption fg-2">Compiled stack (execution order)</div>
            <div className="col gap1" style={{ marginTop: 6 }}>
              {(config.middleware || []).filter((m: any) => m.enabled !== false).map((m: any, i: number) => (
                <div key={i} className="row gap2"><span className="badge">{i + 1}</span><span className="mono-sm">{m.type}</span></div>
              ))}
              {(config.middleware || []).filter((m: any) => m.enabled !== false).length === 0 && <div className="fg-2 t-caption">No middleware</div>}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
