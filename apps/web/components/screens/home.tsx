"use client";
/* Forge home screens: Dashboard, Project Overview, Onboarding wizard. */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { Sparkline, StatusPill, Tabs, Tile, Field, Toggle, EmptyState } from "../primitives";
import { api, Workflow, DashboardStats, ProjectCounts, ProjectStats, ReportRow } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

const fmtLatencyMs = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);

/** Usage breakdown table - one row per workflow + the Forge Assistant. */
function ReportTable({ rows, empty }: { rows: ReportRow[]; empty: string }) {
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <table className="tbl">
        <thead><tr><th>Source</th><th>Runs</th><th>Tokens</th><th>Avg latency</th><th>Cost</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>
                <div className="row gap2">
                  <Tile icon={r.kind === "assistant" ? "sparkles" : "workflows"} color="var(--accent)" size={24} />
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{r.label}</div>
                    <div className="fg-2 t-caption">{r.kind === "assistant" ? "Forge Assistant turns" : "workflow runs (playground & API)"}</div>
                  </div>
                </div>
              </td>
              <td className="mono-sm">{r.runs.toLocaleString()}</td>
              <td className="mono-sm">{r.tokens.toLocaleString()}</td>
              <td className="mono-sm">{fmtLatencyMs(r.avg_latency_ms)}</td>
              <td className="mono-sm">{fmtUSD(r.cost_usd)}</td>
            </tr>
          ))}
          {rows.length === 0 && <tr><td colSpan={5}><div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>{empty}</div></td></tr>}
        </tbody>
      </table>
    </div>
  );
}

export interface ProjectCard {
  id: string; name: string; slug: string; status: string;
  workflows: number; tools: number; runs7d: number; spark: number[]; edited: string;
}

