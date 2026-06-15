"use client";
/* Traces: runs list + span waterfall (kind-colored) + token/cost rollup. */
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { StatusPill, Tile } from "../primitives";
import { api, Span, Trace } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

const KIND_COLOR: Record<string, string> = {
  llm: "var(--accent)", tool: "var(--io-tool)", chain: "var(--io-json)", node: "var(--io-control)",
  agent: "var(--accent)", retriever: "var(--io-vector)", subagent: "var(--signal)",
};

export function TracesScreen({ project }: { project: any }) {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ trace: Trace; spans: Span[] } | null>(null);

  useEffect(() => { if (project?.id) api.listTraces(project.id).then((t) => { setTraces(t); if (t[0]) setSel(t[0].id); }).catch(() => {}); }, [project?.id]);
  useEffect(() => { if (project?.id && sel) api.getTrace(project.id, sel).then(setDetail).catch(() => setDetail(null)); }, [project?.id, sel]);

  return (
    // alignItems:stretch — .row centers children, which gives the list its content
    // height and silently kills its scroll.
    <div className="row" style={{ flex: 1, minHeight: 0, height: "100%", overflow: "hidden", alignItems: "stretch" }}>
      {/* runs list */}
      <div className="scroll-y" style={{ width: 340, flex: "none", borderRight: "1px solid var(--line)", minHeight: 0, height: "100%" }}>
        <div className="t-h2" style={{ padding: "16px 16px 8px" }}>Runs</div>
        {traces.length === 0 && <div className="fg-2 t-caption" style={{ padding: "8px 16px" }}>No runs yet. Run a workflow in the Playground.</div>}
        {traces.map((t) => (
          <button key={t.id} onClick={() => setSel(t.id)} className="row gap2" style={{ width: "100%", textAlign: "left", padding: "11px 16px", border: "none", borderBottom: "1px solid var(--line)", background: sel === t.id ? "var(--bg-3)" : "transparent", cursor: "pointer" }}>
            <div className="grow" style={{ minWidth: 0 }}>
              <div className="row gap2"><StatusPill status={t.status} /><span className="t-caption fg-2 mono">{(t.started_at || "").slice(11, 19)}</span></div>
              <div className="fg-2 t-caption mono" style={{ marginTop: 4 }}>{t.latency_ms}ms · {t.total_tokens} tok · {fmtUSD(t.total_cost_usd)}</div>
            </div>
            <Icon name="chevright" size={15} style={{ color: "var(--fg-2)" }} />
          </button>
        ))}
      </div>
      {/* detail */}
      <div className="scroll-y grow" style={{ minWidth: 0, minHeight: 0, height: "100%", padding: 24, overflowX: "hidden" }}>
        {detail ? <TraceDetail trace={detail.trace} spans={detail.spans} /> : <div className="fg-2" style={{ padding: 40, textAlign: "center" }}>Select a run to inspect its spans.</div>}
      </div>
    </div>
  );
}

function TraceDetail({ trace, spans }: { trace: Trace; spans: Span[] }) {
  const maxLatency = useMemo(() => Math.max(1, ...spans.map((s) => s.latency_ms)), [spans]);
  const depth = useMemo(() => {
    const byId = Object.fromEntries(spans.map((s) => [s.id, s]));
    const d: Record<string, number> = {};
    const compute = (s: Span): number => {
      if (s.id in d) return d[s.id];
      d[s.id] = s.parent_span_id && byId[s.parent_span_id] ? compute(byId[s.parent_span_id]) + 1 : 0;
      return d[s.id];
    };
    spans.forEach(compute);
    return d;
  }, [spans]);

  return (
    <div className="fade-up col" style={{ maxWidth: 1100, height: "100%", minHeight: 0 }}>
      <div className="row spread" style={{ marginBottom: 16, flex: "none" }}>
        <div className="row gap2"><Tile icon="traces" color="var(--accent)" size={32} /><div><div className="t-h1">{trace.name}</div><div className="fg-2 t-caption mono">{trace.id.slice(0, 8)}</div></div></div>
        <div className="row gap3">
          <Metric label="Latency" value={`${trace.latency_ms}ms`} />
          <Metric label="Tokens" value={String(trace.total_tokens)} />
          <Metric label="Cost" value={fmtUSD(trace.total_cost_usd)} />
        </div>
      </div>
      <div className="card scroll-y" style={{ overflowX: "hidden", flex: 1, minHeight: 0 }}>
        {spans.length === 0 && <div className="fg-2" style={{ padding: 22, textAlign: "center" }}>No spans recorded.</div>}
        {spans.map((s) => (
          <div key={s.id} className="row gap3" style={{ padding: "9px 14px", borderBottom: "1px solid var(--line)" }}>
            <div style={{ width: 200, flex: "none", paddingLeft: (depth[s.id] || 0) * 16 }}>
              <div className="row gap2">
                <span style={{ width: 8, height: 8, borderRadius: 2, background: KIND_COLOR[s.kind] || "var(--fg-2)", flex: "none" }} />
                <span className="t-body-sm truncate">{s.name}</span>
              </div>
              <div className="t-caption fg-2 mono" style={{ marginLeft: 16 }}>{s.kind}{s.model ? ` · ${s.model}` : ""}</div>
            </div>
            <div className="grow" style={{ minWidth: 0, display: "flex", alignItems: "center" }}>
              <div style={{ height: 8, borderRadius: 4, background: KIND_COLOR[s.kind] || "var(--fg-2)", width: `${Math.max(3, (s.latency_ms / maxLatency) * 100)}%`, opacity: 0.75 }} />
              <span className="mono-sm fg-2" style={{ marginLeft: 8 }}>{s.latency_ms}ms</span>
            </div>
            <div className="col" style={{ alignItems: "flex-end", width: 120, flex: "none" }}>
              {(s.input_tokens + s.output_tokens) > 0 && <span className="mono-sm">{s.input_tokens + s.output_tokens} tok</span>}
              {s.cost_usd > 0 && <span className="t-caption fg-2">{fmtUSD(s.cost_usd)}</span>}
              {s.error && <span className="pill pill-err" style={{ height: 16 }}>error</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="col" style={{ alignItems: "flex-end" }}><span className="t-display" style={{ fontSize: 18 }}>{value}</span><span className="t-micro">{label}</span></div>;
}
