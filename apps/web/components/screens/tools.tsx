"use client";
/* Tools list (card grid) + Tool Builder (tabbed config + Live response token-meter signature). */
import * as jmespath from "jmespath";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, Segmented, StatusPill, Tabs, Tile, TokenMeter, Toggle } from "../primitives";
import { VersionHistory } from "../version-history";
import { api, AuthProviderT, Tool, ToolSet, ToolTestResult } from "@/lib/api";
import { KIND_ICON, KIND_LABEL } from "@/lib/data";

const estTokens = (o: any) => (o == null ? 0 : Math.max(1, JSON.stringify(o).length >> 2));

const DEFAULT_SAMPLE = {
  data: {
    totals: { subtotal: 4210.0, tax: 421.0, grand_total: 4631.0 },
    customer: { name: "Ada Lovelace", email: "ada@example.com", tier: "gold" },
    line_items: [{ sku: "WIDGET-1", qty: 2, price: 1200 }, { sku: "GADGET-9", qty: 1, price: 1810 }],
    status: "open",
  },
  meta: { request_id: "req_8f21c0", ts: 1780000000, page: 1, per_page: 50 },
};

/* ============ TOOLS LIST ============ */
/* last_tested → [dot colour, label]. Untested reads amber (needs attention) per the design. */
const STATUS = (s?: string | null): [string, string] =>
  s === "pass" ? ["var(--ok)", "Passing"] : s === "fail" ? ["var(--err)", "Failing"] : ["var(--warn)", "Untested"];

