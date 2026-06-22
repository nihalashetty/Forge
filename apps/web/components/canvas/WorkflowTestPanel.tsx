"use client";
/* The canvas Test panel: messages a workflow over SSE and lights up nodes as the graph
   executes (one backend thread per session so the checkpointer holds prior turns). */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { Tile } from "../primitives";
import { api, openSSE, type Workflow, type ComponentT } from "@/lib/api";
import { fmtUSD } from "@/lib/data";
import { ComponentRenderer } from "../component-renderer";
import { Markdown } from "../markdown";
import { ReplyAccumulator, type Part, type ComponentInstance } from "@/lib/chat-parts";
import Mustache from "mustache";

interface TestMsg { role: "user" | "assistant"; content?: string; parts?: Part[] }

export function WorkflowTestPanel({
  project,
  workflow,
  running,
  onRunningChange,
  onBeforeRun,
  onClose,
  onResetRun,
  onNodeStep,
  onFinalDebug,
}: {
  project: any;
  workflow: Workflow;
  running: boolean;
  onRunningChange: (running: boolean) => void;
  onBeforeRun: () => Promise<void>;
  onClose: () => void;
  onResetRun: () => void;
  onNodeStep: (nodeId: string, status?: "idle" | "running" | "done" | "error", output?: any) => void;
  onFinalDebug: (debugNodes: Record<string, any>) => void;
}) {
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<TestMsg[]>([]);
  const [streaming, setStreaming] = useState("");
  const [meter, setMeter] = useState<{ tokens: number; cost: number } | null>(null);
  const [compDefs, setCompDefs] = useState<Record<string, ComponentT>>({});
  const [liveParts, setLiveParts] = useState<Part[]>([]);  // in-flight assistant reply parts (audit H3)
  const activeRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  // One backend thread per test session - the checkpointer holds prior turns.
  const threadRef = useRef<string | null>(null);
  useEffect(() => {
    threadRef.current = null; setMsgs([]);
    if (project?.id) api.listComponents(project.id).then((cs) => setCompDefs(Object.fromEntries(cs.map((c) => [c.id, c])))).catch(() => {});
  }, [workflow?.id, project?.id]);

  useEffect(() => {
    activeRef.current = true;
    return () => {
      activeRef.current = false;
      onRunningChange(false);
    };
  }, [onRunningChange]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, streaming, meter, liveParts]);

  async function send(textArg?: string) {
    const text = (typeof textArg === "string" ? textArg : input).trim();
    if (!text || running || !project?.id || !workflow?.id) return;
    if (typeof textArg !== "string") setInput("");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setStreaming("");
    setMeter(null);
    setLiveParts([]);
    onResetRun();
    onRunningChange(true);
    let finalAnswer = "";
    let lastNode: string | null = null;
    // Components are positioned by the [[forge:component:ID]] markers the agent writes into its
    // reply (not by frame-arrival order), so a widget lands in its natural place, not at the top.
    const acc = new ReplyAccumulator();

    try {
      await onBeforeRun();
      if (!activeRef.current) return;
      const run = await api.createRun(
        project.id, workflow.id,
        { messages: [{ role: "user", content: text }] },
        threadRef.current || undefined,
      );
      threadRef.current = run.thread_id;
      await openSSE(api.runStreamUrl(project.id, workflow.id, run.id), (f) => {
        if (!activeRef.current) return;
        if (f.event === "messages" && f.data?.content) {
          acc.addText(f.data.content);
          setStreaming(acc.text);
          setLiveParts(acc.parts({ streaming: true }));
        } else if (f.event === "node_start" && f.data?.node) {
          const node = f.data.node;
          lastNode = node;
          onNodeStep(node, "running");
        } else if (f.event === "node_error" && f.data?.node) {
          const node = f.data.node;
          onNodeStep(node, "error");
          finalAnswer = `⚠ ${f.data?.message || `${node} failed`}`;
        } else if (f.event === "updates" && f.data && typeof f.data === "object") {
          const node = Object.keys(f.data)[0];
          if (!node) return;
          const output = f.data[node];
          onNodeStep(node, "done", output);
          if (lastNode === node) lastNode = null;
        } else if (f.event === "custom" && f.data?.channel === "component" && f.data?.payload) {
          acc.addComponent(f.data.payload as ComponentInstance);
          setLiveParts(acc.parts({ streaming: true }));
        } else if (f.event === "done") {
          finalAnswer = f.data?.answer || "";
          setMeter({ tokens: f.data?.total_tokens ?? 0, cost: f.data?.total_cost_usd ?? 0 });
          onFinalDebug(f.data?.debug?.nodes || {});
          if (lastNode) onNodeStep(lastNode, "done");
        } else if (f.event === "interrupt") {
          finalAnswer = "⏸ This run paused for approval. Open the full Playground to resume it.";
          if (lastNode) onNodeStep(lastNode, "done");
        } else if (f.event === "error") {
          finalAnswer = `⚠ ${f.data?.message || "run failed"}`;
          if (lastNode) onNodeStep(lastNode, "error");
        }
      });
    } catch (e: any) {
      if (!activeRef.current) return;
      finalAnswer = `⚠ ${e.message || e}`;
      if (lastNode) onNodeStep(lastNode, "error");
    } finally {
      if (activeRef.current) {
        setStreaming("");
        onRunningChange(false);
        setLiveParts([]);
      }
    }
    if (activeRef.current) {
      // resolveText reconciles the streamed buffer with the authoritative answer / error so it's
      // never dropped (audit H2); markers in it splice components into place.
      const finalText = acc.resolveText(finalAnswer);
      if (acc.hasComponents()) {
        setMsgs((m) => [...m, { role: "assistant", parts: acc.parts({ finalText }) }]);
      } else {
        setMsgs((m) => [...m, { role: "assistant", content: finalText || "(no output)" }]);
      }
    }
  }

  function handleComponentAction(inst: ComponentInstance, action: string, fields: Record<string, string>) {
    const def = (inst.actions || []).find((a: any) => a.id === action) || {};
    let msg = (def as any).message || (def as any).label || action;
    try { msg = Mustache.render(String(msg), { props: inst.props || {}, fields, action }); } catch {}
    if (msg) send(msg);
  }

  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div className="row spread" style={{ padding: "12px 14px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        <div style={{ minWidth: 0 }}>
          <div className="t-h2">Test</div>
          <div className="fg-2 t-caption mono truncate">{workflow.name}</div>
        </div>
        <button className="iconbtn" onClick={onClose}><Icon name="x" size={15} /></button>
      </div>
      <div ref={scrollRef} className="scroll-y col gap3" style={{ flex: 1, minHeight: 0, padding: 12, overflowX: "hidden" }}>
        {msgs.length === 0 && !streaming && !running && (
          <div className="col center" style={{ minHeight: 160, textAlign: "center", gap: 8, color: "var(--fg-2)" }}>
            <Tile icon="playground" color="var(--io-json)" size={38} />
            <div className="t-h3" style={{ color: "var(--fg-1)" }}>Message this workflow</div>
            <div className="t-caption">Nodes highlight as the graph executes. Hover the debug chips on nodes for output and cost.</div>
          </div>
        )}
        {msgs.map((m, i) => (
          <TestMessage key={i} role={m.role} content={m.content} parts={m.parts} compDefs={compDefs} onAction={handleComponentAction} />
        ))}
        {(running || liveParts.length > 0) && (
          <TestMessage role="assistant" parts={liveParts} streaming compDefs={compDefs} onAction={handleComponentAction} />
        )}
        {meter && <span className="chip chip-mono" style={{ alignSelf: "flex-start" }}><Icon name="bolt" size={12} />{meter.tokens} tok · {fmtUSD(meter.cost)}</span>}
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--line)", flex: "none" }}>
        <div className="row gap2" style={{ background: "var(--bg-3)", border: "1px solid var(--line)", borderRadius: 10, padding: "6px 6px 6px 10px" }}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask this workflow..."
            disabled={running}
            style={{ flex: 1, minWidth: 0, border: "none", background: "none", outline: "none", fontSize: 13, color: "var(--fg-0)", fontFamily: "var(--font-ui)" }}
          />
          <button className="btn btn-primary btn-sm" onClick={() => send()} disabled={running || !input.trim()}>
            <Icon name={running ? "refresh" : "play"} size={13} style={running ? { animation: "spin 1s linear infinite" } : {}} />
            {running ? "Testing" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* One test-panel turn: a single avatar + a column. The assistant reply is an ordered list
   of parts (text + components interleaved in produced order), matching the Playground so a
   rendered component sits in its correct place within the reply. */
function TestMessage({ role, content, streaming, parts, compDefs, onAction }: {
  role: "user" | "assistant";
  content?: string;
  streaming?: boolean;
  parts?: Part[];
  compDefs: Record<string, ComponentT>;
  onAction: (inst: ComponentInstance, action: string, fields: Record<string, string>) => void;
}) {
  const user = role === "user";
  if (user) {
    return (
      <div className="row" style={{ gap: 8, alignItems: "flex-start", flexDirection: "row-reverse" }}>
        <Tile icon="user" color="var(--signal)" size={24} />
        <div style={{ maxWidth: 260, padding: "8px 10px", borderRadius: 10, borderTopRightRadius: 3, fontSize: 13, lineHeight: "19px", whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: "var(--accent)", color: "var(--fg-on-accent)" }}>
          {content}
        </div>
      </div>
    );
  }
  const renderComp = (inst: ComponentInstance, key: number | string) => {
    const def = compDefs[inst.component_id];
    if (!def) {
      return <div key={key} className="card" style={{ padding: "6px 9px", fontSize: 12, color: "var(--fg-2)" }}>Component “{inst.name || inst.component_id}” unavailable.</div>;
    }
    return (
      <ComponentRenderer key={key} def={{ id: def.id, name: def.name, html: def.html, css: def.css, actions: inst.actions || def.actions }} props={inst.props || {}} onAction={(a, f) => onAction(inst, a, f)} />
    );
  };
  const list: Part[] = parts && parts.length ? parts : (content && content.trim() ? [{ kind: "text", text: content }] : []);
  let lastText = -1;
  for (let k = list.length - 1; k >= 0; k--) { if (list[k].kind === "text") { lastText = k; break; } }
  return (
    <div className="row" style={{ gap: 8, alignItems: "flex-start" }}>
      <Tile icon="sparkles" color="var(--accent)" size={24} />
      <div className="col gap2" style={{ minWidth: 0, flex: 1 }}>
        {list.map((p, j) => (p.kind === "text" ? (
          <div key={j} style={{ fontSize: 13, lineHeight: "19px", color: "var(--fg-0)", overflowWrap: "anywhere" }}>
            <Markdown>{p.text}</Markdown>
            {streaming && j === lastText && <span style={{ display: "inline-block", width: 6, height: 13, background: "var(--accent)", verticalAlign: "-2px", animation: "blink 1s steps(1) infinite" }} />}
          </div>
        ) : renderComp(p.inst, j)))}
        {streaming && lastText === -1 && <span className="fg-2" style={{ fontSize: 13 }}>…</span>}
      </div>
    </div>
  );
}
