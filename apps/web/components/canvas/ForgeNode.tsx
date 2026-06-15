"use client";
/* Forge canvas node — a chip on a circuit board, with IOType-colored typed handles.
   Ports come from the backend Node Type Registry (/v1/node-types) via context. */
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { createContext, useContext, useEffect, useState } from "react";
import { Icon } from "../icons";
import { NODE_META, IO_COLOR, fmtUSD } from "@/lib/data";
import type { NodeType } from "@/lib/api";

export const NodeTypesContext = createContext<Record<string, NodeType>>({});

const CAT_COLOR: Record<string, string> = {
  control: "var(--io-control)", agent: "var(--accent)", json: "var(--io-json)",
  vector: "var(--io-vector)", human: "var(--warn)", signal: "var(--signal)",
};

function summarize(type: string, c: Record<string, any>): string[] {
  switch (type) {
    case "agent":
    case "deep_agent": {
      const tools = (c.tools || []).length;
      const mw = (c.middleware || []).filter((m: any) => m.enabled !== false).length;
      return [String(c.model || "—"), `${tools} tool${tools === 1 ? "" : "s"} · ${mw} middleware`];
    }
    case "router":
      return [`expr · ${c.expression || "—"}`, Object.keys(c.cases || {}).concat(c.default ? ["default"] : []).join(" · ")];
    case "llm":
      return [String(c.model || "—"), "single call"];
    case "classifier":
      return [`→ ${c.output_key || "intent"}`, (c.labels || []).slice(0, 4).join(" · ") || "no labels"];
    case "transform":
      return [`${c.engine || "jmespath"} → ${c.output_key || "data"}`];
    case "tool_call":
      return [String(c.tool_id || "—")];
    case "human_input":
      return [(c.prompt || "").slice(0, 34), (c.allowed_decisions || ["approve", "reject"]).join(" · ")];
    case "webhook_out":
      return [`${c.method || "POST"} ${String(c.url || "").slice(0, 26)}`];
    case "emit_event":
      return [`channel · ${c.channel || ""}`];
    default:
      return [];
  }
}

