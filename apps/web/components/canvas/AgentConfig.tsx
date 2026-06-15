"use client";
/* Agent configuration + the middleware-stack signature. Controlled component. */
import { useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, Segmented, Tile, Toggle } from "../primitives";
import { FieldsForm, MW_FIELDS, MultiSelectChips } from "./ConfigForm";
import { MODELS, MIDDLEWARE_CATALOG, MW_META } from "@/lib/data";
import type { Agent, McpClientT, Tool } from "@/lib/api";

type Cfg = Record<string, any>;
type MW = { type: string; enabled?: boolean; config?: Record<string, any> };

export function AgentConfig({ config, onChange, tools = [], agents = [], folders = [], kinds = [], mcpServers = [] }: { config: Cfg; onChange: (c: Cfg) => void; tools?: Tool[]; agents?: Agent[]; folders?: string[]; kinds?: string[]; mcpServers?: McpClientT[] }) {
  const set = (patch: Cfg) => onChange({ ...config, ...patch });
  const flavor = config.flavor || "agent";
  const selectedTools: string[] = config.tools || [];

  const toggleTool = (id: string) =>
    set({ tools: selectedTools.includes(id) ? selectedTools.filter((t) => t !== id) : [...selectedTools, id] });

  // Built-in knowledge access — compiles to agent-callable RAG / Q&A tools (see
  // tools/builtin.py build_knowledge_capability_tools). Each capability is independent.
  const knowledge = config.knowledge || {};
  const setKnowledge = (key: "rag" | "qa", patch: Cfg) =>
    set({ knowledge: { ...knowledge, [key]: { ...(knowledge[key] || {}), ...patch } } });

  // Bind this node to a saved agent (from the Agents tab). When bound, the saved agent's
  // config drives the node LIVE — the backend resolves `agent_ref` at compile time, so
  // editing the agent once updates every node that uses it. A snapshot of its config is
  // also copied in so the canvas card + validation stay populated. "None" detaches and
  // keeps the current fields for inline editing.
  const boundId: string = config.agent_ref || "";
  const boundAgent = boundId ? agents.find((a) => a.id === boundId) : undefined;
  const bindTo = (id: string) => {
    if (!id) {
      const { agent_ref: _drop, ...rest } = config;
      onChange(rest);
      return;
    }
    const a = agents.find((x) => x.id === id);
    if (!a) return;
    const { name: _name, ...cfg } = a.config || {};
    onChange({ flavor: "agent", ...cfg, agent_ref: id });
  };

  return (
    <div className="col" style={{ gap: 18 }}>
      {agents.length > 0 && (
        <Section label="Saved agent" hint={boundId ? undefined : "Bind this node to a saved agent — it mirrors that agent live, so editing the agent once updates every node that uses it."}>
          <select className="select" value={boundId} onChange={(e) => bindTo(e.target.value)}>
            <option value="">None — configure inline</option>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </Section>
      )}

      {boundId && (
        <div className="card col gap2" style={{ padding: 12 }}>
          {boundAgent ? (
            <>
              <div className="row gap2" style={{ alignItems: "center", minWidth: 0 }}>
                <Tile icon={boundAgent.config?.flavor === "deep_agent" ? "n_deepagent" : "n_agent"} color="var(--accent)" size={26} />
                <div style={{ minWidth: 0 }}>
                  <div className="t-h3 truncate">{boundAgent.name}</div>
                  <div className="t-caption fg-2 truncate">{boundAgent.config?.model || "—"} · {(boundAgent.config?.tools || []).length} tools · {(boundAgent.config?.middleware || []).length} middleware</div>
                </div>
              </div>
              <div className="field-help">This node mirrors the saved agent. Edit its model, instructions, tools, and middleware in the Agents tab — changes apply everywhere it's used.</div>
            </>
          ) : (
            <div className="field-help" style={{ color: "var(--warn)" }}>The saved agent this node referenced wasn’t found (it may have been deleted). Pick another above, or detach to configure inline.</div>
          )}
          <button className="btn btn-secondary btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => bindTo("")}>Detach &amp; edit inline</button>
        </div>
      )}

      {!boundId && (
      <>
      <Section label="Flavor">
        <Segmented options={[{ value: "agent", label: "Agent" }, { value: "deep_agent", label: "Deep Agent" }]} value={flavor} onChange={(v) => set({ flavor: v })} />
      </Section>

      <Section label="Model">
        <select className="select" value={config.model || ""} onChange={(e) => set({ model: e.target.value })}>
          <option value="">Select a model…</option>
          {MODELS.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.provider}</option>)}
          {config.model && !MODELS.some((m) => m.id === config.model) && <option value={config.model}>{config.model}</option>}
        </select>
      </Section>

      <Section label="Instructions" hint="The model reads this as its system prompt.">
        <textarea className="textarea" rows={4} value={config.system_prompt || ""} placeholder="You are a helpful support agent…" onChange={(e) => set({ system_prompt: e.target.value })} />
      </Section>

      <Section label="Tools" hint={tools.length ? undefined : "No tools in this project yet — add them on the Tools screen."}>
        <div className="row gap2 wrap">
          {tools.map((t) => {
            const on = selectedTools.includes(t.id);
            return (
              <button key={t.id} className="chip" onClick={() => toggleTool(t.id)}
                style={{ cursor: "pointer", borderColor: on ? "var(--accent)" : "var(--line)", color: on ? "var(--accent)" : "var(--fg-1)", background: on ? "var(--accent-glow)" : "var(--bg-3)" }}>
                {on && <Icon name="check" size={12} />}<span className="mono-sm">{t.name}</span>
              </button>
            );
          })}
        </div>
      </Section>

      <Section label="Knowledge" hint="Give this agent built-in access to the project knowledge base — no separate tool needed. The agent searches per sub-question, so one agent can answer multi-part questions.">
        <div className="col gap3">
          <div className="col gap2">
            <label className="row gap2" style={{ cursor: "pointer" }}>
              <Toggle on={!!knowledge.rag?.enabled} onChange={(on) => setKnowledge("rag", { enabled: on })} />
              <span className="t-body-sm">Search knowledge base (RAG over documents)</span>
            </label>
            {knowledge.rag?.enabled && (
              <div style={{ paddingLeft: 6 }}>
                <Field label="Folders" help="Limit document search to these folders (none selected = all).">
                  <MultiSelectChips value={knowledge.rag?.folders || []} options={folders} onChange={(items) => setKnowledge("rag", { folders: items })} />
                </Field>
              </div>
            )}
          </div>
          <div className="col gap2">
            <label className="row gap2" style={{ cursor: "pointer" }}>
              <Toggle on={!!knowledge.qa?.enabled} onChange={(on) => setKnowledge("qa", { enabled: on })} />
              <span className="t-body-sm">Look up FAQ / Q&amp;A answers</span>
            </label>
            {knowledge.qa?.enabled && (
              <div style={{ paddingLeft: 6 }}>
                <Field label="Kinds" help="Limit Q&A lookup to these kinds/categories (none selected = all).">
                  <MultiSelectChips value={knowledge.qa?.kinds || []} options={kinds} onChange={(items) => setKnowledge("qa", { kinds: items })} />
                </Field>
              </div>
            )}
          </div>
        </div>
      </Section>

      {mcpServers.length > 0 && (
        <Section label="MCP servers" hint="Grant this agent the enabled tools from these MCP servers. Manage servers + per-tool toggles in Build → External MCP.">
          <div className="row gap2 wrap">
            {mcpServers.map((m) => {
              const on = (config.mcp_servers || []).includes(m.id);
              return (
                <button key={m.id} className="chip" onClick={() => {
                  const cur: string[] = config.mcp_servers || [];
                  set({ mcp_servers: on ? cur.filter((x) => x !== m.id) : [...cur, m.id] });
                }} style={{ cursor: "pointer", borderColor: on ? "var(--accent)" : "var(--line)", color: on ? "var(--accent)" : "var(--fg-1)", background: on ? "var(--accent-glow)" : "var(--bg-3)" }}>
                  {on && <Icon name="check" size={12} />}<span className="mono-sm">{m.name}</span>
                </button>
              );
            })}
          </div>
        </Section>
      )}

      <Section label="Middleware stack" hint="Order = execution order (the onion). Drag-free reorder with the arrows.">
        <MiddlewareStack stack={config.middleware || []} onChange={(mw) => set({ middleware: mw })} />
      </Section>

      {flavor === "deep_agent" && (
        <Section label="Deep Agent">
          <div className="row gap3 wrap">
            <label className="row gap2"><Toggle on={config.planning !== false} onChange={(v) => set({ planning: v })} /><span className="t-body-sm">Planning (write_todos)</span></label>
          </div>
          <div className="field-help">Subagents, filesystem backend, and sandbox are configured in the full Deep Agent panel.</div>
        </Section>
      )}
      </>
      )}
    </div>
  );
}

