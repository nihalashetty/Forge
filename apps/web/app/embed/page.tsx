"use client";
/* Standalone embeddable chat widget (Phase 3b/4). Served same-origin from /embed and dropped
   into any allowed site via an <iframe> (see /launcher.js for the floating-bubble launcher).
   Gated by a publishable key (?key=…); identity comes from an optional verified ?session_token=…
   Reaches Playground parity: structured replies + inline components + human-in-the-loop
   approvals (interrupt → approval card → resume). Operator-only affordances (the run-steps
   panel and token/cost meter) are intentionally NOT shown to end users. */
import { useEffect, useRef, useState } from "react";
import { openSSE } from "@/lib/api";
import { Markdown } from "@/components/markdown";
import { ComponentRenderer } from "@/components/component-renderer";
import { Icon } from "@/components/icons";
import Mustache from "mustache";

type Part = { kind: "text"; text: string } | { kind: "component"; inst: any };
interface Msg { role: "user" | "assistant"; content?: string; parts?: Part[] }

const EMBED = (key: string, path: string) => `/api/forge/v1/embed/${encodeURIComponent(key)}${path}`;

/* Normalize a LangGraph interrupt payload into {prompt, decisions, middleware} — ported
   verbatim from the Playground so the widget builds the same buttons AND the same resume value
   encoding. The `middleware` flag is load-bearing: it selects {decisions:[{type}]} vs a bare
   string at resume time and MUST be recomputed from the stored payload then. */
function parseInterrupt(payload: any): { prompt: string; decisions: string[]; middleware: boolean } {
  const flat = (x: any): any[] => (Array.isArray(x) ? x.flatMap(flat) : [x]);
  const items = flat(payload).filter(Boolean);
  const values = items.map((i) => (i && typeof i === "object" && "value" in i ? (i as any).value : i));
  for (const v of values) {
    if (v && typeof v === "object" && (v as any).prompt) {
      return { prompt: String((v as any).prompt), decisions: (v as any).allowed_decisions || ["approve", "reject"], middleware: false };
    }
    if (v && typeof v === "object" && ((v as any).action_requests || (v as any).action_request || (v as any).action)) {
      const reqs = (v as any).action_requests || [(v as any).action_request || v];
      const desc = reqs.map((r: any) => r.description || `${r.action || r.name || "tool"}(${JSON.stringify(r.args || {}).slice(0, 80)})`).join("; ");
      return { prompt: `Approve action: ${desc}`, decisions: ["approve", "reject"], middleware: true };
    }
  }
  return { prompt: "This needs your approval to continue.", decisions: ["approve", "reject"], middleware: false };
}

