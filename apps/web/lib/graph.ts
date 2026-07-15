/* Canvas (React Flow) <-> executable JSON translation + IOType rules.
   Canvas JSON is React-Flow-shaped (UI owns it); executable is the compiler input. */
import type { Edge, Node } from "@xyflow/react";

export interface ForgeNodeData {
  nodeType: string;
  config: Record<string, any>;
  status?: "idle" | "running" | "done" | "error";
  [k: string]: any;
}
export type FlowNode = Node<ForgeNodeData>;
export type FlowEdge = Edge;

export const DEFAULT_STATE: Record<string, any> = {
  messages: { type: "list[message]", reducer: "add_messages" },
  intent: { type: "str", reducer: "last" },
};

/** `any` matches all; `control` only connects to `control`; else exact match. */
export function ioCompatible(a: string, b: string): boolean {
  if (a === "control" || b === "control") return a === "control" && b === "control";
  if (a === "any" || b === "any") return true;
  return a === b;
}

export function newNodeId(type: string, existing: Iterable<string>): string {
  const ids = new Set(existing);
  let i = 1;
  while (ids.has(`${type}_${i}`)) i++;
  return `${type}_${i}`;
}

/** Ensure a node's config carries schema-required fields the UI only defaults visually,
 *  so the saved executable always validates. Agent/deep_agent need an explicit `flavor`
 *  derived from the node type - a deep_agent must never silently compile as a plain agent. */
export function normalizeNodeConfig(nodeType: string, config: Record<string, any>): Record<string, any> {
  if (nodeType === "agent" || nodeType === "deep_agent") {
    return { ...config, flavor: config.flavor || nodeType };
  }
  return config;
}

const ROUTER_CASE_HANDLE = "case:";

function routerCaseFromEdge(node: FlowNode | undefined, edge: FlowEdge): string | undefined {
  const rawHandle = edge.sourceHandle ?? (edge as any).source_handle ?? null;
  if (typeof rawHandle === "string" && rawHandle.startsWith(ROUTER_CASE_HANDLE)) {
    return rawHandle.slice(ROUTER_CASE_HANDLE.length);
  }
  if (!node || node.data.nodeType !== "router") return undefined;

  const cfg = node.data.config || {};
  const label = (edge as any).label;
  if (label != null && Object.prototype.hasOwnProperty.call(cfg.cases || {}, String(label))) {
    return String(label);
  }

  const caseMatch = Object.entries(cfg.cases || {}).find(([, target]) => target === edge.target);
  if (caseMatch) return caseMatch[0];
  if (cfg.default === edge.target) return "__default__";
  return undefined;
}

function normalizeRouterConfigFromEdges(node: FlowNode, edges: FlowEdge[]): Record<string, any> {
  const cfg = { ...(node.data.config || {}) };
  if (node.data.nodeType !== "router") return cfg;

  const cases: Record<string, string> = { ...(cfg.cases || {}) };
  for (const edge of edges) {
    if (edge.source !== node.id) continue;
    const key = routerCaseFromEdge(node, edge);
    if (!key) continue;
    if (key === "__default__") cfg.default = edge.target;
    else if (Object.prototype.hasOwnProperty.call(cases, key)) cases[key] = edge.target;
  }
  return { ...cfg, cases };
}

/** State keys each node type writes (from its config), so the workflow state can declare
 *  them automatically - LangGraph rejects writes to undeclared keys, which would silently
 *  break any router branching on a classifier label, qa/retrieval route flag, or human
 *  decision in a canvas-built workflow. */
function nodeWrittenKeys(nodeType: string, c: Record<string, any>): [string, string][] {
  switch (nodeType) {
    case "classifier": return [[c.output_key || "intent", c.multi_label ? "list[str]" : "str"]];
    case "retrieval": return c.route_key ? [[c.route_key, "str"]] : [];
    case "human_input": return c.output_key ? [[c.output_key, "str"]] : [];
    case "transform": return [[c.output_key || "data", "json"]];
    case "tool_call": return c.output_key ? [[c.output_key, "json"]] : [];
    case "webhook_out": return [[c.output_key || "webhook_result", "json"]];
    default: return [];
  }
}

