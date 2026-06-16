"use client";
/* Self-rendered canvas connections.

   React Flow v12's own edge renderer doesn't draw loaded edges reliably in this dev
   setup (handle-bounds race), so we draw the wires ourselves inside the ViewportPortal —
   which lives in flow coordinate space, so it pans/zooms with the canvas. Native edges
   are hidden via CSS to avoid double-draw. New connections (onConnect) land in the same
   `edges` array, so hand-drawn wires show here too. */
import { ViewportPortal } from "@xyflow/react";
import { useMemo } from "react";
import { Icon } from "../icons";
import { type FlowEdge, type FlowNode } from "@/lib/graph";

const NODE_HEIGHT: Record<string, number> = {
  start: 36, end: 36,
  agent: 90, deep_agent: 90, llm: 80, human_input: 80, classifier: 80,
  transform: 64, tool_call: 64, webhook_out: 64, emit_event: 64, retrieval: 64,
};
const NODE_WIDTH = 230;
// Router case-row geometry — must mirror ForgeNode's router body (header + expr line + rows).
const ROUTER_GEOM = { header: 37, exprLine: 22, rowH: 24, pad: 6 };

function routerHeight(cfg: any): number {
  const rows = Object.keys(cfg?.cases || {}).length + 1; // + Else
  return ROUTER_GEOM.header + ROUTER_GEOM.exprLine + rows * ROUTER_GEOM.rowH + ROUTER_GEOM.pad;
}

/** Y offset (within the router node) of the case row an edge leaves from. */
function routerCaseY(cfg: any, edge: FlowEdge): number {
  const keys = [...Object.keys(cfg?.cases || {}), "__default__"];
  let key: string | undefined = edge.sourceHandle?.startsWith("case:") ? edge.sourceHandle.slice(5) : undefined;
  if (!key || !keys.includes(key)) {
    // Infer from routing config: which case (or default) points at this edge's target?
    key = Object.entries(cfg?.cases || {}).find(([, tgt]) => tgt === edge.target)?.[0]
      ?? (cfg?.default === edge.target ? "__default__" : undefined);
  }
  const idx = key ? keys.indexOf(key) : 0;
  return ROUTER_GEOM.header + ROUTER_GEOM.exprLine + (idx < 0 ? 0 : idx) * ROUTER_GEOM.rowH + ROUTER_GEOM.rowH / 2;
}

export function EdgeOverlay({ nodes, edges, onRemove }: { nodes: FlowNode[]; edges: FlowEdge[]; onRemove: (edge: FlowEdge) => void }) {
  const byId = useMemo(() => Object.fromEntries(nodes.map((n) => [n.id, n])), [nodes]);
  const h = (n: any) => (n?.data?.nodeType === "router" ? routerHeight(n?.data?.config) : NODE_HEIGHT[n?.data?.nodeType] ?? 36);
  const edgeGeometry = (e: FlowEdge) => {
    const s = byId[e.source]; const t = byId[e.target];
    if (!s || !t) return null;
    const fromRouter = s.data?.nodeType === "router";
    const sx = s.position.x + NODE_WIDTH;
    const sy = s.position.y + (fromRouter ? routerCaseY(s.data?.config, e) : h(s) / 2);
    const tx = t.position.x, ty = t.position.y + h(t) / 2;
    const dx = Math.max(40, Math.abs(tx - sx) / 2);
    const d = `M ${sx},${sy} C ${sx + dx},${sy} ${tx - dx},${ty} ${tx},${ty}`;
    return { d, mx: (sx + tx) / 2, my: (sy + ty) / 2 };
  };
  return (
    <ViewportPortal>
      <svg style={{ position: "absolute", left: 0, top: 0, width: 1, height: 1, overflow: "visible", pointerEvents: "none", zIndex: -1 }}>
        <defs>
          <marker id="forge-arrow" markerWidth="9" markerHeight="9" refX="7" refY="3" orient="auto">
            <path d="M0,0 L7,3 L0,6 Z" fill="var(--line-strong)" />
          </marker>
        </defs>
        {edges.map((e) => {
          const geo = edgeGeometry(e);
          if (!geo) return null;
          return <path key={e.id} d={geo.d} fill="none" stroke="var(--line-strong)" strokeWidth={2} markerEnd="url(#forge-arrow)" />;
        })}
      </svg>
      {edges.map((e) => {
        const geo = edgeGeometry(e);
        if (!geo) return null;
        return (
          <button
            key={`${e.id}-detach`}
            title="Detach edge"
            aria-label={`Detach edge from ${e.source} to ${e.target}`}
            onPointerDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => { ev.stopPropagation(); onRemove(e); }}
            style={{
              position: "absolute", left: geo.mx - 9, top: geo.my - 9,
              width: 18, height: 18, borderRadius: 999,
              border: "1px solid var(--line-strong)", background: "var(--bg-1)",
              color: "var(--fg-2)", boxShadow: "var(--sh-1)",
              display: "flex", alignItems: "center", justifyContent: "center",
              padding: 0, cursor: "pointer", pointerEvents: "auto", zIndex: 8,
            }}
          >
            <Icon name="x" size={10} />
          </button>
        );
      })}
    </ViewportPortal>
  );
}