function Section({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="col" style={{ gap: 8 }}>
      <div className="t-micro">{label}</div>
      {children}
      {hint && <div className="field-help">{hint}</div>}
    </div>
  );
}

const CAT_KEYS: Record<string, string> = {}; // (reserved)

function MiddlewareStack({ stack, onChange }: { stack: MW[]; onChange: (s: MW[]) => void }) {
  const [adding, setAdding] = useState(false);
  const [openIdx, setOpenIdx] = useState<number | null>(null);

  const update = (i: number, patch: Partial<MW>) => onChange(stack.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  const remove = (i: number) => onChange(stack.filter((_, j) => j !== i));
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= stack.length) return;
    const next = [...stack];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  const add = (type: string) => { onChange([...stack, { type, enabled: true, config: {} }]); setAdding(false); };

  return (
    <div className="col gap2">
      {stack.length === 0 && <div className="field-help">No middleware yet. Add summarization, limits, guardrails, HITL…</div>}
      {stack.map((m, i) => {
        const meta = MW_META[m.type] || { name: m.type, desc: "", color: "var(--fg-2)" };
        const on = m.enabled !== false;
        const open = openIdx === i;
        return (
          <div key={i} className="card" style={{ padding: 0, overflow: "hidden", borderLeft: `3px solid ${meta.color}`, opacity: on ? 1 : 0.6 }}>
            <div className="row gap2" style={{ padding: "9px 11px" }}>
              <div className="col" style={{ gap: 1 }}>
                <button className="iconbtn" style={{ width: 18, height: 14 }} onClick={() => move(i, -1)} disabled={i === 0}><Icon name="chevup" size={13} /></button>
                <button className="iconbtn" style={{ width: 18, height: 14 }} onClick={() => move(i, 1)} disabled={i === stack.length - 1}><Icon name="chevdown" size={13} /></button>
              </div>
              <div className="grow" style={{ minWidth: 0, cursor: "pointer" }} onClick={() => setOpenIdx(open ? null : i)}>
                <div className="row gap2"><span className="t-h3">{meta.name}</span><span className="t-caption fg-2">{m.type}</span></div>
                <div className="t-caption fg-2 truncate">{meta.desc}</div>
              </div>
              <Toggle on={on} onChange={(v) => update(i, { enabled: v })} />
              <button className="iconbtn" onClick={() => remove(i)}><Icon name="trash" size={15} /></button>
            </div>
            {open && (
              <div style={{ padding: "0 11px 11px" }}>
                {MW_FIELDS[m.type] ? (
                  <>
                    <FieldsForm
                      specs={MW_FIELDS[m.type]}
                      config={m.config || {}}
                      onPatch={(patch) => update(i, { config: { ...(m.config || {}), ...patch } })}
                    />
                    <details style={{ marginTop: 8 }}>
                      <summary className="t-caption fg-2" style={{ cursor: "pointer" }}>Advanced (JSON)</summary>
                      <textarea className="textarea mono" rows={4} style={{ fontSize: 12, marginTop: 6 }} defaultValue={JSON.stringify(m.config || {}, null, 2)}
                        onChange={(e) => { try { update(i, { config: JSON.parse(e.target.value || "{}") }); } catch { /* keep last valid */ } }} />
                    </details>
                  </>
                ) : (
                  <>
                    <div className="field-label">Config (JSON)</div>
                    <textarea className="textarea mono" rows={4} style={{ fontSize: 12 }} defaultValue={JSON.stringify(m.config || {}, null, 2)}
                      onChange={(e) => { try { update(i, { config: JSON.parse(e.target.value || "{}") }); } catch { /* keep last valid */ } }} />
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
      <button className="btn btn-secondary btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setAdding(true)}>
        <Icon name="plus" size={14} />Add middleware
      </button>

      <Modal open={adding} onClose={() => setAdding(false)} title="Add middleware" width={560}>
        <div className="col gap4">
          {MIDDLEWARE_CATALOG.map((cat) => (
            <div key={cat.cat}>
              <div className="t-micro" style={{ marginBottom: 8, color: cat.color }}>{cat.cat}</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                {cat.items.map((it) => (
                  <button key={it.type} className="card card-hover" style={{ padding: 10, textAlign: "left" }} onClick={() => add(it.type)}>
                    <div className="t-h3">{it.name}</div>
                    <div className="t-caption fg-2" style={{ marginTop: 2 }}>{it.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}