export function canvasToExecutable(
  nodes: FlowNode[],
  edges: FlowEdge[],
  meta: { id: string; version?: number; state?: Record<string, any> },
): Record<string, any> {
  // Entry: a Start marker, else a trigger node (webhook/schedule/email/app_event),
  // else a node with no incoming edge, else the first node.
  const TRIGGERS = new Set(["webhook_in", "schedule", "email_in", "app_event"]);
  const hasIncoming = new Set(edges.map((e) => e.target));
  const start =
    nodes.find((n) => n.data.nodeType === "start") ||
    nodes.find((n) => TRIGGERS.has(n.data.nodeType)) ||
    nodes.find((n) => !hasIncoming.has(n.id));
  const state: Record<string, any> = { ...(meta.state || DEFAULT_STATE) };
  for (const n of nodes) {
    for (const [key, type] of nodeWrittenKeys(n.data.nodeType, n.data.config || {})) {
      if (key && !state[key]) state[key] = { type, reducer: "last" };
    }
  }
  return {
    id: meta.id,
    version: meta.version || 1,
    state,
    entry_node: start?.id || nodes[0]?.id || "start",
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.data.nodeType,
      config: normalizeNodeConfig(n.data.nodeType, normalizeRouterConfigFromEdges(n, edges)),
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e) => ({
      source: e.source,
      target: e.target,
      source_handle: e.sourceHandle || undefined,
      target_handle: e.targetHandle || undefined,
    })),
  };
}

export function canvasToFlow(canvas: any): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = (canvas?.nodes || []).map((n: any) => ({
    id: n.id,
    type: "forge",
    position: n.position || { x: 0, y: 0 },
    data: { nodeType: n.data?.nodeType || n.type, config: n.data?.config || {} },
  }));
  const byId: Record<string, FlowNode> = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // Forge nodes use React Flow's default handle (one in/out per node), so edges carry no
  // handle id - they attach to the default handle. Any stored handle id is still honored.
  const edges: FlowEdge[] = (canvas?.edges || []).map((e: any, i: number) => ({
    id: e.id || `e${i}`,
    source: e.source,
    target: e.target,
    sourceHandle: e.sourceHandle ?? e.source_handle ?? (
      routerCaseFromEdge(byId[e.source], e as FlowEdge)
        ? `${ROUTER_CASE_HANDLE}${routerCaseFromEdge(byId[e.source], e as FlowEdge)}`
        : null
    ),
    targetHandle: e.targetHandle ?? e.target_handle ?? null,
    label: e.label,
  }));
  return { nodes, edges };
}

/** A minimal runnable starter: start -> end. */
export function starterWorkflow(): { canvas: any; nodes: FlowNode[]; edges: FlowEdge[] } {
  const nodes: FlowNode[] = [
    { id: "start", type: "forge", position: { x: 80, y: 220 }, data: { nodeType: "start", config: {} } },
    { id: "end", type: "forge", position: { x: 520, y: 220 }, data: { nodeType: "end", config: {} } },
  ];
  const edges: FlowEdge[] = [{ id: "e0", source: "start", target: "end" }];
  return { canvas: { nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } }, nodes, edges };
}

const GROUNDING_PROMPT =
  "You are the support assistant for this project. Be friendly, natural, and concise. " +
  "For greetings, thanks, or small talk (e.g. 'hi', 'thanks'), reply naturally and briefly and invite " +
  "the user's question - do NOT refuse these. For questions about this project/product, answer using ONLY " +
  "the KNOWLEDGE BASE context provided in the conversation (documents and FAQs); if it doesn't contain the " +
  "answer, say you don't have that information and offer to connect them with a human. Never invent facts " +
  "or use outside knowledge for such questions. Use the prior conversation turns for context.";

/** A complete grounded support flow: start -> retrieval (RAG over KB + Q&A) -> agent -> end. */
export function groundedWorkflow(model = "openai:gpt-4o-mini"): { canvas: any; executable: Record<string, any> } {
  const nodes: FlowNode[] = [
    { id: "start", type: "forge", position: { x: 60, y: 180 }, data: { nodeType: "start", config: {} } },
    { id: "retrieval_1", type: "forge", position: { x: 300, y: 180 }, data: { nodeType: "retrieval", config: { top_k: 4, include_qa: true, announce_empty: true, min_score: 0.18 } } },
    { id: "agent_1", type: "forge", position: { x: 560, y: 180 }, data: { nodeType: "agent", config: { flavor: "agent", name: "support_agent", model, system_prompt: GROUNDING_PROMPT, tools: [], middleware: [] } } },
    { id: "end", type: "forge", position: { x: 820, y: 180 }, data: { nodeType: "end", config: {} } },
  ];
  const edges: FlowEdge[] = [
    { id: "e0", source: "start", target: "retrieval_1" },
    { id: "e1", source: "retrieval_1", target: "agent_1" },
    { id: "e2", source: "agent_1", target: "end" },
  ];
  return { canvas: { nodes, edges, viewport: { x: 0, y: 0, zoom: 1 } }, executable: canvasToExecutable(nodes, edges, { id: "grounded_support" }) };
}
