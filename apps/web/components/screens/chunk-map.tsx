"use client";
/* Chunk map: a 2-D (PCA) view of the project's stored chunk vectors so you can SEE how your
   knowledge base is laid out - which chunks cluster, which source each belongs to, and (with a
   query) exactly what retrieval returns and how it connects to the query point. Reuses the same
   React Flow canvas the workflow builder uses. Read-only: it never mutates the store. */
import "@xyflow/react/dist/style.css";
import {
  Background, BackgroundVariant, Controls, Handle, MiniMap, Position, ReactFlow, ReactFlowProvider,
  useEdgesState, useNodesState, useReactFlow, type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { Segmented } from "../primitives";
import { api, ChunkMapResult, ChunkPoint } from "@/lib/api";

// How many chunks to plot. More points = a fuller picture but slower to project (PCA/SVD); the
// backend clamps to a hard ceiling regardless of what's requested here.
const POINT_LIMITS = [200, 400, 800, 1500] as const;

// Distinct, canvas-friendly colors assigned to sources in order. Wraps if a project has more
// sources than colors (fine - the legend still disambiguates).
const PALETTE = ["#6ea8fe", "#f7a072", "#8fd694", "#c792ea", "#ffd166", "#6dd0d6", "#f78fb3", "#a0d995", "#c0a2f0", "#e0b0ff"];
const RETRIEVED = "#ffcc33"; // highlight for the query point + retrieved chunks

const HIDDEN_HANDLE = { opacity: 0, width: 1, height: 1, minWidth: 0, minHeight: 0, border: "none", pointerEvents: "none" as const };

// --- custom nodes (dots on the map) ---

function ChunkDot({ data }: NodeProps) {
  const d = data as any;
  const size = d.retrieved ? 17 : 12;
  const ring = d.retrieved
    ? `0 0 0 2px var(--bg-1), 0 0 0 4px ${RETRIEVED}`
    : d.selected
      ? "0 0 0 2px var(--fg-0)"
      : "0 0 1px rgba(0,0,0,.45)";
  return (
    <div style={{ position: "relative" }} title={d.preview}>
      {/* Hidden handles so the query->hit / parent-group edges have anchor points. */}
      <Handle type="target" id="t" position={Position.Top} style={HIDDEN_HANDLE} isConnectable={false} />
      <Handle type="source" id="s" position={Position.Top} style={HIDDEN_HANDLE} isConnectable={false} />
      <div style={{ width: size, height: size, borderRadius: "50%", background: d.color, border: "1.5px solid var(--bg-1)", boxShadow: ring, cursor: "pointer" }} />
      {d.retrieved && (
        <span className="mono" style={{ position: "absolute", top: -9, right: -9, fontSize: 10, fontWeight: 700, color: "#1a1400", background: RETRIEVED, borderRadius: 8, minWidth: 15, height: 15, lineHeight: "15px", textAlign: "center", padding: "0 3px" }}>{d.retrieved}</span>
      )}
    </div>
  );
}

function QueryMarker({ data }: NodeProps) {
  return (
    <div style={{ position: "relative" }} title={(data as any).label}>
      <Handle type="source" id="s" position={Position.Top} style={HIDDEN_HANDLE} isConnectable={false} />
      <div style={{ width: 18, height: 18, background: RETRIEVED, transform: "rotate(45deg)", border: "2px solid var(--bg-1)", boxShadow: `0 0 10px ${RETRIEVED}` }} />
    </div>
  );
}

const NODE_TYPES = { chunk: ChunkDot, query: QueryMarker };

// --- main ---

export function ChunkMap({ project }: { project: any }) {
  return (
    <ReactFlowProvider>
      <ChunkMapInner project={project} />
    </ReactFlowProvider>
  );
}

function ChunkMapInner({ project }: { project: any }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"vector" | "hybrid">("vector");
  const [rerank, setRerank] = useState(false);
  const [limit, setLimit] = useState<number>(400);
  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState("");
  const [res, setRes] = useState<ChunkMapResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [selId, setSelId] = useState<string | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const { fitView } = useReactFlow();

  // Color per source id (stable within a load, keyed off the legend order).
  const colorOf = useMemo(() => {
    const m = new Map<string, string>();
    (res?.sources || []).forEach((s, i) => m.set(s.id, PALETTE[i % PALETTE.length]));
    return (sid?: string | null) => (sid && m.get(sid)) || "var(--fg-2)";
  }, [res]);

  const load = useCallback(async (query?: string) => {
    if (!project?.id) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await api.chunkMap(project.id, {
        query: query?.trim() || undefined,
        folders: folder ? [folder] : undefined,
        hybrid: mode === "hybrid",
        rerank,
        limit,
      });
      setRes(r);
      setSelId(null);
    } catch (e: any) {
      setErr(e?.message || "Failed to build the chunk map.");
      setRes(null);
    } finally {
      setLoading(false);
    }
  }, [project?.id, folder, mode, rerank, limit]);

  useEffect(() => { if (project?.id) api.listFolders(project.id).then(setFolders).catch(() => {}); }, [project?.id]);
  // Load the full map once on open (no query overlay yet).
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [project?.id]);
  // Changing the point budget re-fetches immediately (keeping any applied query overlay). Skip the
  // first run so this doesn't double-load on mount alongside the effect above.
  const didMountLimit = useRef(false);
  useEffect(() => {
    if (!didMountLimit.current) { didMountLimit.current = true; return; }
    if (project?.id) load(res?.query || undefined);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [limit]);

  // Rebuild React Flow nodes/edges whenever the map result (or selection) changes.
  useEffect(() => {
    if (!res) { setNodes([]); setEdges([]); return; }
    const ns: Node[] = res.points.map((p) => ({
      id: p.id,
      type: "chunk",
      position: { x: p.x, y: p.y },
      draggable: false,
      data: { ...p, color: colorOf(p.source_id), selected: p.id === selId },
    }));
    const es: Edge[] = [];
    // Parent-group "constellation": link a parent's children to the group's first child so you
    // can see which small chunks belong to the same parent window (parent_child mode only).
    const byParent = new Map<string, ChunkPoint[]>();
    for (const p of res.points) {
      if (!p.parent_id) continue;
      let arr = byParent.get(p.parent_id);
      if (!arr) { arr = []; byParent.set(p.parent_id, arr); }
      arr.push(p);
    }
    for (const [pid, kids] of byParent) {
      if (kids.length < 2) continue;
      const hub = kids[0].id;
      for (const k of kids.slice(1)) {
        es.push({ id: `pc-${pid}-${k.id}`, source: hub, target: k.id, sourceHandle: "s", targetHandle: "t", selectable: false, style: { stroke: "var(--line)", strokeWidth: 1, opacity: 0.5 } });
      }
    }
    // Query -> retrieved links (only when a query overlay is present).
    if (res.query_point) {
      ns.push({ id: "__query__", type: "query", position: { x: res.query_point[0], y: res.query_point[1] }, draggable: false, data: { label: `query: ${res.query}` } });
      for (const p of res.points) if (p.retrieved) {
        es.push({ id: `q-${p.id}`, source: "__query__", target: p.id, sourceHandle: "s", targetHandle: "t", animated: true, selectable: false, style: { stroke: RETRIEVED, strokeWidth: 1.5 } });
      }
    }
    setNodes(ns);
    setEdges(es);
    // Frame the new layout after React Flow measures the nodes.
    setTimeout(() => fitView({ padding: 0.15, duration: 250 }).catch?.(() => {}), 60);
  }, [res, selId, colorOf, setNodes, setEdges, fitView]);

  const selected = res?.points.find((p) => p.id === selId) || null;
  const empty = res && res.points.length === 0;

  return (
    <div className="col" style={{ gap: 10 }}>
      {/* controls */}
      <div className="row gap2" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <input className="input" style={{ flex: 1, minWidth: 220 }} placeholder="Overlay a query to see what retrieval returns…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load(q)} />
        {folders.length > 0 && (
          <select className="select" style={{ width: 150 }} value={folder} onChange={(e) => setFolder(e.target.value)}>
            <option value="">All folders</option>
            {folders.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
        <button className="btn btn-primary" onClick={() => load(q)} disabled={loading}><Icon name="search" size={14} />{loading ? "Mapping…" : "Map query"}</button>
        {res?.query && <button className="btn btn-ghost btn-sm" onClick={() => { setQ(""); load(); }}>Clear overlay</button>}
      </div>
      <div className="row gap2" style={{ alignItems: "center", flexWrap: "wrap" }}>
        <Segmented options={[{ value: "vector", label: "Vector" }, { value: "hybrid", label: "Hybrid" }]} value={mode} onChange={(v) => setMode(v as any)} />
        <label className="row gap1" style={{ alignItems: "center", cursor: "pointer", fontSize: 13 }} title="Two-stage cross-encoder rerank for the query overlay.">
          <input type="checkbox" checked={rerank} onChange={(e) => setRerank(e.target.checked)} />Rerank
        </label>
        <label className="row gap1" style={{ alignItems: "center", fontSize: 13 }} title="How many chunks to plot. More points give a fuller picture but take longer to project.">
          Max points
          <select className="select" style={{ width: 84 }} value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            {POINT_LIMITS.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <span className="t-caption fg-2">Dots are chunks placed by semantic similarity (PCA), colored by source. Overlay a query to mark retrieved chunks (◆ = query).</span>
      </div>

      {/* legend + truncation note */}
      {res && res.sources.length > 0 && (
        <div className="row gap2" style={{ alignItems: "center", flexWrap: "wrap" }}>
          {res.sources.map((s, i) => (
            <span key={s.id} className="row gap1 t-caption" style={{ alignItems: "center" }}>
              <span style={{ width: 10, height: 10, borderRadius: "50%", background: PALETTE[i % PALETTE.length], display: "inline-block" }} />
              <span className="truncate" style={{ maxWidth: 160 }}>{s.name}</span>
            </span>
          ))}
          <span className="t-caption fg-2" style={{ marginLeft: "auto" }}>
            {res.truncated ? `showing ${res.points.length} of ${res.total} chunks` : `${res.total} chunks`}
          </span>
        </div>
      )}

      {err && <div className="t-caption" style={{ color: "var(--danger, #c00)" }}>⚠ {err}</div>}

      {/* canvas + detail panel */}
      <div className="card" style={{ position: "relative", height: 560, overflow: "hidden", padding: 0 }}>
        {empty ? (
          <div className="col center" style={{ width: "100%", height: "100%", color: "var(--fg-2)", gap: 6 }}>
            <Icon name="layers" size={22} />
            <div>No chunks yet. Add sources in the Files tab, then map them here.</div>
          </div>
        ) : (
          <ReactFlow
            style={{ width: "100%", height: "100%" }}
            nodes={nodes} edges={edges} nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
            onNodeClick={(_, n) => setSelId(n.id === "__query__" ? null : n.id)} onPaneClick={() => setSelId(null)}
            nodesDraggable={false} nodesConnectable={false} minZoom={0.15}
            fitView proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="var(--canvas-grid)" />
            <Controls showInteractive={false} />
            <MiniMap pannable zoomable style={{ background: "var(--bg-1)" }} nodeColor={(n) => (n.data as any)?.color || RETRIEVED} />
          </ReactFlow>
        )}

        {selected && (
          <div className="card" style={{ position: "absolute", top: 10, right: 10, width: 300, padding: 12, boxShadow: "var(--sh-pop)", zIndex: 20 }}>
            <div className="row spread" style={{ marginBottom: 6 }}>
              <span className="t-micro">Chunk</span>
              <button className="iconbtn" onClick={() => setSelId(null)}><Icon name="x" size={14} /></button>
            </div>
            <div className="row gap2 t-caption fg-2" style={{ marginBottom: 8, flexWrap: "wrap" }}>
              <span className="chip">{res?.sources.find((s) => s.id === selected.source_id)?.name || selected.source_id || "-"}</span>
              {selected.chunk_idx != null && <span className="chip chip-mono">#{selected.chunk_idx}</span>}
              {selected.retrieved && <span className="chip chip-mono" style={{ color: RETRIEVED }}>rank {selected.retrieved}</span>}
              {selected.parent_id && <span className="chip chip-mono" title={selected.parent_id}>parent</span>}
            </div>
            <div className="t-body-sm" style={{ maxHeight: 360, overflow: "auto", whiteSpace: "pre-wrap" }}>{selected.preview}</div>
          </div>
        )}
      </div>
    </div>
  );
}
