"use client";
/* Analytics: the project's observability dashboard. Time-series volume/cost/latency/token
   graphs, per-source & per-tool/model breakdowns, a latency distribution, the usage table,
   and quick links - all over a selectable date range. Charts are Recharts, themed from the
   app's CSS variables (resolved to concrete colors so gradients + dark mode work). */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { Icon } from "../icons";
import { Sparkline, StatusPill, EmptyState } from "../primitives";
import { api, Analytics, ProjectCounts, StatRollup, Workflow } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

/* ---------------- formatters ---------------- */
const fmtLatency = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)}s` : `${Math.round(ms)}ms`);
const fmtCompact = (n: number) => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return `${Math.round(n)}`;
};
const fmtInt = (n: number) => Math.round(n).toLocaleString();
// "2026-07-24" -> "Jul 24"
const fmtAxisDate = (iso: string) => {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
};

const SOURCE_LABEL: Record<string, string> = {
  playground: "Playground", api: "API", embed: "Embed", assistant: "Forge Assistant",
  channel_email: "Email", webhook: "Webhook", schedule: "Schedule", app_event: "App event", "-": "Other",
};
const srcLabel = (s: string) => SOURCE_LABEL[s] || s || "Other";

const RANGES = [
  { value: "7", label: "7d" }, { value: "14", label: "14d" },
  { value: "30", label: "30d" }, { value: "90", label: "90d" },
];

/* ---------------- theme-aware palette ----------------
   Recharts draws into SVG; gradient <stop> colors and dark-mode switches need concrete
   values, not `var(--x)` strings. Resolve the tokens from the document once, and again
   whenever the app flips data-theme on <html>. */
interface Palette {
  accent: string; signal: string; ok: string; warn: string; err: string; info: string;
  purple: string; teal: string; fg0: string; fg1: string; fg2: string; line: string; bg1: string; bg3: string;
}
function readPalette(): Palette {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string, fb: string) => (cs.getPropertyValue(n).trim() || fb);
  return {
    accent: v("--accent", "#4F46E5"), signal: v("--signal", "#4F46E5"),
    ok: v("--ok", "#16A34A"), warn: v("--warn", "#D97706"), err: v("--err", "#DC2626"),
    info: v("--info", "#2563EB"), purple: v("--io-json", "#7C3AED"), teal: v("--io-messages", "#0D9488"),
    fg0: v("--fg-0", "#111"), fg1: v("--fg-1", "#444"), fg2: v("--fg-2", "#888"),
    line: v("--line", "#E5E5E5"), bg1: v("--bg-1", "#fff"), bg3: v("--bg-3", "#f0f0f0"),
  };
}
function useThemeColors(): Palette {
  const [pal, setPal] = useState<Palette | null>(null);
  useEffect(() => {
    setPal(readPalette());
    const obs = new MutationObserver(() => setPal(readPalette()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => obs.disconnect();
  }, []);
  return pal || readPaletteFallback();
}
// SSR / first paint before the effect runs: sensible light defaults so charts don't flash.
function readPaletteFallback(): Palette {
  return {
    accent: "#4F46E5", signal: "#4F46E5", ok: "#16A34A", warn: "#D97706", err: "#DC2626",
    info: "#2563EB", purple: "#7C3AED", teal: "#0D9488", fg0: "#111", fg1: "#444", fg2: "#888",
    line: "#E5E5E5", bg1: "#fff", bg3: "#f0f0f0",
  };
}

/* ---------------- small building blocks ---------------- */
function DeltaBadge({ cur, prev, goodUp = true, fmt }: { cur: number; prev: number; goodUp?: boolean; fmt?: (n: number) => string }) {
  if (prev === 0 && cur === 0) return <span className="t-caption fg-2">no change</span>;
  if (prev === 0) return <span className="pill pill-muted" style={{ height: 16 }}>new</span>;
  const pct = ((cur - prev) / Math.abs(prev)) * 100;
  const up = pct > 0;
  const flat = Math.abs(pct) < 0.05;
  const good = flat ? null : up === goodUp;
  const color = flat ? "var(--fg-2)" : good ? "var(--ok)" : "var(--err)";
  return (
    <span className="row gap1" style={{ color, fontSize: 11.5, fontWeight: 600, alignItems: "center" }} title={fmt ? `${fmt(prev)} → ${fmt(cur)}` : undefined}>
      {!flat && <Icon name={up ? "chevup" : "chevdown"} size={12} />}
      {flat ? "±0%" : `${up ? "+" : ""}${pct.toFixed(pct >= 100 || pct <= -100 ? 0 : 1)}%`}
    </span>
  );
}

function KpiTile({ label, value, sub, spark, sparkColor, delta }: {
  label: string; value: string; sub?: string; spark?: number[]; sparkColor?: string; delta?: React.ReactNode;
}) {
  return (
    <div className="card" style={{ padding: 16 }}>
      <div className="row spread" style={{ marginBottom: 8, alignItems: "flex-start" }}>
        <div className="t-micro">{label}</div>
        {delta}
      </div>
      <div className="row spread" style={{ alignItems: "flex-end" }}>
        <div>
          <div className="t-display" style={{ fontSize: 24, lineHeight: 1.1 }}>{value}</div>
          {sub && <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{sub}</div>}
        </div>
        {spark && spark.some((n) => n > 0) && <Sparkline data={spark} w={78} h={30} color={sparkColor || "var(--accent)"} />}
      </div>
    </div>
  );
}

function ChartCard({ title, sub, right, children, height = 232 }: {
  title: string; sub?: string; right?: React.ReactNode; children: React.ReactNode; height?: number;
}) {
  return (
    <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column" }}>
      <div className="row spread" style={{ marginBottom: 10 }}>
        <div>
          <div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650 }}>{title}</div>
          {sub && <div className="fg-2 t-caption" style={{ marginTop: 1 }}>{sub}</div>}
        </div>
        {right}
      </div>
      <div style={{ width: "100%", height }}>{children}</div>
    </div>
  );
}

// One shared tooltip for every chart: dark card, the point label, then each series.
function ChartTooltip({ active, payload, label, pal, labelFmt, valueFmt }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: pal.bg1, border: `1px solid ${pal.line}`, borderRadius: 8, padding: "8px 10px", boxShadow: "var(--sh-2)", fontSize: 12 }}>
      {label != null && <div style={{ color: pal.fg2, marginBottom: 4, fontWeight: 600 }}>{labelFmt ? labelFmt(label) : label}</div>}
      {payload.map((p: any, i: number) => (
        <div key={i} className="row gap2" style={{ alignItems: "center", justifyContent: "space-between", gap: 14 }}>
          <span className="row gap1" style={{ alignItems: "center", color: pal.fg1 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: p.color || p.fill || p.stroke, display: "inline-block" }} />
            {p.name}
          </span>
          <b style={{ color: pal.fg0, fontFamily: "var(--font-mono)" }}>{valueFmt ? valueFmt(p.value, p.dataKey) : p.value}</b>
        </div>
      ))}
    </div>
  );
}

const axisProps = (pal: Palette) => ({ stroke: pal.line, tick: { fill: pal.fg2, fontSize: 11 }, tickLine: false, axisLine: { stroke: pal.line } });

/* ================= ANALYTICS SCREEN ================= */
export function AnalyticsScreen({ project, onNav }: { project: any; onNav: (s: string) => void }) {
  const pal = useThemeColors();
  const [days, setDays] = useState("30");
  const [data, setData] = useState<Analytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [counts, setCounts] = useState<ProjectCounts | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const reqId = useRef(0);

  useEffect(() => {
    if (!project?.id) return;
    const id = ++reqId.current;
    setLoading(true);
    api.projectAnalytics(project.id, Number(days))
      .then((d) => { if (id === reqId.current) { setData(d); setLoading(false); } })
      .catch(() => { if (id === reqId.current) { setData(null); setLoading(false); } });
  }, [project?.id, days]);

  useEffect(() => {
    if (!project?.id) return;
    api.projectCounts(project.id).then(setCounts).catch(() => setCounts(null));
    api.listWorkflows(project.id).then(setWorkflows).catch(() => setWorkflows([]));
  }, [project?.id]);

  const ts = data?.timeseries || [];
  const spark = (key: keyof Analytics["timeseries"][number]) => ts.map((p) => Number(p[key]) || 0);
  const t = data?.totals || ({} as StatRollup);
  const pv = data?.prev_totals || ({} as StatRollup);
  const successRate = (r: StatRollup) => (r.runs ? Math.round((100 - (r.error_rate || 0)) * 10) / 10 : 0);

  // Merge success+error per day for the stacked volume chart; label sources for the pie.
  const volume = useMemo(() => ts.map((p) => ({ date: p.date, Success: p.success, Errors: p.errors })), [ts]);
  const sourcePie = useMemo(
    () => (data?.by_source || []).filter((s) => s.cost_usd > 0 || s.runs > 0).map((s) => ({ name: srcLabel(s.source), value: Math.round(s.cost_usd * 1e6) / 1e6, runs: s.runs })),
    [data],
  );
  const pieColors = [pal.accent, pal.teal, pal.warn, pal.purple, pal.info, pal.ok, pal.err];
  const hasRuns = (data?.totals?.runs || 0) > 0;

  const health = [
    { label: "Workflows", value: counts?.workflows ?? workflows.length, icon: "workflows", screen: "workflows" },
    { label: "Agents", value: counts?.agents ?? "-", icon: "agents", screen: "agents" },
    { label: "Tools", value: counts?.tools ?? "-", icon: "tools", screen: "tools" },
    { label: "Knowledge", value: counts?.knowledge ?? "-", icon: "knowledge", screen: "knowledge" },
  ];

  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div className="fade-up" style={{ maxWidth: 1600, margin: "0 auto" }}>
        {/* header + range picker */}
        <div className="row spread" style={{ marginBottom: 18, alignItems: "flex-end" }}>
          <div>
            <div className="t-display">{project?.name}</div>
            <div className="fg-1" style={{ marginTop: 3 }}>Analytics · {project?.slug}</div>
          </div>
          <div className="row gap2" style={{ alignItems: "center" }}>
            <Icon name="clock" size={15} style={{ color: "var(--fg-2)" }} />
            <div className="segmented">
              {RANGES.map((r) => (
                <button key={r.value} className={days === r.value ? "active" : ""} onClick={() => setDays(r.value)}>{r.label}</button>
              ))}
            </div>
          </div>
        </div>

        {loading && !data ? (
          <div className="fg-2" style={{ padding: 60, textAlign: "center" }}>Loading analytics…</div>
        ) : !hasRuns ? (
          <>
            <div className="card" style={{ padding: 8, marginBottom: 22 }}>
              <EmptyState icon="activity" title="No activity in this window"
                sub="Run a workflow in the Playground, from the API, or chat with the Forge Assistant - metrics will appear here."
                action={<button className="btn btn-primary" style={{ marginTop: 6 }} onClick={() => onNav("playground")}><Icon name="playground" size={15} />Open Playground</button>} />
            </div>
            <QuickLinks health={health} workflows={workflows} onNav={onNav} />
          </>
        ) : (
          <>
            {/* KPI row */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(6,1fr)", gap: 14, marginBottom: 20 }}>
              <KpiTile label="Runs" value={fmtInt(t.runs)} sub={`over ${days}d`} spark={spark("runs")} sparkColor="var(--accent)" delta={<DeltaBadge cur={t.runs} prev={pv.runs} fmt={fmtInt} />} />
              <KpiTile label="Success rate" value={`${successRate(t)}%`} sub={`${t.errors || 0} errors`} spark={spark("success")} sparkColor="var(--ok)" delta={<DeltaBadge cur={successRate(t)} prev={successRate(pv)} fmt={(n) => `${n}%`} />} />
              <KpiTile label="Avg latency" value={fmtLatency(t.avg_latency_ms)} sub="per run" spark={spark("avg_latency_ms")} sparkColor="var(--info)" delta={<DeltaBadge cur={t.avg_latency_ms} prev={pv.avg_latency_ms} goodUp={false} fmt={fmtLatency} />} />
              <KpiTile label="Spend" value={fmtUSD(t.cost_usd)} sub="tracked cost" spark={spark("cost_usd")} sparkColor="var(--warn)" delta={<DeltaBadge cur={t.cost_usd} prev={pv.cost_usd} goodUp={false} fmt={fmtUSD} />} />
              <KpiTile label="Tokens" value={fmtCompact(t.tokens)} sub="in + out" spark={spark("tokens")} sparkColor="var(--io-json)" delta={<DeltaBadge cur={t.tokens} prev={pv.tokens} fmt={fmtCompact} />} />
              <KpiTile label="Error rate" value={`${t.error_rate || 0}%`} sub={`${t.errors || 0} of ${fmtInt(t.runs)}`} spark={spark("errors")} sparkColor="var(--err)" delta={<DeltaBadge cur={t.error_rate || 0} prev={pv.error_rate || 0} goodUp={false} fmt={(n) => `${n}%`} />} />
            </div>

            {/* time-series: volume + cost */}
            <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr", gap: 16, marginBottom: 16 }}>
              <ChartCard title="Run volume" sub="Successful vs errored runs per day">
                <ResponsiveContainer>
                  <AreaChart data={volume} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                    <defs>
                      <linearGradient id="gSuccess" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={pal.accent} stopOpacity={0.35} /><stop offset="100%" stopColor={pal.accent} stopOpacity={0.02} /></linearGradient>
                      <linearGradient id="gErr" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={pal.err} stopOpacity={0.35} /><stop offset="100%" stopColor={pal.err} stopOpacity={0.02} /></linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={pal.line} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={fmtAxisDate} minTickGap={28} {...axisProps(pal)} />
                    <YAxis allowDecimals={false} width={40} {...axisProps(pal)} />
                    <Tooltip content={<ChartTooltip pal={pal} labelFmt={fmtAxisDate} />} />
                    <Area type="monotone" dataKey="Success" stackId="1" stroke={pal.accent} strokeWidth={2} fill="url(#gSuccess)" />
                    <Area type="monotone" dataKey="Errors" stackId="1" stroke={pal.err} strokeWidth={2} fill="url(#gErr)" />
                  </AreaChart>
                </ResponsiveContainer>
              </ChartCard>
              <ChartCard title="Cost" sub="Tracked spend per day">
                <ResponsiveContainer>
                  <BarChart data={ts} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={pal.line} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={fmtAxisDate} minTickGap={28} {...axisProps(pal)} />
                    <YAxis width={48} tickFormatter={(v) => `$${v < 1 ? v.toFixed(2) : fmtCompact(v)}`} {...axisProps(pal)} />
                    <Tooltip cursor={{ fill: pal.bg3, opacity: 0.5 }} content={<ChartTooltip pal={pal} labelFmt={fmtAxisDate} valueFmt={(v: number) => fmtUSD(v)} />} />
                    <Bar dataKey="cost_usd" name="Cost" fill={pal.warn} radius={[3, 3, 0, 0]} maxBarSize={26} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* time-series: latency + tokens */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
              <ChartCard title="Latency" sub="Average run latency per day">
                <ResponsiveContainer>
                  <LineChart data={ts} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={pal.line} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={fmtAxisDate} minTickGap={28} {...axisProps(pal)} />
                    <YAxis width={44} tickFormatter={(v) => fmtLatency(v)} {...axisProps(pal)} />
                    <Tooltip content={<ChartTooltip pal={pal} labelFmt={fmtAxisDate} valueFmt={(v: number) => fmtLatency(v)} />} />
                    <Line type="monotone" dataKey="avg_latency_ms" name="Avg latency" stroke={pal.info} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>
              <ChartCard title="Token usage" sub="Total tokens per day">
                <ResponsiveContainer>
                  <AreaChart data={ts} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
                    <defs><linearGradient id="gTok" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={pal.purple} stopOpacity={0.35} /><stop offset="100%" stopColor={pal.purple} stopOpacity={0.02} /></linearGradient></defs>
                    <CartesianGrid strokeDasharray="3 3" stroke={pal.line} vertical={false} />
                    <XAxis dataKey="date" tickFormatter={fmtAxisDate} minTickGap={28} {...axisProps(pal)} />
                    <YAxis width={44} tickFormatter={(v) => fmtCompact(v)} {...axisProps(pal)} />
                    <Tooltip content={<ChartTooltip pal={pal} labelFmt={fmtAxisDate} valueFmt={(v: number) => fmtInt(v)} />} />
                    <Area type="monotone" dataKey="tokens" name="Tokens" stroke={pal.purple} strokeWidth={2} fill="url(#gTok)" />
                  </AreaChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* breakdowns: cost by source (pie) + tool calls (bar) + latency distribution (bar) */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr 1fr", gap: 16, marginBottom: 16 }}>
              <ChartCard title="Cost by source">
                {sourcePie.length === 0 ? <NoData /> : (
                  <ResponsiveContainer>
                    <PieChart>
                      <Pie data={sourcePie} dataKey="value" nameKey="name" innerRadius={52} outerRadius={82} paddingAngle={2} stroke={pal.bg1} strokeWidth={2}>
                        {sourcePie.map((_, i) => <Cell key={i} fill={pieColors[i % pieColors.length]} />)}
                      </Pie>
                      <Tooltip content={<ChartTooltip pal={pal} valueFmt={(v: number) => fmtUSD(v)} />} />
                    </PieChart>
                  </ResponsiveContainer>
                )}
                <PieLegend items={sourcePie} colors={pieColors} />
              </ChartCard>

              <ChartCard title="Top tool calls" sub="Calls in the selected window">
                {(data?.tools?.length || 0) === 0 ? <NoData label="No tool calls recorded" /> : (
                  <ResponsiveContainer>
                    <BarChart layout="vertical" data={data!.tools.slice(0, 6)} margin={{ top: 0, right: 12, left: 8, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke={pal.line} horizontal={false} />
                      <XAxis type="number" allowDecimals={false} {...axisProps(pal)} />
                      <YAxis type="category" dataKey="name" width={104} tick={{ fill: pal.fg1, fontSize: 11 }} tickLine={false} axisLine={{ stroke: pal.line }} />
                      <Tooltip cursor={{ fill: pal.bg3, opacity: 0.5 }} content={<ChartTooltip pal={pal} valueFmt={(v: number, k: string) => (k === "calls" ? fmtInt(v) : v)} />} />
                      <Bar dataKey="calls" name="Calls" fill={pal.teal} radius={[0, 3, 3, 0]} maxBarSize={22} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>

              <ChartCard title="Latency distribution" sub="Runs by response time">
                <ResponsiveContainer>
                  <BarChart data={data?.latency_histogram || []} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={pal.line} vertical={false} />
                    <XAxis dataKey="label" interval={0} angle={-30} textAnchor="end" height={48} tick={{ fill: pal.fg2, fontSize: 9.5 }} tickLine={false} axisLine={{ stroke: pal.line }} />
                    <YAxis allowDecimals={false} width={34} {...axisProps(pal)} />
                    <Tooltip cursor={{ fill: pal.bg3, opacity: 0.5 }} content={<ChartTooltip pal={pal} valueFmt={(v: number) => `${fmtInt(v)} runs`} />} />
                    <Bar dataKey="count" name="Runs" fill={pal.accent} radius={[3, 3, 0, 0]} maxBarSize={40} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>
            </div>

            {/* usage-by-source table + models + recent */}
            <div style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr", gap: 16, marginBottom: 16 }}>
              <div className="card" style={{ overflow: "hidden" }}>
                <div className="row spread" style={{ padding: "14px 16px 10px" }}><div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650 }}>Usage by source</div></div>
                <table className="tbl">
                  <thead><tr><th>Source</th><th>Runs</th><th>Tokens</th><th>Avg latency</th><th>Errors</th><th>Cost</th></tr></thead>
                  <tbody>
                    {(data?.by_workflow || []).map((r, i) => (
                      <tr key={i}>
                        <td>
                          <div className="row gap2">
                            <Icon name={r.kind === "assistant" ? "sparkles" : r.kind === "workflow" ? "workflows" : "activity"} size={16} style={{ color: "var(--accent)", flex: "none" }} />
                            <span style={{ fontWeight: 600, fontSize: 13 }}>{r.label}</span>
                          </div>
                        </td>
                        <td className="mono-sm">{fmtInt(r.runs)}</td>
                        <td className="mono-sm">{fmtInt(r.tokens)}</td>
                        <td className="mono-sm">{fmtLatency(r.avg_latency_ms)}</td>
                        <td className="mono-sm">{r.errors ? <span className="pill pill-err" style={{ height: 16 }}>{r.errors}</span> : <span className="fg-2">0</span>}</td>
                        <td className="mono-sm">{fmtUSD(r.cost_usd)}</td>
                      </tr>
                    ))}
                    {(data?.by_workflow?.length || 0) === 0 && <tr><td colSpan={6}><div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>No usage in this window.</div></td></tr>}
                  </tbody>
                </table>
              </div>

              <div className="card" style={{ overflow: "hidden" }}>
                <div className="row spread" style={{ padding: "14px 16px 10px" }}><div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650 }}>Model spend</div></div>
                {(data?.models?.length || 0) === 0 ? (
                  <div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>No model calls recorded.</div>
                ) : (
                  <table className="tbl">
                    <thead><tr><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost</th></tr></thead>
                    <tbody>
                      {data!.models.map((m, i) => (
                        <tr key={i}>
                          <td><span className="mono-sm" style={{ fontWeight: 600 }}>{m.model}</span></td>
                          <td className="mono-sm">{fmtInt(m.calls)}</td>
                          <td className="mono-sm">{fmtCompact(m.tokens)}</td>
                          <td className="mono-sm">{fmtUSD(m.cost_usd)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            {/* recent activity */}
            <div className="card" style={{ overflow: "hidden", marginBottom: 20 }}>
              <div className="row spread" style={{ padding: "14px 16px 10px" }}>
                <div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650 }}>Recent runs</div>
                <button className="btn btn-ghost btn-sm" onClick={() => onNav("traces")}>View traces<Icon name="chevright" size={14} /></button>
              </div>
              {(data?.recent || []).map((r, i, arr) => (
                <div key={r.id} className="row gap3" style={{ padding: "10px 16px", borderTop: "1px solid var(--line)" }}>
                  <StatusPill status={r.status} />
                  <div className="grow truncate" style={{ fontWeight: 600, fontSize: 13 }}>{r.workflow}</div>
                  <span className="mono-sm fg-2">{fmtInt(r.tokens)} tok</span>
                  <span className="mono-sm fg-2">{fmtLatency(r.latency_ms)}</span>
                  <span className="mono-sm" style={{ color: "var(--fg-1)" }}>{fmtUSD(r.cost_usd)}</span>
                  <span className="fg-2 t-caption" style={{ width: 44, textAlign: "right" }}>{r.started_at ? r.started_at.slice(11, 16) : ""}</span>
                </div>
              ))}
              {(data?.recent?.length || 0) === 0 && <div className="fg-2 t-caption" style={{ padding: 22, textAlign: "center" }}>No recent runs.</div>}
            </div>

            <QuickLinks health={health} workflows={workflows} onNav={onNav} />
          </>
        )}
      </div>
    </div>
  );
}

function NoData({ label = "No data" }: { label?: string }) {
  return <div className="col center" style={{ height: "100%", color: "var(--fg-2)", fontSize: 12.5 }}>{label}</div>;
}

function PieLegend({ items, colors }: { items: { name: string; value: number }[]; colors: string[] }) {
  if (!items.length) return null;
  return (
    <div className="col gap1" style={{ marginTop: 6 }}>
      {items.slice(0, 5).map((s, i) => (
        <div key={i} className="row spread" style={{ fontSize: 12 }}>
          <span className="row gap2" style={{ alignItems: "center", color: "var(--fg-1)" }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: colors[i % colors.length] }} />{s.name}
          </span>
          <b className="mono-sm">{fmtUSD(s.value)}</b>
        </div>
      ))}
    </div>
  );
}

/* Quick links kept from the old Overview so this stays the project landing screen:
   resource counts (deep-link into each builder) + the workflow list + deployment shortcuts. */
function QuickLinks({ health, workflows, onNav }: { health: { label: string; value: any; icon: string; screen: string }[]; workflows: Workflow[]; onNav: (s: string) => void }) {
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14, marginBottom: 16 }}>
        {health.map((h, i) => (
          <button key={i} className="card card-hover" style={{ padding: 16, textAlign: "left", background: "var(--bg-1)" }} onClick={() => onNav(h.screen)}>
            <div className="row spread"><Icon name={h.icon} size={20} style={{ color: "var(--fg-2)" }} /><Icon name="chevright" size={16} style={{ color: "var(--fg-2)" }} /></div>
            <div className="t-display" style={{ fontSize: 26, marginTop: 12 }}>{h.value}</div>
            <div className="fg-2 t-caption">{h.label}</div>
          </button>
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16 }}>
        <div className="card" style={{ padding: 18 }}>
          <div className="row spread" style={{ marginBottom: 14 }}>
            <div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650 }}>Workflows</div>
            <button className="btn btn-ghost btn-sm" onClick={() => onNav("workflow-canvas")}><Icon name="plus" size={14} />New</button>
          </div>
          {workflows.length === 0 ? (
            <div className="fg-2 t-caption" style={{ padding: "18px 0", textAlign: "center" }}>No workflows yet. Open the canvas to build one.</div>
          ) : (
            <div className="col gap2">
              {workflows.map((w) => (
                <button key={w.id} className="row gap3" onClick={() => onNav("workflows")} style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "var(--bg-1)", cursor: "pointer", textAlign: "left" }}>
                  <Icon name="workflows" size={18} style={{ color: "var(--accent)", flex: "none" }} />
                  <div className="grow"><div style={{ fontWeight: 600, fontSize: 13 }}>{w.name}</div><div className="fg-2 t-caption">v{w.active_version}</div></div>
                  <StatusPill status={w.status === "active" ? "active" : "draft"} />
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="card" style={{ padding: 18 }}>
          <div className="t-h3" style={{ fontSize: 13.5, fontWeight: 650, marginBottom: 14 }}>Deployment</div>
          <div className="col gap3">
            {[["msg", "Channels", "Email", "channels"], ["connect", "Connect", "Run API · MCP · widget", "connect"], ["playground", "Playground", "Test your workflow", "playground"]].map((d, i) => (
              <button key={i} className="row gap3" onClick={() => onNav(d[3])} style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid var(--line)", background: "var(--bg-1)", cursor: "pointer", textAlign: "left" }}>
                <Icon name={d[0]} size={18} style={{ color: "var(--fg-2)", flex: "none" }} />
                <div className="grow"><div style={{ fontWeight: 600, fontSize: 13 }}>{d[1]}</div><div className="fg-2 t-caption">{d[2]}</div></div>
                <Icon name="chevright" size={16} style={{ color: "var(--fg-2)" }} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