export function ToolsScreen({ project, onOpen }: { project: any; onOpen: (t: Tool) => void }) {
  const [tools, setTools] = useState<Tool[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<"grid" | "list">("grid");
  const [open, setOpen] = useState(false);
  const [toolSets, setToolSets] = useState<ToolSet[]>([]);
  const [filterSet, setFilterSet] = useState<string>("all"); // "all" | "ungrouped" | <setId>
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerSel, setDrawerSel] = useState<string | null>(null); // null = create a new set
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");

  const reload = useCallback(() => {
    if (!project?.id) return;
    api.listTools(project.id).then(setTools).catch((e) => setErr(String(e.message || e)));
    api.listToolSets(project.id).then(setToolSets).catch(() => {});
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  async function del(e: React.MouseEvent, t: Tool) {
    e.stopPropagation();
    if (!window.confirm(`Delete tool "${t.name}"? Workflows/agents referencing it will skip it. This cannot be undone.`)) return;
    setTools((prev) => prev.filter((x) => x.id !== t.id)); // optimistic
    try { await api.deleteTool(project.id, t.id); } catch { reload(); }
  }

  async function duplicate(e: React.MouseEvent, t: Tool) {
    e.stopPropagation();
    try {
      const cfg = { ...((t.config as any) || {}) };
      delete cfg._last_test; // start the copy untested
      await api.createTool(project.id, { name: `${t.name}_copy`, kind: t.kind, config: cfg, auth_provider_id: t.auth_provider_id || undefined });
      reload();
    } catch (e2: any) { setErr(String(e2?.message || e2)); }
  }

  async function toggleEnabled(t: Tool) {
    setTools((prev) => prev.map((x) => (x.id === t.id ? { ...x, enabled: !x.enabled } : x))); // optimistic
    try { await api.updateTool(project.id, t.id, { enabled: !t.enabled }); } catch { reload(); }
  }

  async function toggleToolInSet(setId: string, toolId: string, isMember: boolean) {
    try {
      if (isMember) await api.removeToolFromSet(project.id, setId, toolId);
      else await api.addToolToSet(project.id, setId, toolId);
    } finally {
      reload();
    }
  }

  const memberOf = useMemo(() => {
    const m = new Set<string>();
    toolSets.forEach((s) => s.tool_ids.forEach((id) => m.add(id)));
    return m;
  }, [toolSets]);
  const ungroupedCount = useMemo(() => tools.filter((t) => !memberOf.has(t.id)).length, [tools, memberOf]);
  const countFor = useCallback((s: ToolSet) => { const ids = new Set(s.tool_ids); return tools.filter((t) => ids.has(t.id)).length; }, [tools]);

  const shown = useMemo(() => {
    let list = tools;
    if (filterSet === "ungrouped") list = tools.filter((t) => !memberOf.has(t.id));
    else if (filterSet !== "all") { const ids = new Set(toolSets.find((x) => x.id === filterSet)?.tool_ids || []); list = tools.filter((t) => ids.has(t.id)); }
    const q = query.trim().toLowerCase();
    if (q) list = list.filter((t) => t.name.toLowerCase().includes(q) || String((t.config as any)?.description || "").toLowerCase().includes(q));
    return list;
  }, [tools, toolSets, filterSet, memberOf, query]);

  const showCheckbox = filterSet === "all" || filterSet === "ungrouped";
  const headingLabel = filterSet === "all" ? "Tools" : filterSet === "ungrouped" ? "Ungrouped tools" : (toolSets.find((s) => s.id === filterSet)?.name || "Tools");

  function selectFilter(key: string) { setFilterSet(key); setSelected(new Set()); }
  function toggleSelect(id: string) { setSelected((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; }); }
  function openManage() { setDrawerSel(filterSet !== "all" && filterSet !== "ungrouped" ? filterSet : (toolSets[0]?.id ?? null)); setDrawerOpen(true); }
  function openNewSet() { setDrawerSel(null); setDrawerOpen(true); }

  // Bulk-assign the current selection to a set (skipping tools already in it), then clear.
  async function addSelectedToSet(setId: string) {
    const already = new Set(toolSets.find((s) => s.id === setId)?.tool_ids || []);
    const toAdd = Array.from(selected).filter((id) => !already.has(id));
    try { await Promise.all(toAdd.map((id) => api.addToolToSet(project.id, setId, id))); }
    finally { setSelected(new Set()); reload(); }
  }

  return (
    <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
      <ToolsSidebar tools={tools} toolSets={toolSets} countFor={countFor} ungroupedCount={ungroupedCount} filterSet={filterSet} onFilter={selectFilter} onNewSet={openNewSet} onManage={openManage} />

      <div className="col" style={{ flex: 1, minWidth: 0 }}>
        <div className="scroll-y" style={{ flex: 1, padding: "24px 28px 120px" }}>
          <div className="fade-up" style={{ maxWidth: 1500, margin: "0 auto" }}>
            <div className="row spread wrap gap3" style={{ marginBottom: 22, alignItems: "flex-start" }}>
              <div>
                <div className="t-display" style={{ fontSize: 21 }}>{headingLabel}</div>
                <div className="fg-2" style={{ marginTop: 4, maxWidth: 560 }}>External capabilities - REST, GraphQL, code, SQL, or builtins - with response projection.</div>
              </div>
              <div className="row gap2">
                <input className="input" style={{ width: 190 }} placeholder="Search tools" value={query} onChange={(e) => setQuery(e.target.value)} />
                <button className="btn btn-primary" onClick={() => setOpen(true)}><Icon name="plus" size={15} />New tool</button>
                <Segmented options={[{ value: "grid", label: "Grid" }, { value: "list", label: "List" }]} value={view} onChange={(v) => setView(v as any)} />
              </div>
            </div>
            {err && <div className="card" style={{ padding: 14, color: "var(--err)", marginBottom: 12 }}>{err}</div>}

            {tools.length === 0 && !err ? (
              <div className="card col center" style={{ padding: 44, gap: 8 }}><Tile icon="tools" color="var(--accent)" size={48} glow /><div className="t-h2">No tools yet</div><div className="fg-1">Create one, or ask the Forge Assistant to build it.</div></div>
            ) : shown.length === 0 ? (
              <div className="col center" style={{ padding: "60px 0", color: "var(--fg-2)", textAlign: "center" }}>
                {filterSet === "ungrouped" ? "All tools are assigned to at least one toolset." : query ? "No tools match your search." : "No tools in this set yet."}
              </div>
            ) : view === "grid" ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(280px,1fr))", gap: 16 }}>
                {shown.map((t) => <ToolCard key={t.id} t={t} sets={toolSets} selectable={showCheckbox} selected={selected.has(t.id)} onToggleSelect={() => toggleSelect(t.id)} memberSetIds={new Set(toolSets.filter((s) => s.tool_ids.includes(t.id)).map((s) => s.id))} onToggleSet={(sid, isMember) => toggleToolInSet(sid, t.id, isMember)} onOpen={() => onOpen(t)} onDelete={(e) => del(e, t)} onDuplicate={(e) => duplicate(e, t)} onToggle={() => toggleEnabled(t)} />)}
              </div>
            ) : (
              <div className="card" style={{ overflow: "hidden" }}>
                <table className="tbl">
                  <thead><tr>{showCheckbox && <th style={{ width: 34 }}></th>}<th>Tool</th><th>Kind</th><th>Auth</th><th>Projection</th><th>Status</th><th></th></tr></thead>
                  <tbody>
                    {shown.map((t) => {
                      const lt = (t.config as any)?._last_test;
                      return (
                        <tr key={t.id} className="row" style={{ cursor: "pointer" }} onClick={() => onOpen(t)}>
                          {showCheckbox && <td onClick={(e) => { e.stopPropagation(); toggleSelect(t.id); }}><Checkbox checked={selected.has(t.id)} /></td>}
                          <td><div className="row gap2"><Icon name={KIND_ICON[t.kind] || "k_rest"} size={15} style={{ color: "var(--accent)" }} /><span className="mono-sm" style={{ fontWeight: 600 }}>{t.name}</span></div></td>
                          <td><span className="chip chip-mono">{KIND_LABEL[t.kind] || t.kind}</span></td>
                          <td>{t.auth_provider_id ? <span className="chip chip-mono"><Icon name="auth" size={12} />{t.auth_provider_id.slice(0, 8)}</span> : <span className="fg-2">-</span>}</td>
                          <td>{lt ? <TokenMeter compact raw={lt.raw_tokens} projected={lt.projected_tokens} animateKey={t.id} /> : <span className="fg-2">-</span>}</td>
                          <td><StatusPill status={t.last_tested || "untested"} /></td>
                          <td style={{ textAlign: "right" }}>
                            <div className="row gap1" style={{ justifyContent: "flex-end" }}>
                              <Toggle on={t.enabled} onChange={() => toggleEnabled(t)} />
                              <button className="iconbtn" title="Duplicate tool" onClick={(e) => duplicate(e, t)}><Icon name="copy" size={14} /></button>
                              <button className="iconbtn" title="Delete tool" onClick={(e) => { e.stopPropagation(); del(e, t); }}><Icon name="trash" size={14} /></button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>

      {selected.size > 0 && <SelectionBar count={selected.size} toolSets={toolSets} onAddTo={addSelectedToSet} onClear={() => setSelected(new Set())} />}

      <NewToolModal project={project} open={open} onClose={() => setOpen(false)} onOpenTool={onOpen} onReload={reload} />
      <ManageToolsetsDrawer project={project} tools={tools} toolSets={toolSets} open={drawerOpen} initialSel={drawerSel} onClose={() => setDrawerOpen(false)} onChanged={reload} />
    </div>
  );
}

/* ============ TOOLS SIDEBAR (All / Ungrouped / colour-coded toolsets) ============ */
function Checkbox({ checked }: { checked: boolean }) {
  return (
    <div style={{ width: 16, height: 16, borderRadius: 4, flex: "none", border: `1.5px solid ${checked ? "var(--accent)" : "var(--line-strong)"}`, background: checked ? "var(--accent)" : "var(--bg-1)", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
      {checked && <Icon name="check" size={11} style={{ color: "var(--fg-on-accent)" }} />}
    </div>
  );
}

function SideItem({ label, count, active, onClick, alert }: { label: string; count: number; active: boolean; onClick: () => void; alert?: boolean }) {
  return (
    <div onClick={onClick} className="sidenav-item" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "8px 10px", borderRadius: 8, cursor: "pointer", fontWeight: active ? 600 : 500, fontSize: 13.5, background: active ? "var(--accent-glow)" : undefined, color: active ? "var(--accent)" : "var(--fg-1)" }}>
      <span style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
        <span className="truncate">{label}</span>
        {alert && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--warn)", flex: "none" }} />}
      </span>
      <span style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-2)", flex: "none" }}>{count}</span>
    </div>
  );
}

function ToolsSidebar({ tools, toolSets, countFor, ungroupedCount, filterSet, onFilter, onNewSet, onManage }: { tools: Tool[]; toolSets: ToolSet[]; countFor: (s: ToolSet) => number; ungroupedCount: number; filterSet: string; onFilter: (k: string) => void; onNewSet: () => void; onManage: () => void }) {
  return (
    <div className="col" style={{ width: 236, flex: "0 0 236px", background: "var(--bg-1)", borderRight: "1px solid var(--line)", padding: "20px 14px", gap: 22, overflowY: "auto" }}>
      <div className="t-h1" style={{ padding: "0 8px" }}>MCP Tools</div>

      <div className="col" style={{ gap: 2 }}>
        <SideItem label="All tools" count={tools.length} active={filterSet === "all"} onClick={() => onFilter("all")} />
        <SideItem label="Ungrouped" count={ungroupedCount} active={filterSet === "ungrouped"} onClick={() => onFilter("ungrouped")} alert={ungroupedCount > 0} />
      </div>

      <div className="col" style={{ gap: 6 }}>
        <div className="row spread" style={{ padding: "0 10px" }}>
          <span className="t-micro">Toolsets</span>
          <button className="iconbtn" title="New toolset" onClick={onNewSet} style={{ width: 22, height: 22 }}><Icon name="plus" size={14} /></button>
        </div>
        {toolSets.map((s) => (
          <SideItem key={s.id} label={s.name} count={countFor(s)} active={filterSet === s.id} onClick={() => onFilter(s.id)} />
        ))}
        {toolSets.length === 0 && <div className="fg-2 t-caption" style={{ padding: "2px 10px" }}>No toolsets yet.</div>}
        <button onClick={onManage} className="btn btn-ghost btn-sm" style={{ marginTop: 4, justifyContent: "center", border: "1px dashed var(--line-strong)" }}>Manage toolsets</button>
      </div>

      <div style={{ marginTop: "auto", padding: 10, background: "var(--bg-3)", borderRadius: 8, fontSize: 12, color: "var(--fg-2)", lineHeight: 1.5 }}>
        A tool can belong to any number of toolsets - assign it from its card, bulk-select, or Manage toolsets.
      </div>
    </div>
  );
}

/* Bulk-action bar (fixed, bottom-centre) shown while tools are multi-selected. */
function SelectionBar({ count, toolSets, onAddTo, onClear }: { count: number; toolSets: ToolSet[]; onAddTo: (setId: string) => void; onClear: () => void }) {
  const [menu, setMenu] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setMenu(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [menu]);
  return (
    <div className="fade-up row gap3" style={{ position: "fixed", bottom: 24, left: "50%", transform: "translateX(-50%)", background: "var(--fg-0)", color: "var(--bg-1)", borderRadius: 12, padding: "10px 14px", boxShadow: "var(--sh-pop)", zIndex: 5000 }}>
      <span style={{ fontSize: 13, fontWeight: 600 }}>{count} selected</span>
      <div ref={ref} style={{ position: "relative" }}>
        <button onClick={() => setMenu((m) => !m)} style={{ fontSize: 13, fontWeight: 600, padding: "7px 12px", borderRadius: 8, background: "var(--bg-hover)", color: "var(--fg-0)", border: "none", cursor: "pointer", fontFamily: "var(--font-ui)" }}>Add to toolset ▾</button>
        {menu && (
          <div className="card" style={{ position: "absolute", bottom: "calc(100% + 8px)", left: 0, minWidth: 200, padding: 6, boxShadow: "var(--sh-pop)" }}>
            {toolSets.length === 0 && <div className="fg-2 t-caption" style={{ padding: "8px 10px" }}>No toolsets yet.</div>}
            {toolSets.map((s) => (
              <button key={s.id} onClick={() => { setMenu(false); onAddTo(s.id); }} className="truncate" style={{ width: "100%", textAlign: "left", padding: "8px 10px", border: "none", background: "none", borderRadius: 6, cursor: "pointer", fontSize: 13, color: "var(--fg-0)", fontFamily: "var(--font-ui)" }}>{s.name}</button>
            ))}
          </div>
        )}
      </div>
      <button onClick={onClear} style={{ fontSize: 13, fontWeight: 600, color: "var(--fg-2)", background: "none", border: "none", cursor: "pointer", fontFamily: "var(--font-ui)" }}>Cancel</button>
    </div>
  );
}

/* ============ MANAGE TOOLSETS (right drawer: set list + editor) ============ */
function ManageToolsetsDrawer({ project, tools, toolSets, open, initialSel, onClose, onChanged }: { project: any; tools: Tool[]; toolSets: ToolSet[]; open: boolean; initialSel: string | null; onClose: () => void; onChanged: () => void }) {
  const [sel, setSel] = useState<string | null>(initialSel); // null = creating a new set
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const addRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (open) setSel(initialSel); }, [open, initialSel]);
  useEffect(() => {
    if (sel == null) { setName(""); setDesc(""); setMemberIds([]); return; }
    const s = toolSets.find((x) => x.id === sel);
    if (s) { setName(s.name); setDesc(s.description || ""); setMemberIds(s.tool_ids); }
  }, [sel, toolSets]);
  useEffect(() => {
    if (!addOpen) return;
    const h = (e: MouseEvent) => { if (addRef.current && !addRef.current.contains(e.target as Node)) setAddOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [addOpen]);

  const saved = toolSets.find((x) => x.id === sel)?.tool_ids || [];
  // The list shows only tools that belong to the set - plus any member you just unchecked,
  // kept visible (unchecked) until you Save, at which point it drops off. Unrelated tools
  // are never listed here; add them from the "Add tools" picker.
  const visibleIds = useMemo(() => new Set([...saved, ...memberIds]), [saved, memberIds]);
  const memberList = tools.filter((t) => visibleIds.has(t.id));
  const addable = tools.filter((t) => !visibleIds.has(t.id));
  const addMember = (tid: string) => setMemberIds((m) => (m.includes(tid) ? m : [...m, tid]));
  const toggleMember = (tid: string) => setMemberIds((m) => (m.includes(tid) ? m.filter((x) => x !== tid) : [...m, tid]));

  async function save() {
    setBusy(true);
    try {
      if (sel == null) { const s = await api.createToolSet(project.id, { name: name.trim() || "new_toolset", description: desc, tool_ids: memberIds }); setSel(s.id); }
      else await api.updateToolSet(project.id, sel, { name, description: desc, tool_ids: memberIds });
      onChanged();
    } finally { setBusy(false); }
  }
  async function del() {
    if (sel == null) return;
    if (!window.confirm("Delete this tool set? The tools themselves are not deleted.")) return;
    await api.deleteToolSet(project.id, sel);
    setSel(null); onChanged();
  }

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 7000, pointerEvents: open ? "auto" : "none" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(8,10,14,.4)", opacity: open ? 1 : 0, transition: "opacity var(--dur)" }} />
      <div className="row" style={{ position: "absolute", top: 0, right: 0, bottom: 0, width: 640, maxWidth: "96vw", background: "var(--bg-1)", borderLeft: "1px solid var(--line)", boxShadow: "var(--sh-pop)", transform: open ? "none" : "translateX(100%)", transition: "transform var(--dur-slow) var(--ease)", alignItems: "stretch" }}>
        <div className="col" style={{ width: 210, flex: "0 0 210px", borderRight: "1px solid var(--line)", padding: "20px 12px", gap: 4, overflowY: "auto" }}>
          <div className="t-h1" style={{ padding: "4px 8px 12px" }}>Toolsets</div>
          {toolSets.map((s) => {
            const active = sel === s.id;
            return (
              <div key={s.id} onClick={() => setSel(s.id)} className="sidenav-item" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "9px 10px", borderRadius: 8, cursor: "pointer", background: active ? "var(--accent-glow)" : undefined }}>
                <span className="truncate" style={{ minWidth: 0, fontSize: 13, fontWeight: active ? 600 : 500, color: active ? "var(--accent)" : "var(--fg-1)" }}>{s.name}</span>
                <span className="fg-2 t-caption">{s.tool_ids.length}</span>
              </div>
            );
          })}
          <button onClick={() => setSel(null)} className="btn btn-ghost btn-sm" style={{ marginTop: 6, justifyContent: "flex-start", color: sel == null ? "var(--accent)" : "var(--fg-1)" }}><Icon name="plus" size={13} />New toolset</button>
        </div>

        {/* Right editor pane: header + fields + list header stay put; only the member list
            scrolls, and the action row is pinned to the bottom. */}
        <div className="col grow" style={{ minWidth: 0, minHeight: 0 }}>
          <div style={{ position: "relative", zIndex: 3, padding: "22px 24px 0" }}>
            <div className="row spread" style={{ marginBottom: 16 }}>
              <div className="t-h1">{sel == null ? "New toolset" : name || "Toolset"}</div>
              <button className="iconbtn" onClick={onClose}><Icon name="x" size={17} /></button>
            </div>
            <Field label="Name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="quoting_tools" /></Field>
            <Field label="Description" help="Shown to MCP clients/agents to describe what the set is for."><input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>

            <div className="row spread" style={{ margin: "6px 0 8px", alignItems: "center" }}>
              <span className="t-micro">Tools in this set - {memberIds.length}</span>
              <div ref={addRef} style={{ position: "relative" }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setAddOpen((o) => !o)} disabled={addable.length === 0}><Icon name="plus" size={13} />Add tools</button>
                {addOpen && addable.length > 0 && (
                  <div className="card fade-in" style={{ position: "absolute", top: "calc(100% + 4px)", right: 0, zIndex: 10, minWidth: 260, maxHeight: 280, overflowY: "auto", padding: 4, boxShadow: "var(--sh-pop)" }}>
                    {addable.map((t) => (
                      <button key={t.id} onClick={() => addMember(t.id)} className="row spread gap2" style={{ width: "100%", padding: "7px 9px", border: "none", background: "none", borderRadius: 6, cursor: "pointer", fontFamily: "var(--font-ui)" }}>
                        <span className="mono-sm truncate" style={{ fontWeight: 600 }}>{t.name}</span>
                        <span className="chip chip-mono">{KIND_LABEL[t.kind] || t.kind}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          <div className="scroll-y" style={{ flex: 1, minHeight: 0, padding: "0 24px 8px" }}>
            <div className="col" style={{ border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
              {memberList.map((t, i) => (
                <div key={t.id} onClick={() => toggleMember(t.id)} className="row gap2" style={{ padding: "10px 12px", borderBottom: i < memberList.length - 1 ? "1px solid var(--line)" : "none", cursor: "pointer" }}>
                  <Checkbox checked={memberIds.includes(t.id)} />
                  <span className="mono-sm" style={{ fontWeight: 600, flex: 1, minWidth: 0 }}>{t.name}</span>
                  <span className="chip chip-mono">{KIND_LABEL[t.kind] || t.kind}</span>
                </div>
              ))}
              {memberList.length === 0 && <div className="fg-2 t-caption" style={{ padding: 14 }}>No tools in this set yet — use “Add tools”.</div>}
            </div>
          </div>

          <div className="row spread" style={{ padding: "12px 24px", borderTop: "1px solid var(--line)", flex: "none" }}>
            {sel != null ? <button className="btn btn-ghost btn-sm" style={{ color: "var(--err)" }} onClick={del}><Icon name="trash" size={13} />Delete set</button> : <span />}
            <div className="row gap2">
              <button className="btn btn-ghost btn-sm" onClick={onClose}>Close</button>
              <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save set"}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ToolCard({ t, sets, selectable, selected, onToggleSelect, memberSetIds, onToggleSet, onOpen, onDelete, onDuplicate, onToggle }: { t: Tool; sets: ToolSet[]; selectable: boolean; selected: boolean; onToggleSelect: () => void; memberSetIds: Set<string>; onToggleSet: (setId: string, isMember: boolean) => void; onOpen: () => void; onDelete: (e: React.MouseEvent) => void; onDuplicate: (e: React.MouseEvent) => void; onToggle: () => void }) {
  const [statusColor, statusLabel] = STATUS(t.last_tested);
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    // While its ••• menu is open the card is lifted above its grid siblings, so the dropdown
    // (which overflows the card bounds) isn't painted under the neighbouring cards.
    <div className="card card-hover" style={{ padding: 16, position: "relative", zIndex: menuOpen ? 30 : undefined, opacity: t.enabled ? 1 : 0.6, borderColor: selected ? "var(--accent)" : undefined }} onClick={onOpen}>
      <div className="row spread" style={{ marginBottom: 12 }}>
        <div className="row gap2">
          {selectable && <div onClick={(e) => { e.stopPropagation(); onToggleSelect(); }}><Checkbox checked={selected} /></div>}
          <span className="chip chip-mono">{KIND_LABEL[t.kind] || t.kind}</span>
        </div>
        <div className="row gap2" style={{ alignItems: "center" }}>
          <span style={{ width: 7, height: 7, borderRadius: "50%", background: statusColor, flex: "none" }} />
          <span className="t-caption fg-1">{statusLabel}</span>
          <ToolCardMenu enabled={t.enabled} sets={sets} memberSetIds={memberSetIds} onToggleSet={onToggleSet} onToggle={onToggle} onDuplicate={onDuplicate} onDelete={onDelete} onOpenChange={setMenuOpen} />
        </div>
      </div>
      <div className="mono" style={{ fontWeight: 700, fontSize: 14, color: "var(--fg-0)", overflowWrap: "anywhere", wordBreak: "break-word", marginBottom: 6 }}>{t.name}</div>
      {/* Clamp to 2 lines with a fixed height so cards stay uniform regardless of how long
          a tool's description is (grid rows otherwise stretch to the tallest card). */}
      <div className="fg-1 t-caption" style={{ lineHeight: "18px", height: 36, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>{(t.config as any)?.description || "No description."}</div>
    </div>
  );
}

/* Overflow menu on each tool card - enable/disable toggle, per-set membership toggles
   (colour-coded to match the sidebar), duplicate, delete. Drops DOWN from the trigger,
   right-aligned so it never runs off-screen. */
function ToolCardMenu({ enabled, sets, memberSetIds, onToggleSet, onToggle, onDuplicate, onDelete, onOpenChange }: { enabled: boolean; sets: ToolSet[]; memberSetIds: Set<string>; onToggleSet: (setId: string, isMember: boolean) => void; onToggle: () => void; onDuplicate: (e: React.MouseEvent) => void; onDelete: (e: React.MouseEvent) => void; onOpenChange?: (open: boolean) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { onOpenChange?.(open); }, [open, onOpenChange]);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);
  const item: React.CSSProperties = { display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left", padding: "7px 9px", border: "none", background: "none", cursor: "pointer", borderRadius: 6, fontSize: 12.5, fontFamily: "var(--font-ui)" };
  return (
    <div ref={ref} style={{ position: "relative" }} onClick={(e) => e.stopPropagation()}>
      <button className={"iconbtn" + (open ? " active" : "")} title="More actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}><Icon name="more" size={16} /></button>
      {open && (
        <div className="card fade-in" role="menu" style={{ position: "absolute", top: "100%", right: 0, marginTop: 6, zIndex: 6000, minWidth: 190, padding: 4, boxShadow: "var(--sh-pop)" }}>
          <div style={{ ...item, justifyContent: "space-between", color: "var(--fg-1)", cursor: "default" }}>
            <span className="row gap2"><Icon name="bolt" size={15} style={{ color: enabled ? "var(--signal)" : "var(--fg-2)" }} />{enabled ? "Enabled" : "Disabled"}</span>
            <Toggle on={enabled} onChange={onToggle} />
          </div>
          <div className="divider" style={{ margin: "4px 0" }} />
          <div className="t-micro" style={{ padding: "4px 9px 2px" }}>Tool sets</div>
          {sets.length > 0 ? sets.map((s) => {
            const member = memberSetIds.has(s.id);
            return (
              <button key={s.id} role="menuitemcheckbox" aria-checked={member} style={{ ...item, color: "var(--fg-1)", justifyContent: "space-between" }} onClick={() => onToggleSet(s.id, member)}>
                <span className="truncate">{s.name}</span>
                {member && <Icon name="check" size={14} style={{ color: "var(--accent)" }} />}
              </button>
            );
          }) : <div style={{ padding: "2px 9px 6px", color: "var(--fg-2)", fontSize: 12 }}>Not in any toolset</div>}
          <div className="divider" style={{ margin: "4px 0" }} />
          <button role="menuitem" style={{ ...item, color: "var(--fg-1)" }} onClick={(e) => { setOpen(false); onDuplicate(e); }}><Icon name="copy" size={15} />Duplicate</button>
          <button role="menuitem" style={{ ...item, color: "var(--err)" }} onClick={(e) => { setOpen(false); onDelete(e); }}><Icon name="trash" size={15} />Delete</button>
        </div>
      )}
    </div>
  );
}

/* ============ NEW TOOL ============ */
function NewToolModal({ project, open, onClose, onOpenTool, onReload }: { project: any; open: boolean; onClose: () => void; onOpenTool: (t: Tool) => void; onReload: () => void }) {
  const [kind, setKind] = useState("rest_api");
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [builtin, setBuiltin] = useState("current_time");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setKind("rest_api"); setName(""); setDisplayName(""); setDescription(""); setBuiltin("current_time"); setErr(null);
  }, [open]);

  async function create() {
    setBusy(true); setErr(null);
    try {
      const nm = (name || "untitled_tool").trim().replace(/\s+/g, "_");
      const dn = displayName.trim();
      const config: Record<string, unknown> =
        kind === "rest_api" ? { description, request: { method: "GET", url_template: "https://api.example.com/resource", fields: [], headers: [{ name: "Accept", value: "application/json" }] }, response: {} }
        : kind === "graphql" ? { description, endpoint: "https://api.example.com/graphql", query: "query { __typename }", variables: [] }
        : kind === "code" ? { description, language: "python", source: "def main(text):\n    return text.upper()\n", args_schema: { properties: { text: { type: "string", description: "input text" } }, required: ["text"] }, timeout_seconds: 5 }
        : kind === "sql" ? { description, connection_ref: "secret://proj/db_url", query: "SELECT id, name FROM customers WHERE id = :id", args_schema: { properties: { id: { type: "integer" } }, required: ["id"] }, read_only: true, max_rows: 100 }
        : { description, builtin };
      if (dn) config.display_name = dn;
      const tool = await api.createTool(project.id, { name: nm, kind, config });
      onClose(); onReload(); onOpenTool(tool);
    } catch (e: any) { setErr(String(e?.message || e)); } finally { setBusy(false); }
  }

  return (
    <Modal open={open} onClose={onClose} title="New tool" width={520}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={create} disabled={busy}>{busy ? "Creating…" : "Create tool"}</button></>}>
      <Field label="Kind"><Segmented options={[{ value: "rest_api", label: "REST" }, { value: "graphql", label: "GraphQL" }, { value: "code", label: "Code" }, { value: "sql", label: "SQL" }, { value: "builtin", label: "Builtin" }]} value={kind} onChange={(v) => { setKind(v); setErr(null); }} /></Field>
      <Field label="Name" help="The identifier the model calls (letters, numbers, underscores)."><input className="input mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="get_order" /></Field>
      <Field label="Display name" help="Optional. Human-readable name shown in chat/streaming activity; leave blank to show the identifier."><input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Get order" /></Field>
      <Field label="Description" help="What the model reads to decide when to call this tool."><textarea className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
      {kind === "builtin" && <Field label="Builtin" help={builtin === "knowledge_search" ? "Lets an agent search this project's knowledge base (docs + Q&A) itself - one call per sub-question, optional folder filter." : undefined}><Segmented options={[{ value: "current_time", label: "Time" }, { value: "calculator", label: "Calc" }, { value: "web_fetch", label: "Fetch" }, { value: "web_search", label: "Search" }, { value: "knowledge_search", label: "Knowledge" }, { value: "remember", label: "Remember" }, { value: "recall", label: "Recall" }]} value={builtin} onChange={setBuiltin} /></Field>}
      {err && <div className="card" style={{ padding: 12, color: "var(--err)", marginTop: 4 }}>{err}</div>}
    </Modal>
  );
}

/* ============ TOOL BUILDER ============ */
export function ToolBuilderScreen({ project, toolId, onBack }: { project: any; toolId?: string; onBack: () => void }) {
  const [tool, setTool] = useState<Tool | null>(null);
  const [providers, setProviders] = useState<AuthProviderT[]>([]);
  const [tab, setTab] = useState("request");
  const [draft, setDraft] = useState<any>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [result, setResult] = useState<ToolTestResult | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    if (project?.id && toolId) api.getTool(project.id, toolId).then((t) => {
      setTool(t); setDraft(buildDraft(t));
      // Restore the last live test so the panel shows the real payload on reopen,
      // not the sample placeholder.
      const lt = (t.config as any)?._last_test;
      if (lt && (lt.raw !== undefined || lt.projected !== undefined)) {
        setResult({ ok: true, status: lt.status, latency_ms: lt.latency_ms, raw: lt.raw, projected: lt.projected, raw_tokens: lt.raw_tokens, projected_tokens: lt.projected_tokens, final_url: lt.final_url, redirect: lt.redirect });
      }
    }).catch(() => setTool(null));
    if (project?.id) api.listAuthProviders(project.id).then(setProviders).catch(() => {});
  }, [project?.id, toolId, reloadKey]);

  if (!tool || !draft) return <div className="col center" style={{ flex: 1, color: "var(--fg-2)" }}>Loading tool…</div>;

  const isRest = tool.kind === "rest_api";
  const isGraphql = tool.kind === "graphql";
  const isCode = tool.kind === "code";
  const isSql = tool.kind === "sql";
  const llmFields = (draft.fields || []).filter((f: any) => f.llm_visible !== false);

  function set(patch: any) { setDraft((d: any) => ({ ...d, ...patch })); setSaved(false); }

  async function save() {
    setSaving(true);
    try {
      const config = draftToConfig(tool!, draft);
      const updated = await api.updateTool(project.id, tool!.id, { config, auth_provider_id: draft.auth_provider_id || null });
      setTool(updated); setDraft(buildDraft(updated)); setSaved(true);
    } catch (e) { /* surfaced via Save button state */ } finally { setSaving(false); }
  }

  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div className="row spread" style={{ padding: "14px 22px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        <div className="row gap3">
          <button className="iconbtn" onClick={onBack}><Icon name="chevleft" size={18} /></button>
          <Tile icon={KIND_ICON[tool.kind] || "k_rest"} color="var(--accent)" size={40} glow />
          <div>
            <div className="row gap2"><span className="t-display mono" style={{ fontSize: 18 }}>{tool.name}</span><span className="chip chip-mono">{KIND_LABEL[tool.kind] || tool.kind}</span><StatusPill status={tool.last_tested || "untested"} /></div>
            <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{draft.description || "No description."}</div>
          </div>
        </div>
        <div className="row gap2">
          <VersionHistory entityType="tool" entityId={tool.id} entityLabel={tool.name} buttonClassName="btn btn-secondary" onRestored={() => setReloadKey((k) => k + 1)} />
          <button className="btn btn-primary" onClick={save} disabled={saving}><Icon name="save" size={15} />{saving ? "Saving…" : saved ? "Saved ✓" : "Save"}</button>
        </div>
      </div>

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", minHeight: 0 }}>
        {/* LEFT: config */}
        <div className="col" style={{ borderRight: "1px solid var(--line)", minHeight: 0 }}>
          <div style={{ padding: "0 18px", borderBottom: "1px solid var(--line)" }}>
            <Tabs tabs={[{ value: "request", label: "Request" }, { value: "schema", label: "Input schema" }, { value: "projection", label: "Projection" }, { value: "auth", label: "Auth" }]} value={tab} onChange={setTab} />
          </div>
          <div className="scroll-y" style={{ flex: 1, padding: 18 }}>
            {tab === "request" && (
              <div className="fade-in">
                <Field label="Display name" help={`Human-readable name shown in chat/streaming activity. The model still calls this tool by its identifier (${tool.name}); leave blank to show the identifier.`}><input className="input" value={draft.display_name} onChange={(e) => set({ display_name: e.target.value })} placeholder={tool.name} /></Field>
                <Field label="Description" help="What the model reads to decide when to call this tool."><textarea className="textarea" rows={2} value={draft.description} onChange={(e) => set({ description: e.target.value })} /></Field>
                {(isRest || isGraphql) ? (
                  <>
                    <Field label="Method & URL">
                      <div className="row gap2">
                        {isRest && (
                          <div style={{ width: 96, position: "relative" }}>
                            <select className="select" value={draft.method} onChange={(e) => set({ method: e.target.value })}>{["GET", "POST", "PUT", "DELETE", "PATCH"].map((m) => <option key={m}>{m}</option>)}</select>
                            <Icon name="chevdown" size={13} style={{ position: "absolute", right: 8, top: 9, pointerEvents: "none", color: "var(--fg-2)" }} />
                          </div>
                        )}
                        <input className="input mono" value={draft.url} onChange={(e) => set({ url: e.target.value })} style={{ flex: 1 }} />
                      </div>
                    </Field>
                    {isRest && <Field label="Headers" help="Values interpolate {{ctx.*}} (run context — per-request values injected by the caller) and {{input.*}}. Example: Authorization = Bearer {{ctx.token}}."><KVEditor rows={draft.headers} onChange={(rows) => set({ headers: rows })} /></Field>}
                    {isRest && (
                      <Field label="Body encoding" help="How the request body is sent. Auto: a structured body is JSON, a body template is sent as-is. Form (urlencoded): in:body fields are URL-encoded and the Content-Type is set automatically — use for classic HTML form posts, including multi-line values and repeated keys. Raw: send the Body template string verbatim.">
                        <div style={{ position: "relative" }}>
                          <select className="select" value={draft.body_encoding} onChange={(e) => set({ body_encoding: e.target.value })}>
                            {[["", "Auto"], ["json", "JSON"], ["form", "Form (urlencoded)"], ["raw", "Raw"]].map((o) => <option key={o[0]} value={o[0]}>{o[1]}</option>)}
                          </select>
                          <Icon name="chevdown" size={13} style={{ position: "absolute", right: 9, top: 9, pointerEvents: "none", color: "var(--fg-2)" }} />
                        </div>
                      </Field>
                    )}
                    {isRest && <Field label="Body template" help={'Interpolates {{input.*}} (validated tool args) and {{ctx.*}} (run context). Parsed as JSON when possible, else sent as raw content. To repeat an array element per item (batch many items in one call), use a loop: "items": {"$each": "{{input.items}}", "$as": "item", "$do": { "label": "{{item.label}}" }}. For form posts, prefer in:body fields with Body encoding = Form (auto URL-encoded) over hand-encoding here.'}><textarea className="textarea mono" rows={4} value={draft.body} onChange={(e) => set({ body: e.target.value })} placeholder={'{\n  "name": "{{ input.name }}",\n  "count": {{ input.count }}\n}'} /></Field>}
                    {isRest && (
                      <details style={{ margin: "-4px 0 8px", paddingLeft: 2 }}>
                        <summary className="t-caption fg-2" style={{ cursor: "pointer", userSelect: "none" }}>How to write the body template</summary>
                        <div className="t-caption fg-2" style={{ marginTop: 8, lineHeight: 1.6, display: "flex", flexDirection: "column", gap: 10 }}>
                          <div>
                            The body is JSON. Insert values with <code className="mono">{"{{input.<arg>}}"}</code> (the validated tool args) and <code className="mono">{"{{ctx.<key>}}"}</code> (run context — per-request values supplied by the caller). It is parsed as JSON when possible, otherwise sent as raw text.
                          </div>
                          <div>
                            <b style={{ color: "var(--fg-1)" }}>Type rule:</b> a value that is <i>exactly</i> one token keeps its native type — <code className="mono">{"\"{{input.count}}\""}</code> stays a number, an array stays an array. Wrapping a token in other text, like <code className="mono">{"\"[{{input.count}}]\""}</code>, makes the result a string. Do not add brackets around a token that is already an array.
                          </div>
                          <div>
                            <div style={{ fontWeight: 600, color: "var(--fg-1)", marginBottom: 5 }}>Normal — one fixed body</div>
                            <pre className="mono" style={{ background: "var(--bg-0)", border: "1px solid var(--line)", borderRadius: 6, padding: "9px 11px", margin: 0, overflowX: "auto", fontSize: 12, color: "var(--fg-1)" }}>{`{
  "name": "{{input.name}}",
  "count": {{input.count}},
  "active": {{input.active}}
}`}</pre>
                          </div>
                          <div>
                            <div style={{ fontWeight: 600, color: "var(--fg-1)", marginBottom: 5 }}>Looping — repeat an element per array item</div>
                            <div style={{ marginBottom: 5 }}>
                              Use <code className="mono">$each</code> to expand a list-valued arg into a variable-length array — send many items in one call. <code className="mono">$each</code> is the source list, <code className="mono">$as</code> names each item, and <code className="mono">$do</code> is rendered once per item.
                            </div>
                            <pre className="mono" style={{ background: "var(--bg-0)", border: "1px solid var(--line)", borderRadius: 6, padding: "9px 11px", margin: 0, overflowX: "auto", fontSize: 12, color: "var(--fg-1)" }}>{`{
  "listId": "{{input.listId}}",
  "items": {
    "$each": "{{input.items}}",
    "$as": "item",
    "$do": {
      "label": "{{item.label}}",
      "value": "{{item.value}}",
      "tags": "{{item.tags}}"
    }
  }
}`}</pre>
                            <div style={{ marginTop: 5 }}>
                              With <code className="mono">items</code> = <code className="mono">{"[{\"label\":\"A\",\"value\":10,\"tags\":[\"x\"]}]"}</code>, this builds one <code className="mono">items</code> entry per element, each field keeping its native type (so <code className="mono">tags</code> stays an array).
                            </div>
                          </div>
                        </div>
                      </details>
                    )}
                    {isGraphql && <Field label="Query"><textarea className="textarea mono" rows={6} value={draft.query} onChange={(e) => set({ query: e.target.value })} /></Field>}
                    <Field label="Follow redirects" help="Follow 3xx redirects to the target URL. Each hop is re-checked by the SSRF guard. When off, the redirect's target URL is still reported to the model so it can act on it.">
                      <Toggle on={!!draft.follow_redirects} onChange={(v) => set({ follow_redirects: v })} />
                    </Field>
                    <Field label="Skip TLS verification" help="Disable TLS certificate checks for this call. Honored only when the target host is on the server's allow-listed internal hosts (FORGE_EGRESS_ALLOW_PRIVATE_HOSTS); ignored otherwise. Use for internal/dev services with self-signed certificates — never for public endpoints.">
                      <Toggle on={!!draft.tls_skip_verify} onChange={(v) => set({ tls_skip_verify: v })} />
                    </Field>
                  </>
                ) : isCode ? (
                  <>
                    <Field label="Python source" help="Sandboxed (RestrictedPython). Define def main(**kwargs): return … - imports limited to pure stdlib.">
                      <textarea className="textarea mono" rows={8} value={draft.source} onChange={(e) => set({ source: e.target.value })} placeholder={"def main(text):\n    return text.upper()"} />
                    </Field>
                    <Field label="Arguments (JSON Schema)" help='The LLM-visible args, e.g. {"properties": {"text": {"type": "string"}}, "required": ["text"]}'>
                      <textarea className="textarea mono" rows={5} value={draft.args_schema} onChange={(e) => set({ args_schema: e.target.value })} />
                    </Field>
                  </>
                ) : isSql ? (
                  <>
                    <Field label="Connection secret" help="A secret holding the SQLAlchemy DB URL (e.g. postgresql+psycopg://…). Manage in Settings → Secrets.">
                      <input className="input mono" value={draft.connection_ref} onChange={(e) => set({ connection_ref: e.target.value })} placeholder="secret://proj/db_url" />
                    </Field>
                    <Field label="Query" help="Parameterized SELECT using :named binds. Read-only is enforced by default.">
                      <textarea className="textarea mono" rows={5} value={draft.query} onChange={(e) => set({ query: e.target.value })} placeholder="SELECT id, name FROM customers WHERE id = :id" />
                    </Field>
                    <Field label="Arguments (JSON Schema)" help="The query's :named parameters as JSON Schema.">
                      <textarea className="textarea mono" rows={4} value={draft.args_schema} onChange={(e) => set({ args_schema: e.target.value })} />
                    </Field>
                    <div className="row gap3">
                      <Field label="Read-only"><Toggle on={draft.read_only} onChange={(v) => set({ read_only: v })} /></Field>
                      <Field label="Max rows"><input className="input mono" type="number" value={draft.max_rows} onChange={(e) => set({ max_rows: e.target.value })} /></Field>
                    </div>
                  </>
                ) : (
                  <div className="card" style={{ padding: 14, background: "var(--bg-3)" }}>
                    <div className="row gap2"><Icon name="k_builtin" size={16} style={{ color: "var(--accent)" }} /><span className="mono-sm">builtin · {draft.builtin}</span></div>
                    <div className="fg-2 t-caption" style={{ marginTop: 6 }}>Builtins run in-process with no network. Test it on the right.</div>
                  </div>
                )}
              </div>
            )}
            {tab === "schema" && (
              <div className="fade-in">
                <div className="fg-1" style={{ marginBottom: 12 }}>Request parameters. <b>Model-visible</b> fields become the tool&apos;s JSON Schema (the agent fills them). Turn <b>model-visible</b> off and give a <span className="mono">{"{{ctx.*}}"}</span> default to inject a per-run value (e.g. an auth token) the model never sees.</div>
                <FieldsEditor fields={draft.fields} onChange={(fields) => set({ fields })} />
              </div>
            )}
            {tab === "projection" && (
              <div className="fade-in">
                <div className="card" style={{ padding: 12, marginBottom: 14, background: "var(--signal-glow)", borderColor: "transparent" }}>
                  <div className="row gap2"><Icon name="bolt" size={16} style={{ color: "var(--signal)" }} /><span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--signal)" }}>Projection trims the raw response before it reaches the model context.</span></div>
                </div>
                <Field label="Projection expression" help="JMESPath over the response the model sees. Only projected keys count toward context tokens. On a redirect the response is {body, redirect} — use redirect.location to keep just the target URL."><textarea className="textarea mono" rows={3} value={draft.projection} onChange={(e) => set({ projection: e.target.value })} /></Field>
                <Field label="On error">
                  <div style={{ position: "relative" }}>
                    <select className="select" value={draft.on_error} onChange={(e) => set({ on_error: e.target.value })}>
                      {[["return_message", "Return error message to model"], ["raise", "Raise & stop run"], ["retry", "Retry (handled by middleware)"]].map((o) => <option key={o[0]} value={o[0]}>{o[1]}</option>)}
                    </select>
                    <Icon name="chevdown" size={13} style={{ position: "absolute", right: 9, top: 9, pointerEvents: "none", color: "var(--fg-2)" }} />
                  </div>
                </Field>
              </div>
            )}
            {tab === "auth" && (
              <div className="fade-in">
                <Field label="Auth provider" help="Reusable credential + session strategy. Secrets resolved at call time.">
                  <div style={{ position: "relative" }}>
                    <select className="select" value={draft.auth_provider_id || ""} onChange={(e) => set({ auth_provider_id: e.target.value })}>
                      <option value="">None</option>
                      {providers.map((p) => <option key={p.id} value={p.id}>{p.name} · {p.kind}</option>)}
                    </select>
                    <Icon name="chevdown" size={13} style={{ position: "absolute", right: 9, top: 9, pointerEvents: "none", color: "var(--fg-2)" }} />
                  </div>
                </Field>
                {draft.auth_provider_id && (() => {
                  const p = providers.find((x) => x.id === draft.auth_provider_id);
                  if (!p) return null;
                  return (
                    <div className="card" style={{ padding: 14, background: "var(--bg-3)" }}>
                      <div className="row spread" style={{ marginBottom: 8 }}><span className="t-h3 mono">{p.name}</span><StatusPill status="pass" /></div>
                      <div className="col gap1 mono-sm fg-1">
                        <div>strategy · {p.kind}</div>
                        <div>credentials · {p.credentials_ref || "-"}</div>
                      </div>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>
        </div>

        {/* RIGHT: live response */}
        <LiveResponse project={project} tool={tool} draft={draft} llmFields={llmFields} result={result} setResult={setResult} />
      </div>
    </div>
  );
}

function buildDraft(t: Tool): any {
  const c: any = t.config || {};
  const req: any = c.request || {};
  return {
    display_name: c.display_name || "",
    description: c.description || "",
    method: req.method || "GET",
    url: req.url_template || c.endpoint || "",
    headers: (req.headers || []).map((h: any) => [h.name, h.value]),
    body: req.body_template || "",
    body_encoding: req.body_encoding || "",
    follow_redirects: !!(req.follow_redirects ?? c.follow_redirects),
    tls_skip_verify: !!(req.tls_skip_verify ?? c.tls_skip_verify),
    query: c.query || "",
    builtin: c.builtin || "current_time",
    fields: (req.fields || c.variables || []).map((f: any) => ({ ...f })),
    projection: c.response?.projection_jmespath || c.response?.projection || "",
    on_error: c.response?.on_error || "return_message",
    auth_provider_id: t.auth_provider_id || "",
    // code / sql
    source: c.source || "",
    args_schema: c.args_schema ? JSON.stringify(c.args_schema, null, 2) : "",
    connection_ref: c.connection_ref || "",
    read_only: c.read_only !== false,
    max_rows: c.max_rows ?? 100,
  };
}

function parseSchema(s: string): any {
  try { return s.trim() ? JSON.parse(s) : {}; } catch { return {}; }
}

function draftToConfig(t: Tool, d: any): any {
  const base: any = { ...(t.config || {}), description: d.description };
  // Blank display name => omit it, so streaming falls back to the underscore identifier.
  if ((d.display_name || "").trim()) base.display_name = d.display_name.trim(); else delete base.display_name;
  delete base._last_test;
  if (t.kind === "rest_api") {
    const request: any = {
      ...(base.request || {}), method: d.method, url_template: d.url,
      headers: (d.headers || []).filter((r: any) => r[0]).map((r: any) => ({ name: r[0], value: r[1] })),
      fields: d.fields, follow_redirects: !!d.follow_redirects, tls_skip_verify: !!d.tls_skip_verify,
    };
    // The saved config REPLACES the old one, but `...base.request` above carries the previous
    // values forward - so an empty selection must explicitly DELETE the key, or "Auto" body
    // encoding (and a cleared body template) silently revert to the last saved value.
    if (d.body_encoding) request.body_encoding = d.body_encoding; else delete request.body_encoding;
    if (d.body) request.body_template = d.body; else delete request.body_template;
    base.request = request;
    base.response = { ...(base.response || {}), ...(d.projection ? { projection_jmespath: d.projection } : {}), on_error: d.on_error };
  } else if (t.kind === "graphql") {
    base.endpoint = d.url; base.query = d.query; base.variables = d.fields; base.follow_redirects = !!d.follow_redirects; base.tls_skip_verify = !!d.tls_skip_verify;
    base.response = { ...(base.response || {}), ...(d.projection ? { projection_jmespath: d.projection } : {}), on_error: d.on_error };
  } else if (t.kind === "code") {
    base.language = "python"; base.source = d.source; base.args_schema = parseSchema(d.args_schema);
    base.timeout_seconds = base.timeout_seconds || 5;
  } else if (t.kind === "sql") {
    base.connection_ref = d.connection_ref; base.query = d.query; base.args_schema = parseSchema(d.args_schema);
    base.read_only = d.read_only; base.max_rows = Number(d.max_rows) || 100;
  } else {
    base.builtin = d.builtin;
  }
  return base;
}

function KVEditor({ rows, onChange }: { rows: [string, string][]; onChange: (r: [string, string][]) => void }) {
  return (
    <div className="col gap2">
      {rows.map((r, i) => (
        <div key={i} className="row gap2">
          <input className="input mono" value={r[0]} onChange={(e) => { const c = [...rows]; c[i] = [e.target.value, r[1]]; onChange(c); }} style={{ flex: "0 0 36%" }} placeholder="Header" />
          <input className="input mono" value={r[1]} onChange={(e) => { const c = [...rows]; c[i] = [r[0], e.target.value]; onChange(c); }} style={{ flex: 1 }} placeholder="Value" />
          <button className="iconbtn" onClick={() => onChange(rows.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
        </div>
      ))}
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => onChange([...rows, ["", ""]])}><Icon name="plus" size={13} />Add header</button>
    </div>
  );
}

function FieldsEditor({ fields, onChange }: { fields: any[]; onChange: (f: any[]) => void }) {
  // Per-field description is optional and collapsed by default (the row is already dense);
  // a field that already has one starts expanded so its content is visible.
  const [descOpen, setDescOpen] = useState<Record<number, boolean>>({});
  function upd(i: number, patch: any) { const c = fields.map((f, j) => (j === i ? { ...f, ...patch } : f)); onChange(c); }
  return (
    <div className="col gap2">
      {fields.map((f, i) => (
        <div key={i} className="col gap2" style={{ padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 9 }}>
          <div className="row gap2">
            <input className="input mono" value={f.path || ""} onChange={(e) => upd(i, { path: e.target.value })} placeholder="field" style={{ flex: 1 }} />
            <div style={{ width: 92 }}>
              <select className="select" value={f.type || "string"} onChange={(e) => upd(i, { type: e.target.value })}>{["string", "integer", "number", "boolean", "array", "object"].map((t) => <option key={t}>{t}</option>)}</select>
            </div>
            <div style={{ width: 92 }}>
              <select className="select" value={f.in || "query"} onChange={(e) => upd(i, { in: e.target.value })} title="Where this value is placed in the outbound request">{["query", "header", "path", "body", "cookie"].map((t) => <option key={t}>{t}</option>)}</select>
            </div>
            <button className="chip" title="required" onClick={() => upd(i, { required: !f.required })} style={{ cursor: "pointer", color: f.required ? "var(--err)" : "var(--fg-2)" }}>{f.required ? "required" : "optional"}</button>
            <button className="iconbtn" title="remove" onClick={() => onChange(fields.filter((_, j) => j !== i))}><Icon name="x" size={14} /></button>
          </div>
          <div className="row gap2" style={{ alignItems: "center" }}>
            <input className="input mono" value={f.default ?? ""} onChange={(e) => upd(i, { default: e.target.value || undefined })} placeholder="default — supports {{ctx.*}} (run context) / {{input.*}}" style={{ flex: 1 }} />
            <label className="row gap1" title="On: the model decides this value (appears in the tool's args schema). Off: the server injects it (e.g. default {{ctx.token}}) and it is hidden from the model." style={{ whiteSpace: "nowrap", cursor: "pointer" }}>
              <Toggle on={f.llm_visible !== false} onChange={() => upd(i, { llm_visible: f.llm_visible === false })} />
              <span className="t-caption fg-2">model-visible</span>
            </label>
          </div>
          {/* Per-arg description (fed to the args schema for model-visible fields only),
              collapsed by default to keep the row uncluttered; expand to add allowed values
              / format guidance, e.g. the vendor keys. */}
          {f.llm_visible !== false && ((descOpen[i] ?? !!f.description) ? (
            <div className="col gap1">
              <textarea className="textarea mono" rows={2} value={f.description ?? ""} onChange={(e) => upd(i, { description: e.target.value || undefined })} placeholder="description the model reads for this arg — e.g. allowed values" style={{ width: "100%" }} />
              <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setDescOpen((s) => ({ ...s, [i]: false }))}><Icon name="minus" size={13} />Hide description</button>
            </div>
          ) : (
            <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setDescOpen((s) => ({ ...s, [i]: true }))}><Icon name="plus" size={13} />Description</button>
          ))}
        </div>
      ))}
      <button className="btn btn-ghost btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => onChange([...fields, { path: "new_field", type: "string", in: "query", required: false, llm_visible: true }])}><Icon name="plus" size={13} />Add field</button>
    </div>
  );
}

/* RIGHT panel - the token-meter signature: real /test, or a client-side projection preview. */
function LiveResponse({ project, tool, draft, llmFields, result, setResult }: { project: any; tool: Tool; draft: any; llmFields: any[]; result: ToolTestResult | null; setResult: (r: ToolTestResult | null) => void }) {
  const [args, setArgs] = useState<Record<string, string>>({});
  const [ctxText, setCtxText] = useState("");
  const [sample, setSample] = useState(JSON.stringify(DEFAULT_SAMPLE, null, 2));
  const [busy, setBusy] = useState(false);
  const [animKey, setAnimKey] = useState(0);

  // Client-side preview (before a real test): apply the projection to the editable sample.
  const preview = useMemo(() => {
    let parsed: any = null;
    try { parsed = JSON.parse(sample); } catch { return { raw: 0, projected: 0, err: "Invalid sample JSON", projObj: null }; }
    const rawT = estTokens(parsed);
    if (!draft.projection?.trim()) return { raw: rawT, projected: rawT, err: null, projObj: parsed };
    try { const out = jmespath.search(parsed, draft.projection); return { raw: rawT, projected: estTokens(out), err: null, projObj: out }; }
    catch { return { raw: rawT, projected: rawT, err: "Invalid JMESPath", projObj: parsed }; }
  }, [sample, draft.projection]);

  async function run() {
    setBusy(true); setResult(null);
    try {
      let context: Record<string, unknown> | undefined;
      if (ctxText.trim()) context = JSON.parse(ctxText);
      const res = await api.testTool(project.id, tool.id, { ...args }, context);
      setResult(res); setAnimKey((k) => k + 1);
    } catch (e: any) {
      setResult({ ok: false, error: e.message || String(e) });
    } finally { setBusy(false); }
  }

  const tested = result && result.ok;
  const rawTok = tested ? (result!.raw_tokens ?? 0) : preview.raw;
  const projTok = tested ? (result!.projected_tokens ?? rawTok) : preview.projected;
  const rawObj = tested ? result!.raw : preview.projObj == null ? null : JSON.parse(sample);
  const projObj = tested ? result!.projected : preview.projObj;

  return (
    // minWidth:0 + overflow hidden: wide JSON payloads otherwise blow the 1fr grid
    // column open (grid items default to min-width:auto) and shift the whole layout.
    <div className="col" style={{ minHeight: 0, minWidth: 0, overflow: "hidden", background: "var(--bg-0)" }}>
      <div className="row spread" style={{ padding: "12px 18px", borderBottom: "1px solid var(--line)" }}>
        <div className="row gap2"><Icon name="play" size={15} style={{ color: "var(--signal)" }} /><span className="t-h2">Live response</span></div>
        {tested && <span className="chip chip-mono">{result!.status}{result!.latency_ms != null ? ` · ${result!.latency_ms}ms` : ""}</span>}
      </div>
      <div className="scroll-y" style={{ flex: 1, padding: 18 }}>
        {/* test inputs */}
        <div className="card" style={{ padding: 14, marginBottom: 14 }}>
          <div className="t-micro" style={{ marginBottom: 10 }}>Test inputs</div>
          {llmFields.length > 0 ? llmFields.map((f) => (
            <Field key={f.path} label={f.path}>
              {/* string/array fields get a textarea so multi-line values (e.g. one product per
                  line for a form post) can be entered; scalars stay single-line. */}
              {f.type === "string" || f.type === "array"
                ? <textarea className="textarea mono" rows={2} placeholder={f.type} value={args[f.path] || ""} onChange={(e) => setArgs((a) => ({ ...a, [f.path]: e.target.value }))} />
                : <input className="input mono" placeholder={f.type} value={args[f.path] || ""} onChange={(e) => setArgs((a) => ({ ...a, [f.path]: e.target.value }))} />}
            </Field>
          )) : <Field label="Arguments (JSON)"><textarea className="textarea mono" rows={2} placeholder='{ }' onChange={(e) => { try { setArgs(JSON.parse(e.target.value || "{}")); } catch { /* */ } }} /></Field>}
          <Field label="Run context (auth values for the call)"><textarea className="textarea mono" rows={2} placeholder='{ "token": "…" }' value={ctxText} onChange={(e) => setCtxText(e.target.value)} /></Field>
          <button className="btn btn-secondary" style={{ width: "100%" }} onClick={run} disabled={busy}><Icon name={busy ? "refresh" : "validate"} size={15} style={busy ? { animation: "spin 1s linear infinite" } : {}} />{busy ? "Testing…" : "Test"}</button>
          {result && !result.ok && <div className="t-caption" style={{ color: "var(--err)", marginTop: 8 }}>{result.error}</div>}
        </div>

        {/* redirect banner - the API redirected; show where (and whether we followed) */}
        {tested && result!.redirect && (
          <div className="card" style={{ padding: 12, marginBottom: 14, borderColor: "var(--accent)" }}>
            <div className="row gap2" style={{ marginBottom: 4 }}>
              <Icon name="auth" size={15} style={{ color: "var(--accent)" }} />
              <span style={{ fontSize: 12.5, fontWeight: 600 }}>
                {result!.redirect!.followed ? "Followed redirect" : `Redirect (${result!.redirect!.status}) - not followed`}
              </span>
            </div>
            <div className="mono-sm fg-1" style={{ wordBreak: "break-all" }}>
              {result!.redirect!.followed
                ? <>→ {result!.redirect!.final_url}</>
                : <>→ {result!.redirect!.location || "no Location header"}</>}
            </div>
            {!result!.redirect!.followed && result!.redirect!.location && (
              <div className="fg-2 t-caption" style={{ marginTop: 6 }}>Enable “Follow redirects” on the Request tab to fetch the target automatically.</div>
            )}
          </div>
        )}

        {/* the signature token meter */}
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div className="row spread" style={{ marginBottom: 12 }}>
            <span className="t-h2">Context cost</span>
            <button className="btn btn-ghost btn-sm" onClick={() => setAnimKey((k) => k + 1)}><Icon name="refresh" size={13} />Replay</button>
          </div>
          <BigTokenMeter raw={rawTok} projected={projTok} animateKey={animKey + ":" + rawTok + ":" + projTok} />
          {!tested && <div className="fg-2 t-caption" style={{ marginTop: 8 }}>Preview from the editable sample below. Run Test for the live payload.</div>}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: 12 }}>
          <div style={{ minWidth: 0 }}>
            <div className="row gap2" style={{ marginBottom: 6 }}><span className="t-micro">Raw response</span><span className="chip" style={{ height: 18, color: "var(--fg-2)" }}>{rawTok} tok</span></div>
            <pre className="mono no-scrollbar" style={{ margin: 0, padding: 12, background: "var(--bg-1)", border: "1px solid var(--line)", borderRadius: 8, fontSize: 11, maxHeight: 280, overflow: "auto", color: "var(--fg-1)" }}>{JSON.stringify(rawObj, null, 2)}</pre>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="row gap2" style={{ marginBottom: 6 }}><span className="t-micro" style={{ color: "var(--signal)" }}>Projected → model</span><span className="pill pill-ok" style={{ height: 18 }}>{projTok} tok</span></div>
            <pre className="mono no-scrollbar" style={{ margin: 0, padding: 12, background: "var(--bg-1)", borderRadius: 8, fontSize: 11, maxHeight: 280, overflow: "auto", color: "var(--fg-1)", boxShadow: "0 0 0 1px var(--signal), 0 0 18px var(--signal-glow)" }}>{JSON.stringify(projObj, null, 2)}</pre>
          </div>
        </div>

        {!tested && (
          <Field
            label="Try projection on a sample (no live call)"
            help="Paste an example of what this API returns, then tweak the Projection expression on the left - the meter above previews the token savings. Disappears once a real Test has run (the live payload is shown and remembered instead)."
          >
            <textarea className="textarea mono" value={sample} onChange={(e) => setSample(e.target.value)} rows={7} style={{ fontSize: 11.5, marginTop: 12 }} />
          </Field>
        )}
      </div>
    </div>
  );
}

function BigTokenMeter({ raw, projected, animateKey }: { raw: number; projected: number; animateKey: any }) {
  const [phase, setPhase] = useState<"raw" | "proj">("raw");
  useEffect(() => { setPhase("raw"); const t = setTimeout(() => setPhase("proj"), 420); return () => clearTimeout(t); }, [animateKey]);
  const pct = raw > 0 && phase === "proj" ? (projected / raw) * 100 : 100;
  const saved = raw > 0 ? Math.round((1 - projected / raw) * 100) : 0;
  return (
    <div className="col gap3">
      <div style={{ position: "relative", height: 38, borderRadius: 10, background: "var(--bg-3)", overflow: "hidden", border: "1px solid var(--line)" }}>
        <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", paddingLeft: 12, fontSize: 11, color: "var(--fg-2)", fontFamily: "var(--font-mono)" }}>raw {raw.toLocaleString()} tok</div>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: pct + "%", borderRadius: 10, background: phase === "proj" ? "linear-gradient(90deg, var(--signal-dim), var(--signal))" : "linear-gradient(90deg, var(--accent-dim), var(--accent))", transition: "width .7s var(--ease), background .4s", boxShadow: phase === "proj" ? "0 0 16px var(--signal-glow)" : "none", display: "flex", alignItems: "center", paddingLeft: 12, overflow: "hidden" }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: "#fff", fontFamily: "var(--font-mono)", whiteSpace: "nowrap" }}>{phase === "proj" ? projected.toLocaleString() + " tok" : raw.toLocaleString() + " tok"}</span>
        </div>
      </div>
      <div className="row spread">
        <span className="fg-2 t-caption">{phase === "proj" ? "Projected payload sent to the model" : "Full provider response"}</span>
        <span className="pill pill-ok"><Icon name="bolt" size={12} />{saved}% fewer tokens</span>
      </div>
    </div>
  );
}
