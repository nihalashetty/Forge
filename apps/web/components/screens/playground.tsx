"use client";
/* Playground — chat that runs a real workflow over SSE with token-by-token streaming. */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { Tile } from "../primitives";
import { api, openSSE, Workflow } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

interface ChatMsg { role: "user" | "assistant"; content: string }
interface Step { node: string }

export function PlaygroundScreen({ project }: { project: any }) {
  const [wf, setWf] = useState<Workflow | null>(null);
  const [wfs, setWfs] = useState<Workflow[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [streaming, setStreaming] = useState("");
  const [steps, setSteps] = useState<Step[]>([]);
  const [running, setRunning] = useState(false);
  const [meter, setMeter] = useState<{ tokens: number; cost: number } | null>(null);
  const [pendingInterrupt, setPendingInterrupt] = useState<{ runId: string; payload: any } | null>(null);
  const [resuming, setResuming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  // One backend thread per chat session: the checkpointer holds the conversation, so
  // each turn sends ONLY the new message (no full-transcript replay).
  const threadRef = useRef<string | null>(null);

  useEffect(() => {
    if (!project?.id) return;
    threadRef.current = null;
    setWf(null); setLoadErr(null); setMsgs([]); setSteps([]); setMeter(null);
    api.listWorkflows(project.id)
      .then((ws) => {
        setWfs(ws);
        const active = ws.find((w) => w.status === "active") || ws[0] || null;
        setWf(active);
        if (!active) setLoadErr("No workflows in this project yet. Create one in Workflows, or ask the Forge Assistant to build one.");
      })
      .catch((e) => setLoadErr(String(e.message || e)));
  }, [project?.id]);

  useEffect(() => { scrollRef.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [msgs, streaming, steps]);

  async function send() {
    if (!input.trim() || !wf || running) return;
    const text = input.trim();
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setStreaming(""); setSteps([]); setMeter(null); setRunning(true);
    let buffer = "";
    let finalAnswer = "";
    try {
      // The thread's checkpointer holds prior turns, so send only the new message when a
      // thread exists; the first turn establishes the thread.
      const run = await api.createRun(
        project.id, wf.id,
        { messages: [{ role: "user", content: text }] },
        threadRef.current || undefined,
      );
      threadRef.current = run.thread_id;
      let interrupted = false;
      const url = api.runStreamUrl(project.id, wf.id, run.id);
      await openSSE(url, (f) => {
        if (f.event === "messages" && f.data?.content) {
          buffer += f.data.content;
          setStreaming(buffer);
        } else if ((f.event === "node_start" || f.event === "updates") && f.data) {
          const node = f.event === "node_start" ? f.data.node : Object.keys(f.data || {})[0];
          if (node) setSteps((s) => (s.some((x) => x.node === node) ? s : [...s, { node }]));
        } else if (f.event === "done") {
          finalAnswer = f.data?.answer || "";
          setMeter({ tokens: f.data?.total_tokens ?? 0, cost: f.data?.total_cost_usd ?? 0 });
        } else if (f.event === "interrupt") {
          interrupted = true;
          setPendingInterrupt({ runId: run.id, payload: f.data });
        } else if (f.event === "error") {
          finalAnswer = `⚠ ${f.data?.message || "run failed"}`;
        }
      });
      if (interrupted) { setStreaming(""); setRunning(false); return; } // approval card takes over
    } catch (e: any) {
      finalAnswer = `⚠ ${e.message || e}`;
    } finally {
      setStreaming("");
      setRunning(false);
    }
    setMsgs((m) => [...m, { role: "assistant", content: finalAnswer || buffer || "(no output)" }]);
  }

  /** Pull the human-facing prompt + decision options out of the interrupt payload.
      Handles both shapes: the human_input node ({prompt, allowed_decisions}) and
      HumanInTheLoopMiddleware (action requests; resume wants {decisions:[{type}]}). */
  function parseInterrupt(payload: any): { prompt: string; decisions: string[]; middleware: boolean } {
    const flat = (x: any): any[] => (Array.isArray(x) ? x.flatMap(flat) : [x]);
    const items = flat(payload).filter(Boolean);
    const values = items.map((i) => (i && typeof i === "object" && "value" in i ? i.value : i));
    for (const v of values) {
      if (v && typeof v === "object" && v.prompt) {
        return { prompt: String(v.prompt), decisions: v.allowed_decisions || ["approve", "reject"], middleware: false };
      }
      if (v && typeof v === "object" && (v.action_requests || v.action_request || v.action)) {
        const reqs = v.action_requests || [v.action_request || v];
        const desc = reqs.map((r: any) => r.description || `${r.action || r.name || "tool"}(${JSON.stringify(r.args || {}).slice(0, 80)})`).join("; ");
        return { prompt: `Approve tool call: ${desc}`, decisions: ["approve", "reject"], middleware: true };
      }
    }
    return { prompt: "This run paused for your approval.", decisions: ["approve", "reject"], middleware: false };
  }

  async function resume(decision: string) {
    if (!pendingInterrupt || !wf || resuming) return;
    const { middleware } = parseInterrupt(pendingInterrupt.payload);
    setResuming(true);
    try {
      const value = middleware ? { decisions: [{ type: decision }] } : decision;
      const res = await api.resumeRun(project.id, wf.id, pendingInterrupt.runId, value);
      const msgsOut = res.messages || [];
      const last = [...msgsOut].reverse().find((m: any) => (m.type === "ai" || m.role === "assistant") && m.content);
      const content = last ? (typeof last.content === "string" ? last.content : JSON.stringify(last.content)) : (res.error || "(resumed)");
      setMsgs((m) => [...m, { role: "assistant", content: res.interrupted ? content + "\n⏸ paused again for another approval — check Traces." : content }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `⚠ resume failed: ${e.message || e}` }]);
    } finally {
      setPendingInterrupt(null);
      setResuming(false);
    }
  }

  const samples = ["How to convert quote to order", "What can you help me with?"];

  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      {/* header */}
      <div className="row spread" style={{ padding: "12px 20px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        <div className="row gap2">
          <Tile icon="playground" color="var(--io-json)" size={30} />
          <div>
            <div className="t-h2">Playground</div>
            {wfs.length > 1 ? (
              <select
                className="select" disabled={running}
                value={wf?.id || ""}
                style={{ marginTop: 2, height: 24, fontSize: 12, padding: "0 6px", maxWidth: 280 }}
                onChange={(e) => {
                  const next = wfs.find((w) => w.id === e.target.value) || null;
                  threadRef.current = null;
                  setWf(next); setMsgs([]); setSteps([]); setMeter(null); setStreaming("");
                }}>
                {wfs.map((w) => (
                  <option key={w.id} value={w.id}>{w.name}{w.status === "active" ? " · active" : " · draft"}</option>
                ))}
              </select>
            ) : (
              <div className="fg-2 t-caption mono">{wf ? wf.name : "loading…"}{running && " · running"}</div>
            )}
          </div>
        </div>
        <div className="row gap2">
          {meter && (
            <span className="chip chip-mono"><Icon name="bolt" size={13} />{meter.tokens} tok · {fmtUSD(meter.cost)}</span>
          )}
          <span className="chip chip-mono"><Icon name="knowledge" size={12} />grounded</span>
          <button className="btn btn-ghost btn-sm" onClick={() => { setMsgs([]); setSteps([]); setMeter(null); }} disabled={running}>
            <Icon name="refresh" size={14} />Reset
          </button>
        </div>
      </div>

      {/* alignItems:stretch — the global .row centers children, which stops the chat column
          from filling the height: it then sizes to content, overflows the viewport, and the
          scroll area never scrolls. Stretch restores the fixed-height column + inner scroll. */}
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        <div className="col grow" style={{ minWidth: 0, minHeight: 0, borderRight: "1px solid var(--line)" }}>
          <div ref={scrollRef} className="scroll-y" style={{ flex: 1, minHeight: 0, padding: "20px 0" }}>
            {/* minHeight:100% + justify-end pins a short conversation to the bottom (chat-style);
                once it outgrows the viewport it scrolls normally. */}
            <div style={{ maxWidth: 720, margin: "0 auto", padding: "0 24px", width: "100%", minHeight: "100%", display: "flex", flexDirection: "column", justifyContent: "flex-end" }}>
              {loadErr && <div className="card" style={{ padding: 14, color: "var(--err)", marginBottom: 12 }}>{loadErr}</div>}
              {msgs.length === 0 && !streaming && !loadErr && (
                <div className="col center" style={{ minHeight: 300, gap: 10, color: "var(--fg-2)", textAlign: "center", margin: "auto 0" }}>
                  <Tile icon="sparkles" color="var(--accent)" size={44} glow />
                  <div className="t-h2" style={{ color: "var(--fg-1)" }}>Run “{wf?.name || "your workflow"}” live</div>
                  <div className="t-caption">Answers are grounded in this project’s knowledge base & Q&A — it streams token by token.</div>
                  <div className="row gap2 wrap center" style={{ maxWidth: 460, marginTop: 6 }}>
                    {samples.map((s) => (
                      <button key={s} className="chip" style={{ cursor: "pointer" }} onClick={() => setInput(s)}>{s}</button>
                    ))}
                  </div>
                </div>
              )}
              <div className="col gap4">
                {msgs.map((m, i) => <Bubble key={i} role={m.role} content={m.content} />)}
                {(streaming || running) && <Bubble role="assistant" content={streaming} streaming />}
                {pendingInterrupt && (() => {
                  const info = parseInterrupt(pendingInterrupt.payload);
                  return (
                    <div className="card fade-up" style={{ padding: 14, borderLeft: "3px solid var(--warn)" }}>
                      <div className="row gap2" style={{ marginBottom: 6 }}>
                        <Icon name="user" size={15} style={{ color: "var(--warn)" }} />
                        <span className="t-h3">Approval required</span>
                      </div>
                      <div className="t-body-sm fg-1" style={{ marginBottom: 10, whiteSpace: "pre-wrap" }}>{info.prompt}</div>
                      <div className="row gap2">
                        {info.decisions.map((d) => (
                          <button key={d}
                            className={d === "approve" ? "btn btn-primary btn-sm" : "btn btn-secondary btn-sm"}
                            disabled={resuming} onClick={() => resume(d)}>
                            {resuming ? "…" : d}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })()}
              </div>
            </div>
          </div>
          {/* composer */}
          <div style={{ padding: "14px 24px", borderTop: "1px solid var(--line)", flex: "none" }}>
            <div style={{ maxWidth: 720, margin: "0 auto" }}>
              <div className="row gap2" style={{ background: "var(--bg-1)", border: "1px solid var(--line-strong)", borderRadius: 12, padding: "7px 7px 7px 14px", boxShadow: "var(--sh-1)" }}>
                <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
                  placeholder="Message the workflow…" disabled={!wf || running}
                  style={{ flex: 1, minWidth: 0, border: "none", background: "none", outline: "none", fontSize: 14, color: "var(--fg-0)", fontFamily: "var(--font-ui)" }} />
                <button className="btn btn-primary" onClick={send} disabled={!wf || running}>
                  <Icon name={running ? "refresh" : "play"} size={15} style={running ? { animation: "spin 1s linear infinite" } : {}} />{running ? "Running" : "Run"}
                </button>
              </div>
              <div className="fg-2 t-caption" style={{ textAlign: "center", marginTop: 7 }}>Runs against the active workflow · interrupts surface for approval</div>
            </div>
          </div>
        </div>

        {/* Steps column */}
        <div style={{ width: 280, flex: "none", background: "var(--bg-1)", minHeight: 0 }} className="scroll-y">
          <div className="t-micro" style={{ padding: "14px 16px 8px" }}>Run steps</div>
          <div className="col" style={{ padding: "0 12px 12px" }}>
            {steps.length === 0 && <div className="fg-2 t-caption" style={{ padding: "4px 8px" }}>Nodes light up as the graph executes.</div>}
            {steps.map((s, i) => (
              <div key={i} className="row gap2 fade-in" style={{ padding: "8px 8px", borderRadius: 7 }}>
                <div style={{ width: 18, height: 18, borderRadius: "50%", background: "var(--ok-bg)", color: "var(--ok)", display: "flex", alignItems: "center", justifyContent: "center", flex: "none" }}><Icon name="check" size={12} /></div>
                <span className="mono-sm grow truncate" style={{ color: "var(--fg-1)" }}>{s.node}</span>
                <span className="t-caption fg-2">{i + 1}</span>
              </div>
            ))}
            {running && (
              <div className="row gap2" style={{ padding: "8px", color: "var(--accent)" }}>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--accent)", animation: "pulse 1s infinite" }} />
                <span className="t-caption">streaming…</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Bubble({ role, content, streaming }: { role: "user" | "assistant"; content: string; streaming?: boolean }) {
  const user = role === "user";
  return (
    <div className="row" style={{ gap: 9, alignItems: "flex-start", flexDirection: user ? "row-reverse" : "row" }}>
      {user ? <Tile icon="user" color="var(--signal)" size={28} /> : <Tile icon="sparkles" color="var(--accent)" size={28} />}
      <div style={{ maxWidth: 540, minWidth: 0, padding: "10px 13px", borderRadius: 12, fontSize: 14, lineHeight: "21px", whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "break-word", background: user ? "var(--accent)" : "var(--bg-2)", color: user ? "var(--fg-on-accent)" : "var(--fg-0)", border: user ? "none" : "1px solid var(--line)", borderTopRightRadius: user ? 3 : 12, borderTopLeftRadius: user ? 12 : 3 }}>
        {content}
        {streaming && <span style={{ display: "inline-block", width: 7, height: 14, background: "var(--accent)", marginLeft: 2, verticalAlign: "-2px", animation: "blink 1s steps(1) infinite" }} />}
      </div>
    </div>
  );
}
