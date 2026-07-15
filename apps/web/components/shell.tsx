"use client";
/* Forge app shell: topbar, project sidebar, command palette, assistant. */
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icons";
import { Avatar, Tile } from "./primitives";
import { PROJECT_NAV, NavLeaf } from "@/lib/data";
import { Markdown } from "./markdown";

/* ---------------- Theme hook ---------------- */
export function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = useState("light");
  useEffect(() => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    setTheme(cur);
  }, []);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  return [theme, setTheme];
}

/* ---------------- Global rail ---------------- */
function RailBtn({ icon, label, onClick, active }: { icon: string; label: string; onClick?: () => void; active?: boolean }) {
  const [hv, setHv] = useState(false);
  return (
    <div style={{ position: "relative" }} onMouseEnter={() => setHv(true)} onMouseLeave={() => setHv(false)}>
      <button className={"iconbtn" + (active ? " active" : "")} style={{ width: 38, height: 38 }} onClick={onClick}>
        <Icon name={icon} size={19} />
      </button>
      {hv && <div className="tooltip-pop" style={{ left: 46, top: 9 }}>{label}</div>}
    </div>
  );
}

export function GlobalRail({ theme, setTheme, onCommand, onAssistant, onHome }: { theme: string; setTheme: (t: string) => void; onCommand: () => void; onAssistant: () => void; onHome: () => void }) {
  return (
    <div style={{ width: 56, flex: "none", background: "var(--bg-1)", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", alignItems: "center", padding: "10px 0", gap: 4 }}>
      <button onClick={onHome} style={{ width: 34, height: 34, borderRadius: 9, background: "linear-gradient(140deg,var(--accent-bright),var(--accent-dim))", display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", marginBottom: 8, boxShadow: "0 2px 10px var(--accent-glow)", border: "none", cursor: "pointer" }}>
        <Icon name="flame" size={20} />
      </button>
      <RailBtn icon="search" label="Search  ⌘K" onClick={onCommand} />
      <RailBtn icon="sparkles" label="Forge Assistant" onClick={onAssistant} />
      <div style={{ flex: 1 }} />
      <RailBtn icon="theme" label={theme === "dark" ? "Light mode" : "Dark console"} onClick={() => setTheme(theme === "dark" ? "light" : "dark")} />
      <RailBtn icon="help" label="Ask the Forge Assistant" onClick={onAssistant} />
      <div style={{ marginTop: 6 }}><Avatar name="Riley Cho" size={30} /></div>
    </div>
  );
}

/* ---------------- Account menu (avatar + sign out) ---------------- */
function AccountMenu() {
  const [open, setOpen] = useState(false);
  const [me, setMe] = useState<{ email: string; role: string } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let live = true;
    import("@/lib/api")
      .then(({ api }) => api.me())
      .then((m: any) => { if (live) setMe({ email: m.email, role: m.role }); })
      .catch(() => {});
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);
  async function signOut() {
    const { clearTokens } = await import("@/lib/api");
    clearTokens();
    window.location.reload();
  }
  return (
    <div ref={ref} style={{ position: "relative", flex: "none" }}>
      <button onClick={() => setOpen((o) => !o)} title="Account" aria-label="Account"
        style={{ border: "none", background: "none", cursor: "pointer", padding: 0, borderRadius: "50%", display: "flex" }}>
        <Avatar name={me?.email || "You"} size={30} />
      </button>
      {open && (
        <div className="card fade-in" style={{ position: "absolute", top: "100%", right: 0, marginTop: 6, zIndex: 6000, minWidth: 210, padding: 6, boxShadow: "var(--sh-pop)" }}>
          <div style={{ padding: "6px 9px 8px" }}>
            <div className="t-body-sm truncate" style={{ fontWeight: 600 }}>{me?.email || "Signed in"}</div>
            {me?.role && <div className="t-caption fg-2" style={{ textTransform: "capitalize", marginTop: 1 }}>{me.role}</div>}
          </div>
          <div className="divider" style={{ margin: "2px 0 4px" }} />
          <button onClick={signOut}
            style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left", padding: "7px 9px", border: "none", background: "none", cursor: "pointer", borderRadius: 6, fontSize: 13, fontFamily: "var(--font-ui)", color: "var(--err)" }}>
            <Icon name="logout" size={15} />Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/* ---------------- Topbar ---------------- */
export interface Crumb { label: string; onClick?: () => void }
export function Topbar({ crumbs, right, left, onCommand }: { crumbs: Crumb[]; right?: ReactNode; left?: ReactNode; onCommand: () => void }) {
  return (
    <div style={{ height: 52, flex: "none", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", padding: "0 16px", gap: 12, background: "var(--bg-1)" }}>
      {left}
      <div className="row gap2" style={{ minWidth: 0 }}>
        {crumbs.map((c, i) => (
          <div key={i} className="row gap2" style={{ minWidth: 0 }}>
            {i > 0 && <Icon name="chevright" size={15} style={{ color: "var(--fg-2)", flex: "none" }} />}
            <button onClick={c.onClick} disabled={!c.onClick}
              style={{ background: "none", border: "none", cursor: c.onClick ? "pointer" : "default", padding: 0, fontFamily: i === crumbs.length - 1 ? "var(--font-display)" : "var(--font-ui)", fontSize: i === crumbs.length - 1 ? 16 : 13, fontWeight: 600, color: i === crumbs.length - 1 ? "var(--fg-0)" : "var(--fg-2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {c.label}
            </button>
          </div>
        ))}
      </div>
      <div style={{ flex: 1 }} />
      <button className="row gap2" onClick={onCommand}
        style={{ height: 32, padding: "0 10px", border: "1px solid var(--line-strong)", borderRadius: 6, background: "var(--bg-1)", cursor: "pointer", color: "var(--fg-2)", fontSize: 12.5 }}>
        <Icon name="search" size={15} />
        <span style={{ width: 110, textAlign: "left" }}>Search…</span>
        <span className="kbd">⌘K</span>
      </button>
      {right}
      <AccountMenu />
    </div>
  );
}

/* ---------------- Project sidebar ---------------- */
export function ProjectSidebar({ project, active, onNav, onBack, refreshKey }: { project: any; active: string; onNav: (id: string) => void; onBack: () => void; refreshKey?: any }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  // api.ts fires this after any create/delete of a counted resource, so the badges
  // refresh immediately instead of waiting for a page reload.
  const [countsBump, setCountsBump] = useState(0);
  useEffect(() => {
    const onChange = () => setCountsBump((n) => n + 1);
    window.addEventListener("forge:counts-changed", onChange);
    return () => window.removeEventListener("forge:counts-changed", onChange);
  }, []);
  useEffect(() => {
    const pid = project?.id;
    if (!pid) return;
    let live = true;
    const refresh = async () => {
      // One cheap counts call (COUNT(*) per resource) instead of fetching six full lists
      // just to read their `.length`. Re-runs on create/delete via countsBump so badges
      // stay in sync. A short poll also keeps the agent-inbox badge current when a workflow
      // opens a handoff while the operator is on another screen.
      const { api } = await import("@/lib/api");
      try {
        const c = await api.projectCounts(pid);
        if (live) setCounts(c as unknown as Record<string, number>);
      } catch {
        if (live) setCounts({});
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 15_000);
    return () => { live = false; window.clearInterval(timer); };
  }, [project?.id, refreshKey, countsBump]);
  const renderLeaf = (n: NavLeaf) => {
    const on = active === n.id;
    const count = n.countKey ? counts[n.countKey] : undefined;
    return (
      <button key={n.id} onClick={() => onNav(n.id)} title={n.help || n.label} className={"sidenav-item" + (on ? " active" : "")}
        style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", height: 34, padding: "0 10px", marginBottom: 1, borderRadius: 8, border: "none", cursor: "pointer", textAlign: "left", color: on ? "var(--accent)" : "var(--fg-1)", fontSize: 13, fontWeight: on ? 600 : 500, fontFamily: "var(--font-ui)", transition: "color var(--dur-fast)" }}>
        <Icon name={n.icon} size={16} style={{ flex: "none" }} />
        <span className="grow truncate">{n.label}</span>
        {count != null && count > 0 && <span className="badge" style={on ? { background: "var(--accent-glow)", color: "var(--accent)" } : {}}>{count}</span>}
      </button>
    );
  };
  // Settings is pinned to the bottom (rendered in the footer below), so drop it from the scroll list.
  const settingsLeaf = PROJECT_NAV.find((e): e is NavLeaf => "id" in e && e.id === "settings");
  return (
    <div style={{ width: 224, flex: "none", background: "var(--bg-1)", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <button onClick={onBack} className="row gap2" style={{ height: 52, flex: "none", padding: "0 14px", background: "none", border: "none", borderBottom: "1px solid var(--line)", cursor: "pointer", textAlign: "left", alignItems: "center" }}>
        <div className="t-h2 truncate" style={{ minWidth: 0, flex: 1 }}>{project?.name}</div>
        <Icon name="chevdown" size={15} style={{ color: "var(--fg-2)", flex: "none" }} />
      </button>
      <nav className="scroll-y" style={{ flex: 1, minHeight: 0, padding: 8 }}>
        {PROJECT_NAV.map((entry) => {
          if ("id" in entry && entry.id === "settings") return null; // pinned to the footer
          if ("section" in entry) {
            // Static section heading (like the design) - no collapse toggle.
            return (
              <div key={entry.section}>
                <div className="t-micro" style={{ letterSpacing: ".06em", textTransform: "uppercase", padding: "14px 10px 5px" }}>{entry.section}</div>
                {entry.items.map(renderLeaf)}
              </div>
            );
          }
          return renderLeaf(entry);
        })}
      </nav>
      {/* Settings pinned to the bottom: sits above the scrolling nav (z-index + solid bg + top
          shadow) so nav items scroll behind it on short viewports. */}
      {settingsLeaf && (
        <div style={{ flex: "none", position: "relative", zIndex: 2, padding: 8, borderTop: "1px solid var(--line)", background: "var(--bg-1)", boxShadow: "0 -6px 12px -8px rgba(0,0,0,.18)" }}>
          {renderLeaf(settingsLeaf)}
        </div>
      )}
    </div>
  );
}

/* ---------------- Command palette ---------------- */
export function CommandPalette({ open, onClose, onGo, projects }: { open: boolean; onClose: () => void; onGo: (v: any) => void; projects: { id: string; name: string }[] }) {
  const [q, setQ] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (open) { setQ(""); setTimeout(() => inputRef.current?.focus(), 30); }
  }, [open]);
  const cmds = useMemo(() => {
    const first = projects[0]?.id || "p_support";
    const list = [
      { sec: "Go to", label: "Home / Dashboard", icon: "dashboard", go: { name: "dashboard" } },
      { sec: "Go to", label: "Workflow Canvas - Support Router", icon: "workflows", go: { name: "project", project: first, screen: "workflow-canvas" } },
      { sec: "Go to", label: "Tool Builder", icon: "tools", go: { name: "project", project: first, screen: "tool-builder" } },
      { sec: "Go to", label: "Agent Config", icon: "agents", go: { name: "project", project: first, screen: "agent-config" } },
      { sec: "Go to", label: "Playground", icon: "playground", go: { name: "project", project: first, screen: "playground" } },
      { sec: "Go to", label: "Traces", icon: "traces", go: { name: "project", project: first, screen: "traces" } },
      { sec: "Go to", label: "Knowledge", icon: "knowledge", go: { name: "project", project: first, screen: "knowledge" } },
      { sec: "Go to", label: "Settings & Secrets", icon: "secret", go: { name: "project", project: first, screen: "settings" } },
      { sec: "Actions", label: "New project…", icon: "plus", go: { name: "onboarding" } },
    ];
    projects.forEach((p) => list.push({ sec: "Projects", label: p.name, icon: "layers", go: { name: "project", project: p.id, screen: "overview" } }));
    if (!q) return list;
    return list.filter((c) => c.label.toLowerCase().includes(q.toLowerCase()));
  }, [q, projects]);
  const groups = useMemo(() => { const g: Record<string, any[]> = {}; cmds.forEach((c) => (g[c.sec] = g[c.sec] || []).push(c)); return g; }, [cmds]);
  if (!open) return null;
  return (
    <div className="fade-in" style={{ position: "fixed", inset: 0, zIndex: 8500, background: "rgba(8,10,14,.45)", backdropFilter: "blur(3px)", display: "flex", justifyContent: "center", paddingTop: "12vh" }} onMouseDown={onClose}>
      <div className="card fade-up" style={{ width: 600, maxWidth: "92vw", height: "fit-content", maxHeight: "70vh", boxShadow: "var(--sh-pop)", display: "flex", flexDirection: "column", overflow: "hidden" }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="row gap2" style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)" }}>
          <Icon name="search" size={18} style={{ color: "var(--fg-2)" }} />
          <input ref={inputRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search projects, workflows, tools, actions…"
            style={{ flex: 1, border: "none", outline: "none", background: "none", fontSize: 15, color: "var(--fg-0)", fontFamily: "var(--font-ui)" }} />
          <span className="kbd">esc</span>
        </div>
        <div className="scroll-y" style={{ padding: 8 }}>
          {Object.entries(groups).map(([sec, items]) => (
            <div key={sec} style={{ marginBottom: 6 }}>
              <div className="t-micro" style={{ padding: "6px 8px 4px" }}>{sec}</div>
              {items.map((c, i) => (
                <button key={i} onClick={() => { onGo(c.go); onClose(); }} className="row gap3"
                  style={{ width: "100%", padding: "8px", border: "none", background: "none", cursor: "pointer", borderRadius: 7, textAlign: "left", color: "var(--fg-1)" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-3)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}>
                  <Icon name={c.icon} size={16} style={{ color: "var(--fg-2)" }} />
                  <span style={{ fontSize: 13.5, color: "var(--fg-0)" }}>{c.label}</span>
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------------- Forge Assistant ---------------- */
interface AsstMsg { role: "user" | "assistant"; content: string; thinking?: string; thinkSecs?: number }
interface AsstStep { name: string; result?: string; turn: number }
interface AsstTodo { content?: string; status?: string; [k: string]: any }

// Friendly labels for the inline "current step" line (the Steps drawer keeps raw names).
const TOOL_LABELS: Record<string, string> = {
  write_todos: "Planning the steps",
  list_resources: "Reviewing the project",
  describe_workflow: "Reading the workflow",
  list_node_types: "Checking available nodes",
  get_node_schema: "Checking node options",
  list_middleware_types: "Checking middleware",
  read_file: "Reading the platform guide",
  create_agent_preset: "Creating an agent",
  create_builtin_tool: "Adding a tool",
  create_rest_tool: "Adding a REST tool",
  create_auth_provider: "Adding an auth provider",
  add_qa_pair: "Adding a Q&A pair",
  add_knowledge_text: "Adding knowledge",
  create_grounded_workflow: "Building the workflow",
  create_intent_router_workflow: "Building the workflow",
  create_custom_workflow: "Building the workflow",
  add_human_review: "Adding a human-approval step",
  test_workflow: "Testing the workflow",
  evaluate_build: "Reviewing the result",
  delete_workflow: "Removing a workflow",
};
const prettyTool = (name: string) => TOOL_LABELS[name] || name.replace(/_/g, " ");

function newThreadId() {
  return `panel-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function AssistantPanel({ open, onClose, project, onMutate }: { open: boolean; onClose: () => void; project?: { id: string; name: string } | null; onMutate?: () => void }) {
  const [msgs, setMsgs] = useState<AsstMsg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState("");
  // GPT-style thinking: while the agent works through intermediate narration, the text
  // streams as dim auto-scrolling lines under a "Thinking" label; on finish it collapses
  // to "Thought for Xs" and only the final segment stays as the answer bubble.
  const [liveThink, setLiveThink] = useState<string | null>(null);
  const [openThought, setOpenThought] = useState<number | null>(null);
  // Tool/plan activity lives in the collapsible Steps drawer above the composer
  // (not as chips in the transcript).
  const [steps, setSteps] = useState<AsstStep[]>([]);
  const [currentTool, setCurrentTool] = useState<string | null>(null);
  const [stepsOpen, setStepsOpen] = useState(false);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [todos, setTodos] = useState<AsstTodo[]>([]);
  const [busy, setBusy] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<string | null>(null); // human-readable prompt
  const turnRef = useRef(0);
  // Server-side conversation: the backend checkpointer holds the thread (history,
  // plan, files), so each turn sends ONLY the new message under this thread id.
  const threadRef = useRef<string>(newThreadId());
  const scrollRef = useRef<HTMLDivElement>(null);
  const thinkRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { scrollRef.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [msgs, streaming, currentTool, pendingApproval, liveThink !== null]);
  // Auto-grow the composer up to ~6 lines, then scroll inside it.
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`;
  }, [input]);
  // The thinking ticker auto-scrolls its own little window as text streams.
  useEffect(() => { if (thinkRef.current) thinkRef.current.scrollTop = thinkRef.current.scrollHeight; }, [liveThink]);
  // New project = new conversation thread.
  useEffect(() => { threadRef.current = newThreadId(); setMsgs([]); setSteps([]); setTodos([]); setPendingApproval(null); setLiveThink(null); }, [project?.id]);

  function describeInterrupt(data: any): string {
    const flat = (x: any): any[] => (Array.isArray(x) ? x.flatMap(flat) : [x]);
    for (const item of flat(data?.interrupts ?? data ?? [])) {
      const v = item && typeof item === "object" && "value" in item ? item.value : item;
      const reqs = v?.action_requests || (v?.action_request ? [v.action_request] : v?.action ? [v] : null);
      if (reqs) {
        return reqs.map((r: any) => r.description || `${r.action || r.name || "action"}(${JSON.stringify(r.args || {}).slice(0, 100)})`).join("; ");
      }
      if (v?.prompt) return String(v.prompt);
    }
    return "The assistant wants to perform a sensitive action.";
  }

  async function streamTurn(body: Record<string, unknown>, url: string) {
    if (!project) return;
    setStreaming(""); setLiveThink(null); setBusy(true); setPendingApproval(null);
    const turn = ++turnRef.current;
    const turnStart = Date.now();
    let mutated = false; let interruptPrompt: string | null = null; let acted = false;
    // Segmentation: each AI message in the agent loop is one segment (the backend tags
    // tokens with the message id; a tool call also closes the segment). Everything
    // before the LAST segment is "thinking"; the last segment is the answer.
    let cur = "";                  // current segment
    let segId: string | null = null;
    const done: string[] = [];     // completed (thinking) segments
    let thinking = false;          // becomes true on first tool call / segment change

    const render = () => {
      if (thinking) {
        setStreaming("");
        setLiveThink([...done, cur].filter(Boolean).join("\n\n"));
      } else {
        setStreaming(cur);
      }
    };
    const closeSegment = () => {
      if (cur.trim()) done.push(cur.trim());
      cur = "";
    };
    const activateThinking = () => { thinking = true; };

    try {
      const { openSSE } = await import("@/lib/api");
      await openSSE(url, (f) => {
        if (f.event === "messages" && f.data?.content) {
          const id = f.data.id || null;
          if (segId && id && id !== segId) { closeSegment(); activateThinking(); }
          if (id) segId = id;
          cur += f.data.content;
          render();
        }
        else if (f.event === "tool" && f.data?.name) {
          acted = true;
          closeSegment(); activateThinking(); render();
          setCurrentTool(f.data.name);
          setSteps((s) => [...s, { name: f.data.name, result: f.data.result, turn }]);
        }
        else if (f.event === "todos" && Array.isArray(f.data?.todos)) { acted = true; setTodos(f.data.todos); }
        else if (f.event === "interrupt") { interruptPrompt = describeInterrupt(f.data); }
        else if (f.event === "done") { mutated = (f.data?.mutated || []).length > 0; }
        else if (f.event === "error") { cur += `\n⚠ ${f.data?.message || "assistant error"}`; render(); }
      }, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    } catch (e: any) {
      cur += `\n⚠ ${e.message || e}`;
    } finally {
      const answer = cur.trim();
      const thought = thinking ? done.join("\n\n") : "";
      const secs = Math.max(1, Math.round((Date.now() - turnStart) / 1000));
      if (answer || thought || acted) {
        setMsgs((m) => [...m, {
          role: "assistant",
          content: answer || (interruptPrompt ? "(paused for your approval)" : "(done - see Steps for details)"),
          ...(thought ? { thinking: thought, thinkSecs: secs } : {}),
        }]);
      }
      setStreaming(""); setLiveThink(null); setCurrentTool(null); setBusy(false);
      setPendingApproval(interruptPrompt);
      if (mutated) onMutate?.();
    }
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || busy || !project) return;
    setMsgs((m) => [...m, { role: "user", content: q }]); setInput("");
    const { api } = await import("@/lib/api");
    await streamTurn({ message: q, thread_id: threadRef.current }, api.assistantStreamUrl(project.id));
  }

  async function decide(decision: "approve" | "reject") {
    if (!project || busy) return;
    setMsgs((m) => [...m, { role: "user", content: decision === "approve" ? "✓ Approved" : "✕ Rejected" }]);
    const { api } = await import("@/lib/api");
    await streamTurn({ thread_id: threadRef.current, decision }, api.assistantResumeUrl(project.id));
  }

  function resetChat() {
    threadRef.current = newThreadId();
    setMsgs([]); setSteps([]); setTodos([]); setPendingApproval(null); setStreaming(""); setCurrentTool(null); setStepsOpen(false);
  }

  const suggestions = ["How does my workflow work?", "Build a grounded support workflow", "What's in this project?"];

  if (!open) return null;
  return (
    <aside style={{ width: 380, maxWidth: "38vw", flex: "none", background: "var(--bg-1)", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="row spread" style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        <div style={{ minWidth: 0 }}>
          <div className="t-h2">Forge Assistant</div>
          <div className="fg-2 t-caption truncate">{project ? `Building in ${project.name}` : "Open a project to build"}</div>
        </div>
        <div className="row gap1">
          <button className="iconbtn" title="New conversation" onClick={resetChat} disabled={busy}><Icon name="refresh" size={15} /></button>
          <button className="iconbtn" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div ref={scrollRef} className="scroll-y col gap4" style={{ padding: 16, flex: 1, minHeight: 0 }}>
        {msgs.length === 0 && !streaming && (
          <div className="col gap2" style={{ color: "var(--fg-2)" }}>
            <div className="row gap2"><Tile icon="sparkles" color="var(--accent)" size={28} /><div style={{ fontSize: 13, lineHeight: "19px", color: "var(--fg-1)" }}>I can build tools, auth providers, Q&A, knowledge, and whole workflows - and explain how Forge works. What should we build?</div></div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className="col" style={{ gap: 4 }}>
            {m.thinking && (
              <div style={{ paddingLeft: 37 }}>
                <button onClick={() => setOpenThought(openThought === i ? null : i)}
                  style={{ border: "none", background: "none", padding: 0, cursor: "pointer", fontSize: 12, color: "var(--fg-2)", display: "inline-flex", alignItems: "center", gap: 4 }}>
                  Thought for {m.thinkSecs}s
                  <Icon name={openThought === i ? "chevdown" : "chevright"} size={11} />
                </button>
                {openThought === i && (
                  <div style={{ marginTop: 4, fontSize: 12, lineHeight: "18px", color: "var(--fg-2)", whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 260, overflowY: "auto" }} className="no-scrollbar">
                    {m.thinking}
                  </div>
                )}
              </div>
            )}
            <div className="row" style={{ gap: 9, alignItems: "flex-start", flexDirection: m.role === "user" ? "row-reverse" : "row" }}>
              {m.role === "assistant" ? <Tile icon="sparkles" color="var(--accent)" size={28} /> : <Avatar name="You" size={28} />}
              <div style={{ maxWidth: 280, padding: "9px 12px", borderRadius: 11, fontSize: 13, lineHeight: "19px", overflowWrap: "anywhere", whiteSpace: m.role === "user" ? "pre-wrap" : "normal", background: m.role === "user" ? "var(--accent)" : "var(--bg-3)", color: m.role === "user" ? "var(--fg-on-accent)" : "var(--fg-0)", borderTopRightRadius: m.role === "user" ? 3 : 11, borderTopLeftRadius: m.role === "assistant" ? 3 : 11 }}>{m.role === "assistant" ? <Markdown>{m.content}</Markdown> : m.content}</div>
            </div>
          </div>
        ))}
        {pendingApproval && !busy && (
          <div className="card col gap2" style={{ padding: 12, borderColor: "var(--warn)" }}>
            <div className="row gap2" style={{ alignItems: "center" }}><Icon name="bolt" size={14} style={{ color: "var(--warn)" }} /><span className="t-h3">Approval required</span></div>
            <div className="t-body-sm fg-1" style={{ overflowWrap: "anywhere" }}>{pendingApproval}</div>
            <div className="row gap2">
              <button className="btn btn-primary btn-sm" onClick={() => decide("approve")}>Approve</button>
              <button className="btn btn-secondary btn-sm" onClick={() => decide("reject")}>Reject</button>
            </div>
          </div>
        )}
        {busy && (
          (liveThink !== null || currentTool) ? (
            /* Working: dim reasoning streams up in a small auto-scrolling window AND the
               current step shows on a single line that updates in place as the run moves
               from one tool to the next. Collapses to "Thought for Xs" on finish. */
            <div style={{ paddingLeft: 37 }} className="col gap1">
              <div style={{ fontSize: 12, color: "var(--fg-2)", display: "inline-flex", alignItems: "center", gap: 5 }}>
                Thinking
                <span style={{ display: "inline-block", width: 5, height: 5, borderRadius: "50%", background: "var(--fg-2)", animation: "blink 1s steps(1) infinite" }} />
              </div>
              {liveThink && (
                <div ref={thinkRef} className="no-scrollbar" style={{
                  maxHeight: 72, overflowY: "auto", fontSize: 12, lineHeight: "18px",
                  color: "var(--fg-2)", opacity: 0.72, whiteSpace: "pre-wrap", overflowWrap: "anywhere",
                  WebkitMaskImage: "linear-gradient(to bottom, transparent 0, black 22px)",
                  maskImage: "linear-gradient(to bottom, transparent 0, black 22px)",
                }}>
                  {liveThink}
                </div>
              )}
              {currentTool && (
                <div className="row gap2" style={{ alignItems: "center", fontSize: 12, color: "var(--fg-1)" }}>
                  <Icon name="refresh" size={11} style={{ color: "var(--accent)", animation: "spin 1s linear infinite" }} />
                  <span>{prettyTool(currentTool)}…</span>
                </div>
              )}
            </div>
          ) : (
            <div className="row" style={{ gap: 9, alignItems: "flex-start" }}>
              <Tile icon="sparkles" color="var(--accent)" size={28} />
              <div style={{ maxWidth: 280, padding: "9px 12px", borderRadius: 11, fontSize: 13, lineHeight: "19px", overflowWrap: "anywhere", background: "var(--bg-3)", color: "var(--fg-0)", borderTopLeftRadius: 3 }}>
                {streaming ? <Markdown>{streaming}</Markdown> : "…"}
                <span style={{ display: "inline-block", width: 6, height: 13, background: "var(--accent)", marginLeft: 2, verticalAlign: "-2px", animation: "blink 1s steps(1) infinite" }} />
              </div>
            </div>
          )
        )}
        {msgs.length === 0 && (
          <div className="row gap2 wrap" style={{ marginTop: 4 }}>
            {suggestions.map((s) => (
              <button key={s} className="chip" style={{ cursor: project ? "pointer" : "not-allowed", opacity: project ? 1 : 0.5 }} onClick={() => send(s)} disabled={!project}>{s}</button>
            ))}
          </div>
        )}
      </div>
      {/* Sticky, togglable Steps drawer: plan + every tool call, out of the chat flow. */}
      {(steps.length > 0 || todos.length > 0) && (
        <div style={{ flex: "none", borderTop: "1px solid var(--line)", background: "var(--bg-1)" }}>
          <button className="row spread" onClick={() => setStepsOpen((o) => !o)}
            style={{ width: "100%", padding: "8px 14px", border: "none", background: "none", cursor: "pointer", alignItems: "center" }}>
            <span className="row gap2" style={{ alignItems: "center", fontSize: 12.5, fontWeight: 650, color: "var(--fg-1)" }}>
              <Icon name="list" size={13} />
              Steps ({steps.length}){todos.length > 0 ? ` · plan ${todos.filter((t) => t.status === "completed").length}/${todos.length}` : ""}
              {busy && currentTool && <span className="mono-sm" style={{ color: "var(--accent)", fontWeight: 450 }}> · {currentTool}…</span>}
            </span>
            <Icon name={stepsOpen ? "chevdown" : "chevup"} size={14} style={{ color: "var(--fg-2)" }} />
          </button>
          {stepsOpen && (
            <div className="scroll-y col gap1" style={{ maxHeight: 220, padding: "0 14px 10px" }}>
              {todos.length > 0 && (
                <div className="col gap1" style={{ paddingBottom: 6, borderBottom: "1px solid var(--line)", marginBottom: 4 }}>
                  <div className="t-micro">Plan</div>
                  {todos.map((t, i) => (
                    <div key={i} className="row gap2" style={{ alignItems: "center", fontSize: 12, color: t.status === "completed" ? "var(--fg-2)" : "var(--fg-0)" }}>
                      <Icon name={t.status === "completed" ? "check" : t.status === "in_progress" ? "refresh" : "minus"} size={11}
                        style={{ color: t.status === "completed" ? "var(--ok)" : t.status === "in_progress" ? "var(--accent)" : "var(--fg-2)" }} />
                      <span style={{ textDecoration: t.status === "completed" ? "line-through" : "none" }}>{t.content || JSON.stringify(t)}</span>
                    </div>
                  ))}
                </div>
              )}
              {steps.map((s, i) => (
                <div key={i} className="col" style={{ gap: 2 }}>
                  <button className="row gap2" onClick={() => setExpandedStep(expandedStep === i ? null : i)}
                    style={{ border: "none", background: "none", cursor: s.result ? "pointer" : "default", padding: "2px 0", alignItems: "center", textAlign: "left" }}>
                    <Icon name="check" size={11} style={{ color: "var(--ok)" }} />
                    <span className="mono-sm" style={{ color: "var(--fg-1)" }}>{s.name}</span>
                    {s.result && <Icon name={expandedStep === i ? "chevdown" : "chevright"} size={11} style={{ color: "var(--fg-2)" }} />}
                  </button>
                  {expandedStep === i && s.result && (
                    <pre className="mono-sm" style={{ margin: "0 0 4px 18px", padding: "8px 10px", borderRadius: 8, background: "var(--bg-3)", fontSize: 11, lineHeight: "16px", whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: 160, overflow: "auto" }}>{s.result}</pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div style={{ padding: 12, borderTop: "1px solid var(--line)", background: "var(--bg-1)", flex: "none" }}>
        <div className="row gap2" style={{ alignItems: "flex-end", background: "var(--bg-3)", border: "1px solid var(--line)", borderRadius: 10, padding: "6px 6px 6px 12px" }}>
          <textarea ref={taRef} value={input} onChange={(e) => setInput(e.target.value)} rows={1}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            placeholder={project ? "Ask or instruct…  (Shift+Enter for a new line)" : "Open a project first"} disabled={!project || busy}
            style={{ flex: 1, minWidth: 0, border: "none", background: "none", outline: "none", resize: "none", overflowY: "auto", maxHeight: 140, fontSize: 13, lineHeight: "19px", color: "var(--fg-0)", fontFamily: "var(--font-ui)" }} />
          <button className="btn btn-primary btn-sm" onClick={() => send(input)} disabled={!project || busy} style={{ flex: "none" }}><Icon name={busy ? "refresh" : "bolt"} size={14} style={busy ? { animation: "spin 1s linear infinite" } : {}} /></button>
        </div>
      </div>
      </div>
    </aside>
  );
}