export default function EmbedWidget() {
  const [key, setKey] = useState<string | null>(null);
  const [sessionToken, setSessionToken] = useState<string | null>(null);
  const [cfg, setCfg] = useState<{ name: string; workflow_id: string } | null>(null);
  const [compDefs, setCompDefs] = useState<Record<string, any>>({});
  const [err, setErr] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [liveParts, setLiveParts] = useState<Part[]>([]);
  const [pendingInterrupt, setPendingInterrupt] = useState<{ runId: string; payload: any } | null>(null);
  const [resuming, setResuming] = useState(false);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [embedded, setEmbedded] = useState(false);
  const threadRef = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const parentOriginRef = useRef<string>("");

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    setKey(sp.get("key"));
    setSessionToken(sp.get("session_token"));
  }, []);

  // Launcher handshake (only when embedded in a parent frame). The launcher passes its origin
  // via ?host=… (the trusted source); we fall back to the referrer origin. We NEVER trust "*":
  // without a validated host origin we post nothing on the channel and hide the close button.
  // Escape is handled HERE (inside the iframe) because a host-page key handler can't see
  // keystrokes once focus is in this cross-origin frame.
  useEffect(() => {
    if (window.parent === window) return;
    const sp = new URLSearchParams(window.location.search);
    let po = sp.get("host") || "";
    if (!po) { try { po = document.referrer ? new URL(document.referrer).origin : ""; } catch { po = ""; } }
    try { po = po ? new URL(po).origin : ""; } catch { po = ""; }
    parentOriginRef.current = po;
    setEmbedded(!!po);
    if (po) { try { window.parent.postMessage({ type: "forge:ready" }, po); } catch { /* ignore */ } }
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") closeWidget(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!key) return;
    fetch(EMBED(key, "/config"))
      .then((r) => { if (!r.ok) throw new Error("This chat is unavailable."); return r.json(); })
      .then(setCfg)
      .catch((e) => setErr(String(e.message || e)));
    fetch(EMBED(key, "/components"))
      .then((r) => (r.ok ? r.json() : []))
      .then((cs: any[]) => setCompDefs(Object.fromEntries((cs || []).map((c) => [c.id, c]))))
      .catch(() => {});
  }, [key]);

  useEffect(() => { scrollRef.current?.scrollTo({ top: 1e9, behavior: "smooth" }); }, [msgs, liveParts, pendingInterrupt]);

  function closeWidget() {
    const po = parentOriginRef.current;
    if (!po) return; // only post to a validated host origin, never "*"
    try { window.parent.postMessage({ type: "forge:close" }, po); } catch { /* ignore */ }
  }

  async function send(text: string) {
    const q = text.trim();
    if (!q || !key || !cfg || running) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: q }]);
    setRunning(true);
    setLiveParts([]);
    let buffer = "";
    let finalAnswer = "";
    let interrupted = false;
    const parts: Part[] = [];
    const pushText = (s: string) => {
      const last = parts[parts.length - 1];
      if (last && last.kind === "text") last.text += s;
      else parts.push({ kind: "text", text: s });
    };
    try {
      const runRes = await fetch(EMBED(key, "/runs"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input: { messages: [{ role: "user", content: q }] },
          thread_id: threadRef.current || undefined,
          session_token: sessionToken || undefined,
        }),
      });
      if (!runRes.ok) throw new Error("Could not start the chat.");
      const run = await runRes.json();
      threadRef.current = run.thread_id;
      await openSSE(EMBED(key, `/runs/${run.id}/stream`), (f) => {
        if (f.event === "messages" && f.data?.content) { buffer += f.data.content; pushText(f.data.content); setLiveParts([...parts]); }
        else if (f.event === "custom" && f.data?.channel === "component" && f.data?.payload) { parts.push({ kind: "component", inst: f.data.payload }); setLiveParts([...parts]); }
        else if (f.event === "interrupt") { interrupted = true; setPendingInterrupt({ runId: run.id, payload: f.data }); }
        else if (f.event === "done") { finalAnswer = f.data?.answer || ""; }
        else if (f.event === "error") { finalAnswer = `⚠ ${f.data?.message || "error"}`; }
      });
      // Paused for approval: commit whatever streamed before the pause, then hand off to the card.
      if (interrupted) {
        const hadComp = parts.some((p) => p.kind === "component");
        if (hadComp || buffer.trim()) {
          setMsgs((m) => [...m, hadComp
            ? { role: "assistant", parts: parts.filter((p) => p.kind === "component" || (p.kind === "text" && (p as any).text.trim())) }
            : { role: "assistant", content: buffer }]);
        }
        return; // approval card takes over; `finally` resets running/liveParts
      }
    } catch (e: any) {
      finalAnswer = `⚠ ${e.message || e}`;
    } finally {
      setRunning(false);
      setLiveParts([]);
    }
    const hasComp = parts.some((p) => p.kind === "component");
    if (hasComp) {
      const streamed = parts.filter((p) => p.kind === "text").map((p: any) => p.text).join("").trim();
      const fa = (finalAnswer || "").trim();
      if (fa && fa !== streamed) parts.push({ kind: "text", text: finalAnswer });
      setMsgs((m) => [...m, { role: "assistant", parts: parts.filter((p) => p.kind === "component" || (p.kind === "text" && (p as any).text.trim())) }]);
    } else {
      setMsgs((m) => [...m, { role: "assistant", content: finalAnswer || buffer || "(no output)" }]);
    }
  }

  // Submit an approval decision and render the resumed reply (single JSON response, like the
  // Playground). The resume value encoding depends on the interrupt kind (middleware vs human_input).
  async function resume(decision: string) {
    if (!pendingInterrupt || !key || resuming) return;
    const { middleware } = parseInterrupt(pendingInterrupt.payload);
    setResuming(true);
    try {
      const value = middleware ? { decisions: [{ type: decision }] } : decision;
      const res = await fetch(EMBED(key, `/runs/${pendingInterrupt.runId}/resume`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value }),
      });
      const data = await res.json().catch(() => ({} as any));
      if (!res.ok || data.error) throw new Error(data.error || `resume failed (${res.status})`);
      const out: any[] = data.messages || [];
      const last = [...out].reverse().find((m) => (m.type === "ai" || m.role === "assistant") && m.content);
      const content = last ? (typeof last.content === "string" ? last.content : JSON.stringify(last.content)) : "(resumed)";
      setMsgs((m) => [...m, { role: "assistant", content: data.interrupted ? content + "\n\n⏸ This needs another approval step." : content }]);
    } catch (e: any) {
      setMsgs((m) => [...m, { role: "assistant", content: `⚠ resume failed: ${e.message || e}` }]);
    } finally {
      setPendingInterrupt(null);
      setResuming(false);
    }
  }

  function onAction(inst: any, action: string, fields: Record<string, string>) {
    const def = (inst.actions || []).find((a: any) => a.id === action) || {};
    let msg = def.message || def.label || action;
    try { msg = Mustache.render(String(msg), { props: inst.props || {}, fields, action }); } catch {}
    if (msg) send(msg);
  }

  if (err) return <div style={{ padding: 24, color: "var(--fg-2)", fontFamily: "var(--font-ui)" }}>{err}</div>;

  return (
    <div className="col" style={{ height: "100vh", background: "var(--bg-1)" }}>
      <div className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)", alignItems: "center", justifyContent: "space-between", flex: "none" }}>
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 600, fontSize: 15, color: "var(--fg-0)" }}>{cfg?.name || "Chat"}</span>
        {embedded && (
          <button aria-label="Close chat" className="btn btn-ghost btn-sm" onClick={closeWidget} style={{ padding: 4 }}>
            <Icon name="x" size={16} />
          </button>
        )}
      </div>
      <div ref={scrollRef} className="scroll-y col gap4" style={{ flex: 1, minHeight: 0, padding: 16 }}>
        {msgs.length === 0 && !running && <div className="fg-2" style={{ fontSize: 13 }}>Ask a question to get started.</div>}
        {msgs.map((m, i) => <EmbedMsg key={i} m={m} compDefs={compDefs} onAction={onAction} />)}
        {(running || liveParts.length > 0) && <EmbedMsg m={{ role: "assistant", parts: liveParts }} streaming compDefs={compDefs} onAction={onAction} />}
        {pendingInterrupt && (() => {
          const info = parseInterrupt(pendingInterrupt.payload);
          return (
            <div className="card fade-up" style={{ padding: 14, borderLeft: "3px solid var(--warn)" }}>
              <div className="row gap2" style={{ alignItems: "center", marginBottom: 8 }}>
                <Icon name="user" size={15} style={{ color: "var(--warn)" }} />
                <span className="t-h3">Approval required</span>
              </div>
              <div className="t-body-sm fg-1" style={{ whiteSpace: "pre-wrap", marginBottom: 10 }}>{info.prompt}</div>
              <div className="row gap2" style={{ flexWrap: "wrap" }}>
                {info.decisions.map((d) => (
                  <button key={d} className={"btn btn-sm " + (d === "approve" ? "btn-primary" : "btn-secondary")} onClick={() => resume(d)} disabled={resuming}>
                    {resuming ? "…" : d}
                  </button>
                ))}
              </div>
            </div>
          );
        })()}
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--line)", flex: "none" }}>
        <div className="row gap2" style={{ background: "var(--bg-3)", border: "1px solid var(--line)", borderRadius: 10, padding: "6px 6px 6px 12px" }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send(input)} placeholder="Message…" disabled={!cfg || running}
            style={{ flex: 1, minWidth: 0, border: "none", background: "none", outline: "none", fontSize: 14, color: "var(--fg-0)", fontFamily: "var(--font-ui)" }} />
          <button className="btn btn-primary btn-sm" onClick={() => send(input)} disabled={!cfg || running || !input.trim()}>Send</button>
        </div>
      </div>
    </div>
  );
}