function debugPreview(value: any): string {
  if (value == null || value === "") return "";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function HandleStack({ ports, dir }: { ports: { id: string; io_type: string }[]; dir: "in" | "out" }) {
  const n = ports.length;
  return (
    <>
      {ports.map((p, i) => (
        <Handle
          key={p.id}
          // With a single port per side (every Forge node today), omit the id so this is
          // React Flow's *default* handle — then loaded edges (which carry no handle id)
          // always attach, with no id-matching that can silently drop the edge.
          id={n > 1 ? p.id : undefined}
          type={dir === "in" ? "target" : "source"}
          position={dir === "in" ? Position.Left : Position.Right}
          style={{
            top: `${((i + 1) / (n + 1)) * 100}%`,
            width: 11, height: 11, border: "2px solid var(--bg-1)",
            background: IO_COLOR[p.io_type] || "var(--io-any)",
          }}
        />
      ))}
    </>
  );
}

export function ForgeNode({ id, data, selected }: NodeProps) {
  const registry = useContext(NodeTypesContext);
  const type = (data as any).nodeType as string;
  const config = (data as any).config || {};
  const status = (data as any).status as string | undefined;
  const debug = (data as any).debug || {};
  const [showDebug, setShowDebug] = useState(false);
  const meta = NODE_META[type] || { icon: "n_agent", color: "var(--fg-2)", label: type };
  const spec = registry[type];
  const inPorts = spec?.input_ports || [];
  const outPorts = spec?.output_ports || [];
  const lines = summarize(type, config);
  const title = config.name || meta.label || type;
  const hasDebug =
    debug.output !== undefined ||
    Number(debug.cost_usd || 0) > 0 ||
    Number(debug.tokens || 0) > 0;
  const outputPreview = debugPreview(debug.output);

  // The port registry loads after the node first mounts, so the handles appear later.
  // Tell React Flow to re-measure this node's handle bounds when its port count changes —
  // without this, edges that connect to those handles never get drawn. Router case rows
  // each carry a handle, so case-count changes also need a re-measure.
  const caseKeys = type === "router" ? Object.keys(config.cases || {}).join("|") : "";
  const updateNodeInternals = useUpdateNodeInternals();
  const portKey = `${inPorts.length}:${outPorts.length}:${caseKeys}`;
  useEffect(() => { updateNodeInternals(id); }, [id, portKey, updateNodeInternals]);

  const border =
    status === "running" ? "0 0 0 2px var(--ok), 0 0 0 6px var(--ok-bg)" :
    status === "done" ? "0 0 0 1px var(--ok)" :
    status === "error" ? "0 0 0 1px var(--err)" :
    selected ? "var(--glow-accent)" : "0 0 0 1px var(--line)";

  return (
    <div
      style={{
        width: 230, background: "var(--bg-2)", borderRadius: "var(--r-lg)",
        boxShadow: `${border}, var(--node-shadow)`, position: "relative",
      }}
    >
      <HandleStack ports={inPorts} dir="in" />
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", borderBottom: "1px solid var(--line)", background: "var(--bg-3)", borderRadius: "var(--r-lg) var(--r-lg) 0 0", borderLeft: `3px solid ${meta.color}` }}>
        <Icon name={meta.icon} size={16} style={{ color: meta.color, flexShrink: 0 }} />
        <span className="t-h3 truncate" style={{ flex: 1, fontFamily: "var(--font-display)" }}>{title}</span>
        <span className="typechip">{type}</span>
      </div>
      {/* body */}
      {type === "router" ? (
        // OpenAI-style if/else: one labeled output row + connector per case, plus Else.
        // Dragging from a row's handle wires that case (see onConnect in workflows.tsx).
        // Geometry must stay in sync with ROUTER_GEOM in workflows.tsx (EdgeOverlay).
        <div className="col" style={{ paddingBottom: 6 }}>
          <div className="t-caption fg-2 truncate" style={{ padding: "4px 11px", height: 22 }}>expr · {config.expression || "—"}</div>
          {[...Object.keys(config.cases || {}), "__default__"].map((k) => (
            <div key={k} style={{ position: "relative", height: 24, display: "flex", alignItems: "center", justifyContent: "flex-end", padding: "0 12px", borderTop: "1px solid var(--line)", background: "var(--bg-3)" }}>
              <span className="mono-sm truncate" style={{ color: k === "__default__" ? "var(--fg-2)" : "var(--fg-1)" }}>{k === "__default__" ? "Else" : k}</span>
              <Handle
                id={`case:${k}`} type="source" position={Position.Right}
                style={{ top: "50%", right: -6, transform: "translateY(-50%)", width: 10, height: 10, border: "2px solid var(--bg-1)", background: k === "__default__" ? "var(--fg-2)" : "var(--io-control)", position: "absolute" }}
              />
            </div>
          ))}
        </div>
      ) : lines.length > 0 && (
        <div className="col" style={{ padding: "9px 11px", gap: 3 }}>
          {lines.map((l, i) => (
            <div key={i} className={i === 0 ? "mono-sm truncate" : "t-caption fg-2 truncate"} style={{ color: i === 0 ? "var(--fg-1)" : undefined }}>{l}</div>
          ))}
        </div>
      )}
      {type !== "router" && <HandleStack ports={outPorts} dir="out" />}
      {hasDebug && (
        <div
          onMouseEnter={() => setShowDebug(true)}
          onMouseLeave={() => setShowDebug(false)}
          style={{ position: "absolute", right: 8, bottom: -13, zIndex: 25 }}
        >
          <span
            className="chip chip-mono"
            style={{
              height: 24,
              borderColor: "var(--accent)",
              background: "var(--bg-1)",
              boxShadow: "var(--sh-1)",
              color: "var(--fg-0)",
            }}
          >
            <Icon name={Number(debug.cost_usd || 0) > 0 ? "bolt" : "eye"} size={11} />
            {Number(debug.cost_usd || 0) > 0 ? fmtUSD(Number(debug.cost_usd || 0)) : "output"}
          </span>
          {showDebug && (
            <div
              className="card"
              style={{
                position: "absolute",
                right: 0,
                top: 28,
                width: 290,
                padding: 11,
                boxShadow: "var(--sh-pop)",
                zIndex: 80,
              }}
            >
              <div className="row spread" style={{ marginBottom: 8 }}>
                <div className="t-micro">Node output</div>
                {(Number(debug.tokens || 0) > 0 || Number(debug.cost_usd || 0) > 0) && (
                  <span className="chip chip-mono">
                    <Icon name="bolt" size={11} />
                    {Number(debug.tokens || 0)} tok · {fmtUSD(Number(debug.cost_usd || 0))}
                  </span>
                )}
              </div>
              <pre
                className="mono-sm"
                style={{
                  margin: 0,
                  maxHeight: 150,
                  overflow: "auto",
                  whiteSpace: "pre-wrap",
                  overflowWrap: "anywhere",
                  color: "var(--fg-1)",
                  background: "var(--bg-3)",
                  border: "1px solid var(--line)",
                  borderRadius: 7,
                  padding: 8,
                }}
              >
                {(outputPreview || "(no state delta)").slice(0, 1400)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
