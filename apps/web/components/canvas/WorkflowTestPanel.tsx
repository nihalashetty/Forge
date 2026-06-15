"use client";
/* The canvas Test panel: messages a workflow over SSE and lights up nodes as the graph
   executes (one backend thread per session so the checkpointer holds prior turns). */
import { useEffect, useRef, useState } from "react";
import { Icon } from "../icons";
import { Tile } from "../primitives";
import { api, openSSE, type Workflow } from "@/lib/api";
import { fmtUSD } from "@/lib/data";

interface TestMsg { role: "user" | "assistant"; content: string }

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
  const activeRef = useRef(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  // One backend thread per test session — the checkpointer holds prior turns.
  const threadRef = useRef<string | null>(null);
  useEffect(() => { threadRef.current = null; setMsgs([]); }, [workflow?.id]);

  useEffect(() => {
    activeRef.current = true;
    return () => {
      activeRef.current = false;
      onRunningChange(false);
    };
  }, [onRunningChange]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, streaming, meter]);

  async function send() {
    const text = input.trim();
    if (!text || running || !project?.id || !workflow?.id) return;
    setInput("");
    setMsgs((m) => [...m, { role: "user", content: text }]);
    setStreaming("");
    setMeter(null);
    onResetRun();
    onRunningChange(true);
    let buffer = "";
    let finalAnswer = "";
    let lastNode: string | null = null;

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
          buffer += f.data.content;
          setStreaming(buffer);
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
      }
    }
    if (activeRef.current) {
      setMsgs((m) => [...m, { role: "assistant", content: finalAnswer || buffer || "(no output)" }]);
    }
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
        {msgs.length === 0 && !streaming && (
          <div className="col center" style={{ minHeight: 160, textAlign: "center", gap: 8, color: "var(--fg-2)" }}>
            <Tile icon="playground" color="var(--io-json)" size={38} />
            <div className="t-h3" style={{ color: "var(--fg-1)" }}>Message this workflow</div>
            <div className="t-caption">Nodes highlight as the graph executes. Hover the debug chips on nodes for output and cost.</div>
          </div>
        )}
        {msgs.map((m, i) => <TestBubble key={i} role={m.role} content={m.content} />)}
        {(streaming || running) && <TestBubble role="assistant" content={streaming || "…"} streaming />}
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
          <button className="btn btn-primary btn-sm" onClick={send} disabled={running || !input.trim()}>
            <Icon name={running ? "refresh" : "play"} size={13} style={running ? { animation: "spin 1s linear infinite" } : {}} />
            {running ? "Testing" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TestBubble({ role, content, streaming }: { role: "user" | "assistant"; content: string; streaming?: boolean }) {
  const user = role === "user";
  return (
    <div className="row" style={{ gap: 8, alignItems: "flex-start", flexDirection: user ? "row-reverse" : "row" }}>
      <Tile icon={user ? "user" : "sparkles"} color={user ? "var(--signal)" : "var(--accent)"} size={24} />
      <div style={{ maxWidth: 260, minWidth: 0, padding: "8px 10px", borderRadius: 10, fontSize: 13, lineHeight: "19px", whiteSpace: "pre-wrap", overflowWrap: "anywhere", background: user ? "var(--accent)" : "var(--bg-3)", color: user ? "var(--fg-on-accent)" : "var(--fg-0)", borderTopRightRadius: user ? 3 : 10, borderTopLeftRadius: user ? 10 : 3 }}>
        {content}
        {streaming && <span style={{ display: "inline-block", width: 6, height: 13, background: "var(--accent)", marginLeft: 2, verticalAlign: "-2px", animation: "blink 1s steps(1) infinite" }} />}
      </div>
    </div>
  );
}