function EmbedMsg({ m, streaming, compDefs, onAction }: { m: Msg; streaming?: boolean; compDefs: Record<string, any>; onAction: (inst: any, a: string, f: Record<string, string>) => void }) {
  if (m.role === "user") {
    return (
      <div className="row" style={{ flexDirection: "row-reverse" }}>
        <div style={{ maxWidth: "85%", padding: "9px 12px", borderRadius: 12, borderTopRightRadius: 3, fontSize: 14, lineHeight: "21px", whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: "var(--accent)", color: "var(--fg-on-accent)" }}>{m.content}</div>
      </div>
    );
  }
  const list: Part[] = m.parts && m.parts.length ? m.parts : (m.content && m.content.trim() ? [{ kind: "text", text: m.content }] : []);
  let lastText = -1;
  for (let k = list.length - 1; k >= 0; k--) { if (list[k].kind === "text") { lastText = k; break; } }
  return (
    <div className="col gap2" style={{ minWidth: 0 }}>
      {list.map((p, j) => {
        if (p.kind === "text") {
          return (
            <div key={j} style={{ fontSize: 14, lineHeight: "21px", color: "var(--fg-0)", overflowWrap: "anywhere" }}>
              <Markdown>{p.text}</Markdown>
              {streaming && j === lastText && <span style={{ display: "inline-block", width: 7, height: 14, background: "var(--accent)", animation: "blink 1s steps(1) infinite" }} />}
            </div>
          );
        }
        const inst = (p as any).inst;
        const def = compDefs[inst.component_id];
        if (!def) return <div key={j} className="card" style={{ padding: "8px 11px", fontSize: 12.5, color: "var(--fg-2)" }}>Component unavailable.</div>;
        return <ComponentRenderer key={j} def={{ id: def.id, name: def.name, html: def.html, css: def.css, actions: inst.actions || def.actions }} props={inst.props || {}} onAction={(a, f) => onAction(inst, a, f)} />;
      })}
      {streaming && lastText === -1 && <span className="fg-2" style={{ fontSize: 14 }}>…</span>}
    </div>
  );
}