/* ============ DASHBOARD ============ */
export function DashboardScreen({
  projects = [],
  loaded = false,
  stats = null,
  onOpenProject,
  onNewProject,
  onDeleteProject,
}: {
  projects?: ProjectCard[];
  loaded?: boolean;
  // Fetched once by the parent (App) and shared - avoids a second /stats/dashboard call.
  stats?: DashboardStats | null;
  onOpenProject: (id: string) => void;
  onNewProject: () => void;
  onDeleteProject?: (project: { id: string; name: string }) => Promise<void> | void;
}) {
  const empty = loaded && projects.length === 0;
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fmtLatency = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`);
  const kpis = [
    { label: "Runs · 7 days", value: stats ? stats.runs_7d.toLocaleString() : "-", sub: stats ? `${stats.total_runs.toLocaleString()} all-time` : "" },
    { label: "Success rate", value: stats && stats.runs_7d ? `${stats.success_rate}%` : "-", sub: "completed runs" },
    { label: "Avg latency", value: stats && stats.runs_7d ? fmtLatency(stats.avg_latency_ms) : "-", sub: "per run" },
    { label: "Spend · 7 days", value: stats ? fmtUSD(stats.spend_7d) : "-", sub: "tracked cost" },
  ];
  return (
    <div className="scroll-y" style={{ flex: 1, padding: "28px 32px" }}>
      <div className="fade-up" style={{ maxWidth: 1180, margin: "0 auto" }}>
        <div className="row spread" style={{ marginBottom: 22, alignItems: "flex-end" }}>
          <div>
            <div className="t-display-lg">Welcome to Forge</div>
            <div className="fg-1" style={{ marginTop: 4 }}>Self-hosted agent platform · {projects.length} project{projects.length === 1 ? "" : "s"}</div>
          </div>
          <div className="row gap2">
            <button className="btn btn-primary" onClick={onNewProject}><Icon name="plus" size={15} />New project</button>
          </div>
        </div>

        {empty ? (
          <div className="card" style={{ padding: 8 }}>
            <EmptyState
              icon="layers"
              title="Forge your first project"
              sub="A project is a workspace for agents, tools, knowledge, and workflows. Create one to begin building."
              action={<button className="btn btn-primary btn-lg" onClick={onNewProject} style={{ marginTop: 6 }}><Icon name="plus" size={16} />New project</button>}
            />
          </div>
        ) : !loaded ? (
          <div className="fg-2" style={{ padding: 40, textAlign: "center" }}>Loading…</div>
        ) : (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 16, marginBottom: 24 }}>
              {kpis.map((k, i) => (
                <div key={i} className="card" style={{ padding: 16 }}>
                  <div className="t-micro" style={{ marginBottom: 8 }}>{k.label}</div>
                  <div className="t-display" style={{ fontSize: 26 }}>{k.value}</div>
                  <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{k.sub}</div>
                </div>
              ))}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 20 }}>
              <div>
                <div className="row spread" style={{ marginBottom: 12 }}>
                  <div className="t-h1">Projects</div>
                </div>
                <div className="col gap3">
                  {projects.map((p) => (
                    <div key={p.id} className="card card-hover" style={{ padding: 14 }} onClick={() => onOpenProject(p.id)}>
                      <div className="row gap3">
                        <Tile icon="layers" color={p.status === "draft" ? "var(--fg-2)" : "var(--accent)"} size={40} />
                        <div className="grow" style={{ minWidth: 0 }}>
                          <div className="row gap2"><span className="t-h2">{p.name}</span><StatusPill status={p.status} /></div>
                          <div className="fg-2 t-caption row gap3" style={{ marginTop: 3 }}>
                            <span>{p.workflows} workflows</span><span>{p.tools} tools</span><span>edited {p.edited}</span>
                          </div>
                        </div>
                        <div className="col" style={{ alignItems: "flex-end", gap: 4 }}>
                          <Sparkline data={p.spark} w={92} h={26} color="var(--accent)" />
                          <div className="fg-2 t-caption">{p.runs7d.toLocaleString()} runs / 7d</div>
                        </div>
                        {onDeleteProject && (
                          <button
                            className="iconbtn"
                            title="Delete project"
                            disabled={deletingId === p.id}
                            onClick={async (e) => {
                              e.stopPropagation();
                              setDeletingId(p.id);
                              try { await onDeleteProject({ id: p.id, name: p.name }); }
                              finally { setDeletingId(null); }
                            }}
                          >
                            <Icon name="trash" size={15} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
              <div>
                <div className="row spread" style={{ marginBottom: 12 }}>
                  <div className="t-h1">Recent runs</div>
                  <span className="pill pill-info" style={{ height: 18 }}><span className="dot" />live</span>
                </div>
                <div className="card" style={{ overflow: "hidden" }}>
                  {(stats?.recent || []).map((r, i, arr) => (
                    <div key={r.id} className="row gap3" style={{ padding: "11px 14px", borderBottom: i < arr.length - 1 ? "1px solid var(--line)" : "none" }}>
                      <div style={{ width: 7, height: 7, borderRadius: "50%", flex: "none", background: r.status === "done" ? "var(--ok)" : r.status === "error" ? "var(--err)" : "var(--warn)" }} />
                      <div className="grow" style={{ minWidth: 0 }}>
                        <div className="truncate" style={{ fontSize: 13, fontWeight: 600 }}>{r.workflow}</div>
                        <div className="fg-2 t-caption truncate">{r.project} · {r.status}</div>
                      </div>
                      <div className="col" style={{ alignItems: "flex-end" }}>
                        <div className="mono-sm" style={{ color: "var(--fg-1)" }}>{r.tokens.toLocaleString()} tok</div>
                        <div className="fg-2 t-caption">{r.latency_ms}ms{r.started_at ? " · " + r.started_at.slice(11, 16) : ""}</div>
                      </div>
                    </div>
                  ))}
                  {(!stats || stats.recent.length === 0) && (
                    <div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>No runs yet. Run a workflow in the Playground.</div>
                  )}
                </div>
              </div>
            </div>

            {/* All-time usage by project (incl. Forge Assistant share) */}
            <div className="row spread" style={{ margin: "26px 0 12px" }}>
              <div className="t-h1">Reports</div>
              {stats?.totals && (
                <span className="fg-2 t-caption mono">
                  all-time: {stats.totals.runs.toLocaleString()} runs · {stats.totals.tokens.toLocaleString()} tok · {fmtUSD(stats.totals.cost_usd)}
                </span>
              )}
            </div>
            <div className="card" style={{ overflow: "hidden" }}>
              <table className="tbl">
                <thead><tr><th>Project</th><th>Runs</th><th>Tokens</th><th>Avg latency</th><th>Assistant</th><th>Total cost</th></tr></thead>
                <tbody>
                  {(stats?.reports || []).map((r, i) => (
                    <tr key={i}>
                      <td>
                        <div className="row gap2">
                          <Tile icon="layers" color="var(--accent)" size={24} />
                          <span style={{ fontWeight: 600, fontSize: 13 }}>{r.project}</span>
                        </div>
                      </td>
                      <td className="mono-sm">{r.runs.toLocaleString()}</td>
                      <td className="mono-sm">{r.tokens.toLocaleString()}</td>
                      <td className="mono-sm">{fmtLatencyMs(r.avg_latency_ms)}</td>
                      <td className="mono-sm">{r.assistant_turns ? `${fmtUSD(r.assistant_cost_usd)} · ${r.assistant_turns} turns` : "-"}</td>
                      <td className="mono-sm">{fmtUSD(r.cost_usd)}</td>
                    </tr>
                  ))}
                  {(!stats || !stats.reports?.length) && <tr><td colSpan={6}><div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>No usage yet.</div></td></tr>}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

/* ============ PROJECT OVERVIEW ============ */
export function OverviewScreen({ project, onNav, onDeleteProject }: { project: any; onNav: (s: string) => void; onDeleteProject?: (project: { id: string; name: string }) => Promise<void> | void }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [counts, setCounts] = useState<ProjectCounts | null>(null);
  const [stats, setStats] = useState<ProjectStats | null>(null);
  const [tab, setTab] = useState("overview");
  const [deleting, setDeleting] = useState(false);
  useEffect(() => {
    if (!project?.id) return;
    // The workflow list is rendered below (needs the rows); the other tiles only need
    // counts, so they come from the single shared counts call (deduped with the sidebar's).
    api.listWorkflows(project.id).then(setWorkflows).catch(() => setWorkflows([]));
    api.projectCounts(project.id).then(setCounts).catch(() => setCounts(null));
    api.projectStats(project.id).then(setStats).catch(() => setStats(null));
  }, [project?.id]);

  const health = [
    { label: "Workflows", value: counts?.workflows ?? workflows.length, icon: "workflows", screen: "workflows" },
    { label: "Agents", value: counts?.agents ?? "-", icon: "agents", screen: "agents" },
    { label: "Tools", value: counts?.tools ?? "-", icon: "tools", screen: "tools" },
    { label: "Knowledge", value: counts?.knowledge ?? "-", icon: "knowledge", screen: "knowledge" },
  ];
  const usage = [
    { label: "API calls (all-time)", value: stats ? stats.totals.runs.toLocaleString() : "-", sub: stats ? `${stats.last_7d.runs.toLocaleString()} in last 7 days` : "" },
    { label: "Tokens", value: stats ? stats.totals.tokens.toLocaleString() : "-", sub: "across all runs" },
    { label: "Avg latency", value: stats && stats.totals.runs ? fmtLatencyMs(stats.totals.avg_latency_ms) : "-", sub: "per run" },
    { label: "Total cost", value: stats ? fmtUSD(stats.totals.cost_usd) : "-", sub: stats ? `incl. ${fmtUSD(stats.assistant.cost_usd)} Forge Assistant` : "" },
  ];
  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div className="fade-up" style={{ maxWidth: 1080, margin: "0 auto" }}>
        <div className="row spread" style={{ marginBottom: 14 }}>
          <div>
            <div className="t-display">{project?.name}</div>
            <div className="fg-1" style={{ marginTop: 3 }}>Visual agent workspace · {project?.slug}</div>
          </div>
          <div className="row gap2">
            <button className="btn btn-secondary" onClick={() => onNav("tools")}><Icon name="tools" size={15} />Tools</button>
            {onDeleteProject && project?.id && (
              <button
                className="btn btn-danger"
                disabled={deleting}
                onClick={async () => {
                  setDeleting(true);
                  try { await onDeleteProject({ id: project.id, name: project.name }); }
                  finally { setDeleting(false); }
                }}
              >
                <Icon name="trash" size={15} />{deleting ? "Deleting..." : "Delete"}
              </button>
            )}
            <button className="btn btn-primary" onClick={() => onNav("workflow-canvas")}><Icon name="workflows" size={15} />Open canvas</button>
          </div>
        </div>
        <div style={{ marginBottom: 18 }}>
          <Tabs tabs={[{ value: "overview", label: "Overview" }, { value: "reports", label: "Reports" }]} value={tab} onChange={setTab} />
        </div>

        {tab === "reports" ? (
          <>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 22 }}>
              {usage.map((k, i) => (
                <div key={i} className="card" style={{ padding: 16 }}>
                  <div className="t-micro" style={{ marginBottom: 8 }}>{k.label}</div>
                  <div className="t-display" style={{ fontSize: 24 }}>{k.value}</div>
                  <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{k.sub}</div>
                </div>
              ))}
            </div>
            <div className="t-h1" style={{ marginBottom: 12 }}>Usage by source</div>
            <ReportTable rows={stats?.reports || []} empty="No usage yet. Run a workflow in the Playground or chat with the Forge Assistant." />
          </>
        ) : (
        <>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 18 }}>
          {health.map((h, i) => (
            <button key={i} className="card card-hover" style={{ padding: 16, textAlign: "left", background: "var(--bg-1)" }} onClick={() => onNav(h.screen)}>
              <div className="row spread"><Tile icon={h.icon} color="var(--fg-2)" size={34} /><Icon name="chevright" size={16} style={{ color: "var(--fg-2)" }} /></div>
              <div className="t-display" style={{ fontSize: 28, marginTop: 12 }}>{h.value}</div>
              <div className="fg-2 t-caption">{h.label}</div>
            </button>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 22 }}>
          {usage.map((k, i) => (
            <div key={i} className="card" style={{ padding: 14 }}>
              <div className="t-micro" style={{ marginBottom: 6 }}>{k.label}</div>
              <div className="t-display" style={{ fontSize: 20 }}>{k.value}</div>
              <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{k.sub}</div>
            </div>
          ))}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
          <div className="card" style={{ padding: 18 }}>
            <div className="row spread" style={{ marginBottom: 14 }}>
              <div className="t-h1">Workflows</div>
              <button className="btn btn-ghost btn-sm" onClick={() => onNav("workflow-canvas")}><Icon name="plus" size={14} />New</button>
            </div>
            {workflows.length === 0 ? (
              <div className="fg-2 t-caption" style={{ padding: "18px 0", textAlign: "center" }}>No workflows yet. Open the canvas to build one.</div>
            ) : (
              <div className="col gap2">
                {workflows.map((w) => (
                  <button key={w.id} className="row gap3" onClick={() => onNav("workflow-canvas")} style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "var(--bg-1)", cursor: "pointer", textAlign: "left" }}>
                    <Tile icon="workflows" color="var(--accent)" size={30} />
                    <div className="grow"><div style={{ fontWeight: 600, fontSize: 13 }}>{w.name}</div><div className="fg-2 t-caption">v{w.active_version}</div></div>
                    <StatusPill status={w.status === "active" ? "active" : "draft"} />
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="card" style={{ padding: 18 }}>
            <div className="t-h1" style={{ marginBottom: 14 }}>Deployment</div>
            <div className="col gap3">
              {[["msg", "Channels", "Email / Microsoft Teams", "var(--fg-2)", "channels"], ["connect", "MCP Server", "Not exposed", "var(--fg-2)", "connect"], ["playground", "Playground", "Test your workflow", "var(--fg-2)", "playground"]].map((d, i) => (
                <button key={i} className="row gap3" onClick={() => onNav(d[4])} style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "var(--bg-1)", cursor: "pointer", textAlign: "left" }}>
                  <Tile icon={d[0]} color={d[3]} size={30} />
                  <div className="grow"><div style={{ fontWeight: 600, fontSize: 13 }}>{d[1]}</div><div className="fg-2 t-caption">{d[2]}</div></div>
                  <Icon name="chevright" size={16} style={{ color: "var(--fg-2)" }} />
                </button>
              ))}
            </div>
          </div>
        </div>
        </>
        )}
      </div>
    </div>
  );
}

/* ============ ONBOARDING WIZARD ============ */
export function OnboardingScreen({ onCreate, onCancel }: { onCreate: (p: { name: string; template: string; keys: Record<string, string> }) => void; onCancel: () => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [tmpl, setTmpl] = useState("blank");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<Record<string, boolean>>({ anthropic: true, openai: false, google: false });
  const [keys, setKeys] = useState<Record<string, string>>({});
  const provId: Record<string, string> = { anthropic: "anthropic", openai: "openai", google: "google_genai" };
  const steps = ["Project", "Models", "Create"];
  const templates = [
    { id: "blank", name: "Blank canvas", desc: "Start from an empty graph", icon: "workflows" },
    { id: "support", name: "Support agent", desc: "Router → agent → tools → HITL", icon: "agents" },
    { id: "rag", name: "RAG Q&A", desc: "Retrieval + grounded answers", icon: "knowledge" },
    { id: "mcp", name: "MCP toolbox", desc: "Expose tools over MCP", icon: "connect" },
  ];
  return (
    <div className="col center" style={{ flex: 1, padding: 24, background: "var(--bg-0)" }}>
      <div className="card fade-up" style={{ width: 640, maxWidth: "94vw", overflow: "hidden" }}>
        <div style={{ padding: "18px 22px", borderBottom: "1px solid var(--line)" }}>
          <div className="row spread" style={{ marginBottom: 14 }}>
            <div className="t-h1">New project</div>
            <button className="iconbtn" onClick={onCancel}><Icon name="x" size={17} /></button>
          </div>
          <div className="row gap2">
            {steps.map((s, i) => (
              <div key={i} className="row gap2 grow">
                <div style={{ width: 22, height: 22, borderRadius: "50%", flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, fontWeight: 700, fontFamily: "var(--font-mono)", background: i < step ? "var(--accent)" : i === step ? "var(--accent-glow)" : "var(--bg-3)", color: i < step ? "var(--fg-on-accent)" : i === step ? "var(--accent)" : "var(--fg-2)", border: i === step ? "1px solid var(--accent)" : "none" }}>
                  {i < step ? <Icon name="check" size={13} /> : i + 1}
                </div>
                <span style={{ fontSize: 12.5, fontWeight: 600, color: i <= step ? "var(--fg-0)" : "var(--fg-2)" }}>{s}</span>
                {i < steps.length - 1 && <div className="grow" style={{ height: 1, background: i < step ? "var(--accent)" : "var(--line)" }} />}
              </div>
            ))}
          </div>
        </div>
        <div style={{ padding: 22, minHeight: 280 }}>
          {step === 0 && (
            <div className="fade-in">
              <Field label="Project name" help="A workspace for related workflows, tools, and knowledge." required>
                <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Customer Support" />
              </Field>
              <div className="field-label">Start from</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 6 }}>
                {templates.map((t) => (
                  <button key={t.id} onClick={() => setTmpl(t.id)} style={{ textAlign: "left", padding: 12, borderRadius: 10, cursor: "pointer", background: "var(--bg-1)", border: "1px solid " + (tmpl === t.id ? "var(--accent)" : "var(--line)"), boxShadow: tmpl === t.id ? "0 0 0 3px var(--accent-glow)" : "none" }}>
                    <div className="row gap2" style={{ marginBottom: 6 }}><Tile icon={t.icon} color="var(--accent)" size={28} />{tmpl === t.id && <Icon name="check" size={16} style={{ color: "var(--accent)", marginLeft: "auto" }} />}</div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{t.name}</div>
                    <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{t.desc}</div>
                  </button>
                ))}
              </div>
            </div>
          )}
          {step === 1 && (
            <div className="fade-in">
              <div className="fg-1" style={{ marginBottom: 14 }}>Connect at least one model provider. Keys are stored encrypted in your secret store - they never leave your instance.</div>
              {[["anthropic", "Anthropic", "claude-sonnet-4-6, haiku-4-2"], ["openai", "OpenAI", "gpt-5.4, gpt-5.4-mini"], ["google", "Google", "gemini-3.1-pro, 3.5-flash"]].map((p) => (
                <div key={p[0]} className="row gap3" style={{ padding: "12px 14px", borderRadius: 10, border: "1px solid var(--line)", marginBottom: 10 }}>
                  <Tile icon="n_llm" color="var(--fg-2)" size={32} />
                  <div className="grow"><div style={{ fontWeight: 600 }}>{p[1]}</div><div className="fg-2 t-caption">{p[2]}</div></div>
                  {models[p[0]] && <input className="input mono" style={{ width: 200 }} type="password" placeholder="sk-… (optional, encrypted)" value={keys[provId[p[0]]] || ""} onChange={(e) => setKeys((k) => ({ ...k, [provId[p[0]]]: e.target.value }))} />}
                  <Toggle on={models[p[0]]} onChange={(v) => setModels((m) => ({ ...m, [p[0]]: v }))} />
                </div>
              ))}
            </div>
          )}
          {step === 2 && (
            <div className="fade-in col center" style={{ textAlign: "center", gap: 10, paddingTop: 16 }}>
              <Tile icon="check" color="var(--ok)" size={52} glow />
              <div className="t-h1">Create “{name || "Untitled"}”</div>
              <div className="fg-1" style={{ maxWidth: 380 }}>We’ll create an empty project so you can register tools, add knowledge, and build your first workflow from scratch.</div>
            </div>
          )}
        </div>
        <div className="row spread" style={{ padding: "14px 22px", borderTop: "1px solid var(--line)" }}>
          <button className="btn btn-ghost" onClick={() => (step === 0 ? onCancel() : setStep(step - 1))}>{step === 0 ? "Cancel" : "Back"}</button>
          <button className="btn btn-primary" disabled={(step === 0 && !name) || busy} onClick={() => { if (step < 2) { setStep(step + 1); } else { setBusy(true); onCreate({ name, template: tmpl, keys }); } }}>
            {step < 2 ? "Continue" : busy ? "Creating…" : "Create project"}<Icon name="chevright" size={15} />
          </button>
        </div>
      </div>
    </div>
  );
}
