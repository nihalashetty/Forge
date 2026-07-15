"use client";
/* Traces: conversations (chat sessions) grouped by end user, their user<->AI turns, and a
   drill-in to the per-turn span waterfall (tool + LLM request/response). */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { StatusPill } from "../primitives";
import { api, Conversation, ConversationDetail, Facets, openSSE, Span, Turn } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

// Conversations are paged in on scroll (newest-activity first) so the Traces view never
// pulls a project's entire history in one shot.
const PAGE = 20;

// Friendly labels for the raw run source.
const SOURCE_LABEL: Record<string, string> = {
  playground: "Playground", api: "API", embed: "Embed", assistant: "Forge Assistant",
  channel_email: "Email", webhook: "Webhook", schedule: "Schedule", app_event: "App event",
};
const srcLabel = (s: string) => SOURCE_LABEL[s] || s || "—";
const fmtWhen = (iso?: string | null) => (iso ? iso.slice(0, 16).replace("T", " ") : "");

// Trigger a client-side file download of `data` as pretty JSON (used by the Traces export).
function downloadJSON(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function TracesScreen({ project }: { project: any }) {
  const [convos, setConvos] = useState<Conversation[]>([]);
  const [facets, setFacets] = useState<Facets>({ actors: [], sources: [] });
  const [actor, setActor] = useState("");
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [nextOffset, setNextOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  // Distinct actors for the filter dropdown. A separate call because the paged list below
  // only holds one 20-row window - the full actor set can't be derived from it.
  useEffect(() => { if (project?.id) api.conversationFacets(project.id).then(setFacets).catch(() => {}); }, [project?.id]);

  // Debounce the search box so we don't fire a request per keystroke.
  useEffect(() => { const t = setTimeout(() => setSearch(searchInput.trim()), 350); return () => clearTimeout(t); }, [searchInput]);

  // First page - and a reload whenever the project or a filter changes.
  useEffect(() => {
    if (!project?.id) { setConvos([]); setHasMore(false); setNextOffset(0); return; }
    let live = true;
    api.listConversations(project.id, { actor: actor || undefined, source: source || undefined, status: status || undefined, search: search || undefined, limit: PAGE, offset: 0 })
      .then((c) => {
        if (!live) return;
        setConvos(c);
        setNextOffset(c.length);
        setHasMore(c.length === PAGE);
        setSel((cur) => (cur && c.some((x) => x.thread_id === cur) ? cur : c[0]?.thread_id ?? null));
        if (listRef.current) listRef.current.scrollTop = 0;
      })
      .catch(() => { if (live) { setConvos([]); setHasMore(false); setNextOffset(0); } });
    return () => { live = false; };
  }, [project?.id, actor, source, status, search]);

  const loadMore = useCallback(async () => {
    if (!project?.id || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const next = await api.listConversations(project.id, { actor: actor || undefined, source: source || undefined, status: status || undefined, search: search || undefined, limit: PAGE, offset: nextOffset });
      // De-dupe by thread_id in case the scan window shifted between pages.
      setConvos((prev) => {
        const seen = new Set(prev.map((x) => x.thread_id));
        return [...prev, ...next.filter((x) => !seen.has(x.thread_id))];
      });
      setNextOffset((o) => o + next.length);
      setHasMore(next.length === PAGE);
    } catch { /* keep what we have */ } finally { setLoadingMore(false); }
  }, [project?.id, actor, source, status, search, nextOffset, hasMore, loadingMore]);

  const onListScroll = () => {
    const el = listRef.current;
    if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) loadMore();
  };

  useEffect(() => { if (project?.id && sel) api.getConversation(project.id, sel).then(setDetail).catch(() => setDetail(null)); else setDetail(null); }, [project?.id, sel]);

  const purge = async () => {
    const days = window.prompt("Delete conversations older than how many days? (admin only)", "30");
    if (days == null) return;
    const n = parseInt(days, 10);
    if (!Number.isFinite(n) || n < 0) return;
    try {
      const { removed } = await api.purgeConversations(project.id, n);
      window.alert(`Removed ${removed} conversation turn(s) older than ${n} days.`);
      // Reset back to the first page after a purge.
      const first = await api.listConversations(project.id, { actor: actor || undefined, source: source || undefined, status: status || undefined, search: search || undefined, limit: PAGE, offset: 0 });
      setConvos(first);
      setNextOffset(first.length);
      setHasMore(first.length === PAGE);
    } catch { window.alert("Purge failed — this action requires an admin role."); }
  };

  // Export the loaded conversation summaries as JSON (respects the active filters/search).
  const exportConvos = () => {
    if (!convos.length) return;
    downloadJSON(`conversations-${project?.slug || project?.id || "export"}.json`, convos);
  };

  return (
    // alignItems:stretch - .row centers children, which gives the list its content height.
    <div className="row" style={{ flex: 1, minHeight: 0, height: "100%", overflow: "hidden", alignItems: "stretch" }}>
      {/* conversations list + filters */}
      <div className="col" style={{ width: 340, flex: "none", borderRight: "1px solid var(--line)", minHeight: 0, height: "100%" }}>
        <div className="row spread" style={{ padding: "16px 16px 8px", flex: "none" }}>
          <div className="t-h2">Conversations</div>
          <div className="row gap2">
            <button className="t-caption fg-2" onClick={exportConvos} disabled={convos.length === 0} title="Download the loaded conversations as JSON" style={{ background: "none", border: "none", cursor: convos.length ? "pointer" : "default", opacity: convos.length ? 1 : 0.5 }}>Export</button>
            <button className="t-caption fg-2" onClick={purge} title="Delete old conversations" style={{ background: "none", border: "none", cursor: "pointer" }}>Clean up…</button>
          </div>
        </div>
        <div className="col gap2" style={{ padding: "0 16px 10px", flex: "none" }}>
          <input value={searchInput} onChange={(e) => setSearchInput(e.target.value)} placeholder="Search messages…" className="input" style={{ width: "100%", fontSize: 13 }} />
          <div className="row gap1">
            <select value={actor} onChange={(e) => setActor(e.target.value)} className="input" style={{ flex: 1, minWidth: 0, fontSize: 13 }}>
              <option value="">All users</option>
              {facets.actors.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
            <select value={source} onChange={(e) => setSource(e.target.value)} className="input" style={{ flex: 1, minWidth: 0, fontSize: 13 }}>
              <option value="">All sources</option>
              {facets.sources.map((s) => <option key={s} value={s}>{srcLabel(s)}</option>)}
            </select>
          </div>
          <div className="row gap1">
            {[["", "All"], ["success", "Success"], ["error", "Error"]].map(([v, label]) => (
              <button key={v} onClick={() => setStatus(v)} className="t-caption"
                style={{ flex: 1, padding: "5px 0", borderRadius: 6, cursor: "pointer", border: "1px solid var(--line)", background: status === v ? "var(--bg-3)" : "transparent", color: status === v ? "var(--fg-0)" : "var(--fg-2)" }}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <div ref={listRef} onScroll={onListScroll} className="scroll-y" style={{ minHeight: 0, flex: 1 }}>
          {convos.length === 0 && <div className="fg-2 t-caption" style={{ padding: "8px 16px" }}>No conversations yet. Run a workflow in the Playground or from your app.</div>}
          {convos.map((c) => (
            <button key={c.thread_id} onClick={() => setSel(c.thread_id)} className="row gap2" style={{ width: "100%", textAlign: "left", padding: "11px 16px", border: "none", borderBottom: "1px solid var(--line)", background: sel === c.thread_id ? "var(--bg-3)" : "transparent", cursor: "pointer" }}>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="row gap2" style={{ minWidth: 0 }}>
                  <StatusPill status={c.status} />
                  <span className="t-body-sm truncate" style={{ fontWeight: 600 }}>{c.actor}</span>
                </div>
                <div className="truncate fg-2 t-caption" style={{ marginTop: 3 }}>{c.preview || "(no message)"}</div>
                <div className="fg-2 t-caption mono" style={{ marginTop: 3 }}>{srcLabel(c.source)} · {c.turns} turn{c.turns === 1 ? "" : "s"} · {fmtWhen(c.last_activity)}</div>
              </div>
              <Icon name="chevright" size={15} style={{ color: "var(--fg-2)" }} />
            </button>
          ))}
          {loadingMore && <div className="fg-2 t-caption" style={{ padding: "10px 16px", textAlign: "center" }}>Loading more…</div>}
          {hasMore && !loadingMore && (
            <button onClick={loadMore} className="t-caption fg-2" style={{ width: "100%", padding: "10px 16px", background: "none", border: "none", borderTop: "1px solid var(--line)", cursor: "pointer" }}>Load more</button>
          )}
        </div>
      </div>
      {/* transcript */}
      <div className="scroll-y grow" style={{ minWidth: 0, minHeight: 0, height: "100%", padding: 24, overflowX: "hidden" }}>
        {detail ? <ConversationView key={detail.conversation.thread_id} project={project} detail={detail} />
          : <div className="fg-2" style={{ padding: 40, textAlign: "center" }}>Select a conversation to see its messages.</div>}
      </div>
    </div>
  );
}

function ConversationView({ project, detail }: { project: any; detail: ConversationDetail }) {
  const c = detail.conversation;
  const [openTurn, setOpenTurn] = useState<string | null>(null);
  const [traces, setTraces] = useState<Record<string, { spans: Span[] }>>({});
  const [rerunning, setRerunning] = useState<string | null>(null);

  const toggle = async (trid: string) => {
    if (openTurn === trid) { setOpenTurn(null); return; }
    setOpenTurn(trid);
    if (!traces[trid]) {
      try { const d = await api.getTrace(project.id, trid); setTraces((t) => ({ ...t, [trid]: { spans: d.spans } })); } catch { /* ignore */ }
    }
  };

  const rerun = async (turn: Turn) => {
    if (!c.workflow_id || rerunning) return;
    setRerunning(turn.run_id);
    try {
      const run = await api.rerunRun(project.id, c.workflow_id, turn.run_id);
      let outcome = "completed";
      await openSSE(api.runStreamUrl(project.id, c.workflow_id, run.id), (frame) => {
        if (frame.event === "error") outcome = "failed";
        else if (frame.event === "interrupt") outcome = "paused for input";
      });
      window.alert(`Re-run ${outcome}. Open its new conversation from the list.`);
    } catch (error) {
      window.alert(error instanceof Error ? `Re-run failed: ${error.message}` : "Re-run failed.");
    } finally {
      setRerunning(null);
    }
  };

  return (
    <div className="fade-up col" style={{ maxWidth: 1100, margin: "0 auto", gap: 4 }}>
      {/* high-level rollup */}
      <div className="card" style={{ padding: "14px 18px", marginBottom: 12, borderLeft: "3px solid var(--accent)" }}>
        <div className="row spread">
          <div>
            <div className="t-h2" style={{ marginBottom: 2 }}>{c.actor}</div>
            <div className="fg-2 t-caption mono">{srcLabel(c.source)} · {c.turns} turn{c.turns === 1 ? "" : "s"} · started {fmtWhen(c.started_at)}</div>
          </div>
          <div className="row gap3">
            <Metric label="Turns" value={String(c.turns)} />
            <Metric label="Tokens" value={String(c.total_tokens)} />
            <Metric label="Cost" value={fmtUSD(c.total_cost_usd)} />
          </div>
        </div>
      </div>

      {/* transcript */}
      {detail.turns.map((turn) => (
        <div key={turn.trace_id} style={{ marginBottom: 14 }}>
          {turn.user_message && (
            <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}>
              <div style={{ maxWidth: "78%", background: "var(--accent)", color: "var(--fg-on-accent)", padding: "9px 13px", borderRadius: "12px 12px 3px 12px", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{turn.user_message}</div>
            </div>
          )}
          <AITurn
            turn={turn}
            open={openTurn === turn.trace_id}
            spans={traces[turn.trace_id]?.spans}
            onToggle={() => toggle(turn.trace_id)}
            onRerun={() => rerun(turn)}
            rerunning={rerunning === turn.run_id}
            canRerun={!!c.workflow_id}
          />
        </div>
      ))}
    </div>
  );
}

function AITurn({ turn, open, spans, onToggle, onRerun, rerunning, canRerun }: {
  turn: Turn;
  open: boolean;
  spans?: Span[];
  onToggle: () => void;
  onRerun: () => void;
  rerunning: boolean;
  canRerun: boolean;
}) {
  const errored = turn.status === "error" || !!turn.error;
  return (
    <div className="row" style={{ justifyContent: "flex-start" }}>
      <div style={{ maxWidth: "88%", width: "100%" }}>
        <button onClick={onToggle} className="col" style={{ width: "100%", textAlign: "left", cursor: "pointer", background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: "12px 12px 12px 3px", padding: "11px 14px" }}>
          <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
            {turn.ai_response || <span className="fg-2">{errored ? "(no response — this turn errored)" : "(no text response)"}</span>}
          </div>
          <div className="row gap2" style={{ marginTop: 8, alignItems: "center" }}>
            {errored && <span className="pill pill-err" style={{ height: 16 }}>error</span>}
            <span className="t-caption fg-2 mono">{turn.latency_ms}ms · {turn.total_tokens} tok · {fmtUSD(turn.total_cost_usd)}</span>
            <span className="grow" />
            <span className="row gap1 t-caption" style={{ color: "var(--accent)" }}>
              <Icon name="chevright" size={12} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
              {open ? "Hide trace" : "View trace"}
            </span>
          </div>
        </button>
        <div className="row" style={{ justifyContent: "flex-end", padding: "5px 4px 0" }}>
          <button
            className="t-caption"
            onClick={onRerun}
            disabled={!canRerun || rerunning}
            title={canRerun ? "Run this turn again with the same input" : "The original workflow is unavailable"}
            style={{ color: "var(--accent)", background: "none", border: "none", cursor: canRerun && !rerunning ? "pointer" : "default", opacity: canRerun ? 1 : 0.5 }}
          >
            {rerunning ? "Running again…" : "Run again"}
          </button>
        </div>
        {turn.error && <div className="mono-sm" style={{ color: "var(--err)", padding: "6px 4px", wordBreak: "break-word" }}>{turn.error}</div>}
        {open && (spans ? <div style={{ marginTop: 8 }}><SpanWaterfall spans={spans} /></div> : <div className="fg-2 t-caption" style={{ padding: "10px 4px" }}>Loading trace…</div>)}
      </div>
    </div>
  );
}

function hasDetail(s: Span): boolean {
  return s.input != null || s.output != null || !!s.error;
}

// The per-turn span waterfall (LLM/tool/chain steps), expandable to tool/LLM I/O.
function SpanWaterfall({ spans }: { spans: Span[] }) {
  const [open, setOpen] = useState<Record<string, boolean>>({});
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
    <div className="card" style={{ overflow: "hidden", maxHeight: 420, overflowY: "auto" }}>
      {spans.length === 0 && <div className="fg-2" style={{ padding: 22, textAlign: "center" }}>No spans recorded.</div>}
      {spans.map((s) => {
        const expandable = hasDetail(s);
        const isOpen = !!open[s.id];
        return (
          <div key={s.id}>
            <div
              className="row gap3"
              onClick={expandable ? () => setOpen((o) => ({ ...o, [s.id]: !o[s.id] })) : undefined}
              style={{ padding: "9px 14px", borderBottom: "1px solid var(--line)", cursor: expandable ? "pointer" : "default", background: isOpen ? "var(--bg-3)" : "transparent" }}
            >
              <div style={{ width: 200, flex: "none", paddingLeft: (depth[s.id] || 0) * 16 }}>
                <div className="row gap2">
                  {expandable
                    ? <Icon name="chevright" size={13} style={{ color: "var(--fg-2)", flex: "none", transform: isOpen ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
                    : <span style={{ width: 8, height: 8, borderRadius: 2, background: s.error ? "var(--err)" : "var(--fg-2)", flex: "none" }} />}
                  <span className="t-body-sm truncate">{s.name}</span>
                </div>
                <div className="t-caption fg-2 mono" style={{ marginLeft: 16 }}>{s.kind}{s.model ? ` · ${s.model}` : ""}</div>
              </div>
              <div className="grow" style={{ minWidth: 0, display: "flex", alignItems: "center" }}>
                <div style={{ height: 8, borderRadius: 4, background: s.error ? "var(--err)" : "var(--fg-2)", width: `${Math.max(3, (s.latency_ms / maxLatency) * 100)}%`, opacity: 0.75 }} />
                <span className="mono-sm fg-2" style={{ marginLeft: 8 }}>{s.latency_ms}ms</span>
              </div>
              <div className="col" style={{ alignItems: "flex-end", width: 120, flex: "none" }}>
                {(s.input_tokens + s.output_tokens) > 0 && <span className="mono-sm">{s.input_tokens + s.output_tokens} tok</span>}
                {s.cost_usd > 0 && <span className="t-caption fg-2">{fmtUSD(s.cost_usd)}</span>}
                {s.error && <span className="pill pill-err" style={{ height: 16 }}>error</span>}
              </div>
            </div>
            {isOpen && <SpanDetail span={s} />}
          </div>
        );
      })}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="col" style={{ alignItems: "flex-end" }}><span className="t-display" style={{ fontSize: 18 }}>{value}</span><span className="t-micro">{label}</span></div>;
}

// Is this a framed REST request/response envelope (vs a generic tool's raw args/return)?
const isRestReq = (v: any) => v && typeof v === "object" && "method" in v && "url" in v;
const isRestRes = (v: any) => v && typeof v === "object" && ("status" in v || "final_url" in v);
const nonEmpty = (v: any) => v != null && !(typeof v === "object" && Object.keys(v).length === 0) && v !== "";

function Code({ value }: { value: any }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return <pre className="mono-sm" style={{ margin: "2px 0 0", padding: "8px 10px", background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: 6, whiteSpace: "pre-wrap", wordBreak: "break-word", overflowX: "auto", maxHeight: 260, overflowY: "auto" }}>{text}</pre>;
}

function Row({ label, value }: { label: string; value: any }) {
  if (!nonEmpty(value)) return null;
  return <div style={{ marginTop: 10 }}><div className="t-caption fg-2" style={{ marginBottom: 2, textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div><Code value={value} /></div>;
}

function SpanDetail({ span }: { span: Span }) {
  const inp = span.input, out = span.output;
  return (
    <div style={{ padding: "12px 16px 16px 30px", borderBottom: "1px solid var(--line)", background: "var(--bg-3)" }}>
      {isRestReq(inp) ? (
        <>
          <div className="t-caption fg-2" style={{ textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 4 }}>Request</div>
          <div className="mono-sm" style={{ wordBreak: "break-all" }}><span className="pill" style={{ marginRight: 6 }}>{inp.method}</span>{inp.url}</div>
          <Row label="Agent args" value={inp.args} />
          <Row label="Query" value={inp.query} />
          <Row label="Headers" value={inp.headers} />
          <Row label="Cookies" value={inp.cookies} />
          <Row label={`Body${inp.body_encoding ? ` · ${inp.body_encoding}` : ""}`} value={inp.body} />
        </>
      ) : (
        <Row label="Agent input" value={inp} />
      )}

      {isRestRes(out) ? (
        <div style={{ marginTop: 14 }}>
          <div className="row gap2" style={{ marginBottom: 4 }}>
            <span className="t-caption fg-2" style={{ textTransform: "uppercase", letterSpacing: 0.4 }}>Response</span>
            {out.status != null && <span className={out.status >= 400 ? "pill pill-err" : "pill"} style={{ height: 18 }}>{out.status}</span>}
            {out.latency_ms != null && <span className="mono-sm fg-2">{out.latency_ms}ms</span>}
          </div>
          {out.final_url && out.final_url !== inp?.url && <div className="mono-sm fg-2" style={{ wordBreak: "break-all", marginBottom: 4 }}>→ {out.final_url}</div>}
          <Row label="Body" value={out.response} />
          {out.error && <Row label="Error" value={out.error} />}
        </div>
      ) : (
        <Row label="Output" value={out} />
      )}

      {span.error && <div style={{ marginTop: 12 }}><div className="t-caption" style={{ color: "var(--err)", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 2 }}>Error</div><Code value={span.error} /></div>}
    </div>
  );
}
