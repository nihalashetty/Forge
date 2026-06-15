/* Forge — Workflow Canvas (the hero). Palette · pannable graph · inspector · minimap · live-run. */

function WorkflowCanvas({ project, onOpenInspectorScreen }) {
  const { WorkflowNode, portPos, edgePath, nodeHeight, NODE_W } = window.WorkflowNodeKit;
  const [nodes, setNodes] = useState(() => DATA.workflowNodes.map(n => ({ ...n })));
  const edges = DATA.workflowEdges;
  const [sel, setSel] = useState('billing_agent');
  const [pan, setPan] = useState({ x: 40, y: -20 });
  const [zoom, setZoom] = useState(0.82);
  const [run, setRun] = useState({ on: false, idx: -1, doneSet: new Set(), pulse: null });
  const [paletteQ, setPaletteQ] = useState('');
  const [inspTab, setInspTab] = useState('config');
  const surfRef = useRef(null);
  const drag = useRef(null);
  const panning = useRef(null);
  const runTimer = useRef(null);

  const selNode = nodes.find(n => n.id === sel);

  /* ---- pan & zoom ---- */
  const onSurfaceDown = (e) => {
    if (e.target.closest('[data-node]') || e.target.closest('[data-nopan]')) return;
    panning.current = { sx: e.clientX, sy: e.clientY, px: pan.x, py: pan.y };
    setSel(null);
  };
  useEffect(() => {
    const move = (e) => {
      if (panning.current) {
        setPan({ x: panning.current.px + (e.clientX - panning.current.sx), y: panning.current.py + (e.clientY - panning.current.sy) });
      } else if (drag.current) {
        const { id, ox, oy } = drag.current;
        const gx = (e.clientX - drag.current.rect.left - pan.x) / zoom - ox;
        const gy = (e.clientY - drag.current.rect.top - pan.y) / zoom - oy;
        setNodes(ns => ns.map(n => n.id === id ? { ...n, position: { x: Math.round(gx), y: Math.round(gy) } } : n));
      }
    };
    const up = () => { panning.current = null; drag.current = null; };
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
    return () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
  }, [pan, zoom]);

  const onNodeDragStart = (e, node) => {
    e.stopPropagation();
    const rect = surfRef.current.getBoundingClientRect();
    const gx = (e.clientX - rect.left - pan.x) / zoom;
    const gy = (e.clientY - rect.top - pan.y) / zoom;
    drag.current = { id: node.id, ox: gx - node.position.x, oy: gy - node.position.y, rect };
    setSel(node.id);
  };
  const onWheel = (e) => {
    e.preventDefault();
    if (e.ctrlKey || e.metaKey || Math.abs(e.deltaY) > 0) {
      const rect = surfRef.current.getBoundingClientRect();
      const mx = e.clientX - rect.left, my = e.clientY - rect.top;
      const factor = e.deltaY < 0 ? 1.08 : 0.93;
      const nz = Math.min(1.8, Math.max(0.35, zoom * factor));
      const k = nz / zoom;
      setPan(p => ({ x: mx - (mx - p.x) * k, y: my - (my - p.y) * k }));
      setZoom(nz);
    }
  };

  /* ---- live run ---- */
  const order = DATA.runOrder;
  const startRun = () => {
    if (run.on) { stopRun(); return; }
    setRun({ on: true, idx: -1, doneSet: new Set(), pulse: null });
    let i = 0;
    const tick = () => {
      if (i >= order.length) {
        setRun(r => ({ ...r, on: false, idx: -1, pulse: null }));
        return;
      }
      const cur = order[i];
      setRun(r => {
        const ds = new Set(r.doneSet);
        if (r.idx >= 0) ds.add(order[r.idx]);
        const edge = edges.find(e => e.target === cur && (r.idx < 0 || e.source === order[r.idx]));
        return { on: true, idx: i, doneSet: ds, pulse: edge ? edge.id : null };
      });
      i++;
      runTimer.current = setTimeout(tick, cur === 'billing_agent' ? 1500 : 850);
    };
    runTimer.current = setTimeout(tick, 400);
  };
  const stopRun = () => { clearTimeout(runTimer.current); setRun({ on: false, idx: -1, doneSet: new Set(), pulse: null }); };
  useEffect(() => () => clearTimeout(runTimer.current), []);

  const activeId = run.idx >= 0 ? order[run.idx] : null;

  /* ---- minimap bounds ---- */
  const bounds = useMemo(() => {
    let minX = 1e9, minY = 1e9, maxX = -1e9, maxY = -1e9;
    nodes.forEach(n => { const h = nodeHeight(n); const w = (n.type === 'start' || n.type === 'end') ? 44 : NODE_W;
      minX = Math.min(minX, n.position.x); minY = Math.min(minY, n.position.y);
      maxX = Math.max(maxX, n.position.x + w); maxY = Math.max(maxY, n.position.y + h); });
    return { minX: minX - 40, minY: minY - 40, maxX: maxX + 40, maxY: maxY + 40 };
  }, [nodes]);

  const fitView = () => {
    const rect = surfRef.current.getBoundingClientRect();
    const bw = bounds.maxX - bounds.minX, bh = bounds.maxY - bounds.minY;
    const z = Math.min((rect.width - 80) / bw, (rect.height - 80) / bh, 1.4);
    setZoom(z);
    setPan({ x: (rect.width - bw * z) / 2 - bounds.minX * z, y: (rect.height - bh * z) / 2 - bounds.minY * z });
  };

  // Auto-fit the graph into the canvas viewport once on mount.
  useEffect(() => { const t = setTimeout(() => { if (surfRef.current) fitView(); }, 60); return () => clearTimeout(t); }, []);

  return React.createElement('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
    /* ===== PALETTE ===== */
    React.createElement('div', { style: { width: 196, flex: 'none', borderRight: '1px solid var(--line)', background: 'var(--bg-1)', display: 'flex', flexDirection: 'column' } },
      React.createElement('div', { style: { padding: 10, borderBottom: '1px solid var(--line)' } },
        React.createElement('div', { className: 'row gap2', style: { background: 'var(--bg-3)', borderRadius: 7, padding: '0 8px', height: 30 } },
          React.createElement(Icon, { name: 'search', size: 14, style: { color: 'var(--fg-2)' } }),
          React.createElement('input', { value: paletteQ, onChange: e => setPaletteQ(e.target.value), placeholder: 'Add node…', style: { flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 12.5, color: 'var(--fg-0)', fontFamily: 'var(--font-ui)' } }))),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 8 } },
        DATA.NODE_CATALOG.map(g => {
          const items = g.items.filter(it => it.label.toLowerCase().includes(paletteQ.toLowerCase()));
          if (!items.length) return null;
          return React.createElement('div', { key: g.group, style: { marginBottom: 12 } },
            React.createElement('div', { className: 't-micro', style: { padding: '2px 6px 6px' } }, g.group),
            React.createElement('div', { className: 'col gap1' }, items.map(it =>
              React.createElement('div', { key: it.type, 'data-nopan': true, draggable: false,
                onClick: () => {
                  const id = it.type + '_' + Math.random().toString(36).slice(2, 5);
                  const cx = (surfRef.current.getBoundingClientRect().width / 2 - pan.x) / zoom;
                  const cy = (surfRef.current.getBoundingClientRect().height / 2 - pan.y) / zoom;
                  setNodes(ns => [...ns, { id, type: it.type, position: { x: Math.round(cx - 90), y: Math.round(cy - 30) }, data: {}, summary: ['unconfigured'] }]);
                  setSel(id);
                },
                className: 'row gap2', style: { padding: '6px 7px', borderRadius: 7, cursor: 'grab', border: '1px solid transparent' },
                onMouseEnter: e => { e.currentTarget.style.background = 'var(--bg-3)'; e.currentTarget.style.borderColor = 'var(--line)'; },
                onMouseLeave: e => { e.currentTarget.style.background = 'none'; e.currentTarget.style.borderColor = 'transparent'; } },
                React.createElement('div', { style: { width: 24, height: 24, borderRadius: 6, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: `color-mix(in srgb, ${g.color} 14%, var(--bg-1))`, color: g.color } },
                  React.createElement(Icon, { name: it.icon, size: 14 })),
                React.createElement('div', { style: { minWidth: 0 } },
                  React.createElement('div', { style: { fontSize: 12, fontWeight: 600 } }, it.label),
                  React.createElement('div', { className: 'truncate', style: { fontSize: 10, color: 'var(--fg-2)' } }, it.desc))))));
        }))),

    /* ===== CANVAS ===== */
    React.createElement('div', { className: 'grow', style: { position: 'relative', overflow: 'hidden', background: 'var(--canvas-bg)' } },
      /* toolbar */
      React.createElement('div', { 'data-nopan': true, style: { position: 'absolute', top: 12, left: 12, right: 12, zIndex: 30, display: 'flex', alignItems: 'center', gap: 8, pointerEvents: 'none' } },
        React.createElement('div', { className: 'card row gap1', style: { padding: 4, pointerEvents: 'auto', boxShadow: 'var(--sh-1)' } },
          React.createElement('button', { className: 'iconbtn', title: 'Undo' }, React.createElement(Icon, { name: 'undo', size: 16 })),
          React.createElement('button', { className: 'iconbtn', title: 'Redo' }, React.createElement(Icon, { name: 'redo', size: 16 })),
          React.createElement('div', { className: 'vdivider', style: { margin: '4px 2px' } }),
          React.createElement('button', { className: 'iconbtn', title: 'Auto-layout', onClick: fitView }, React.createElement(Icon, { name: 'tidy', size: 16 })),
          React.createElement('button', { className: 'iconbtn', title: 'Validate' }, React.createElement(Icon, { name: 'validate', size: 16 }))),
        React.createElement('div', { className: 'grow' }),
        React.createElement('div', { className: 'card row gap2', style: { padding: '5px 7px 5px 10px', pointerEvents: 'auto', boxShadow: 'var(--sh-1)' } },
          React.createElement('span', { className: 'pill pill-muted' }, React.createElement('span', { className: 'dot' }), 'Draft v8'),
          React.createElement('span', { className: 'mono-sm', style: { color: 'var(--fg-2)' } }, nodes.length + ' nodes'),
          React.createElement('button', { className: 'btn btn-secondary btn-sm' }, React.createElement(Icon, { name: 'save', size: 14 }), 'Save'),
          React.createElement('button', { className: 'btn btn-primary btn-sm', onClick: startRun },
            React.createElement(Icon, { name: run.on ? 'stop' : 'play', size: 14 }), run.on ? 'Stop' : 'Test run')),
        ),
      /* run banner */
      run.on && React.createElement('div', { 'data-nopan': true, style: { position: 'absolute', top: 56, left: '50%', transform: 'translateX(-50%)', zIndex: 30, pointerEvents: 'none' } },
        React.createElement('div', { className: 'card row gap2 fade-in', style: { padding: '6px 12px', boxShadow: 'var(--sh-2)', borderColor: 'var(--signal)' } },
          React.createElement('div', { style: { width: 8, height: 8, borderRadius: '50%', background: 'var(--signal)', boxShadow: '0 0 8px var(--signal)', animation: 'pulse 1s infinite' } }),
          React.createElement('span', { style: { fontSize: 12.5, fontWeight: 600 } }, 'Live run · ', React.createElement('span', { className: 'mono-sm', style: { color: 'var(--signal)' } }, activeId || 'starting…')))),

      /* surface */
      React.createElement('div', { ref: surfRef, onMouseDown: onSurfaceDown, onWheel,
        style: { position: 'absolute', inset: 0, cursor: panning.current ? 'grabbing' : 'default',
          backgroundImage: 'radial-gradient(var(--canvas-grid) 1px, transparent 1px)', backgroundSize: 22 * zoom + 'px ' + 22 * zoom + 'px', backgroundPosition: pan.x + 'px ' + pan.y + 'px' } },
        React.createElement('div', { style: { position: 'absolute', transformOrigin: '0 0', transform: `translate(${pan.x}px,${pan.y}px) scale(${zoom})` } },
          /* edges */
          React.createElement('svg', { style: { position: 'absolute', overflow: 'visible', left: 0, top: 0, pointerEvents: 'none' }, width: 1, height: 1 },
            React.createElement('defs', null,
              React.createElement('marker', { id: 'arrow', viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' },
                React.createElement('path', { d: 'M0 0L9 5L0 10z', fill: 'var(--line-strong)' }))),
            edges.map(e => {
              const s = nodes.find(n => n.id === e.source), t = nodes.find(n => n.id === e.target);
              if (!s || !t) return null;
              const a = portPos(s, 'out'), b = portPos(t, 'in');
              const io = DATA.IO_COLOR[e.io] || 'var(--io-control)';
              const isActive = run.pulse === e.id;
              const doneEdge = run.doneSet.has(e.source) && run.doneSet.has(e.target);
              const d = edgePath(a, b);
              return React.createElement('g', { key: e.id },
                React.createElement('path', { d, fill: 'none', stroke: isActive ? io : doneEdge ? io : 'var(--line-strong)', strokeWidth: isActive ? 2.5 : 1.6, strokeOpacity: isActive || doneEdge ? 0.9 : 0.55, markerEnd: 'url(#arrow)', style: { transition: 'stroke .3s, stroke-width .3s' } }),
                isActive && React.createElement('circle', { r: 4, fill: io, style: { filter: `drop-shadow(0 0 5px ${io})` } },
                  React.createElement('animateMotion', { dur: '0.8s', repeatCount: 'indefinite', path: d, keyPoints: '0;1', keyTimes: '0;1' })),
                e.label && React.createElement('g', null,
                  React.createElement('rect', { x: (a.x + b.x) / 2 - 22, y: (a.y + b.y) / 2 - 9, width: 44, height: 16, rx: 4, fill: 'var(--bg-2)', stroke: 'var(--line)' }),
                  React.createElement('text', { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 3, textAnchor: 'middle', fontSize: 9, fontFamily: 'var(--font-mono)', fill: 'var(--fg-1)' }, e.label)));
            })),
          /* nodes */
          nodes.map(n => React.createElement(WorkflowNode, { key: n.id, node: n, selected: sel === n.id,
            active: activeId === n.id, done: run.doneSet.has(n.id), dimmed: run.on && activeId !== n.id && !run.doneSet.has(n.id),
            onSelect: setSel, onDragStart: onNodeDragStart, scale: zoom })))),

      /* zoom controls */
      React.createElement('div', { 'data-nopan': true, className: 'card', style: { position: 'absolute', bottom: 14, left: 14, zIndex: 30, padding: 3, display: 'flex', flexDirection: 'column', gap: 2, boxShadow: 'var(--sh-1)' } },
        React.createElement('button', { className: 'iconbtn', onClick: () => setZoom(z => Math.min(1.8, z * 1.15)) }, React.createElement(Icon, { name: 'zoomin', size: 16 })),
        React.createElement('div', { style: { textAlign: 'center', fontSize: 10, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' } }, Math.round(zoom * 100) + '%'),
        React.createElement('button', { className: 'iconbtn', onClick: () => setZoom(z => Math.max(0.35, z * 0.87)) }, React.createElement(Icon, { name: 'zoomout', size: 16 })),
        React.createElement('div', { className: 'divider', style: { margin: '2px 4px' } }),
        React.createElement('button', { className: 'iconbtn', onClick: fitView, title: 'Fit' }, React.createElement(Icon, { name: 'fit', size: 16 }))),

      /* minimap */
      React.createElement(Minimap, { nodes, bounds, pan, zoom, surfRef })),

    /* ===== INSPECTOR ===== */
    React.createElement('div', { style: { width: 304, flex: 'none', borderLeft: '1px solid var(--line)', background: 'var(--bg-1)', display: 'flex', flexDirection: 'column' } },
      selNode ? React.createElement(Inspector, { node: selNode, tab: inspTab, setTab: setInspTab, onUpdate: (patch) => setNodes(ns => ns.map(n => n.id === selNode.id ? { ...n, ...patch } : n)), onDelete: () => { setNodes(ns => ns.filter(n => n.id !== selNode.id)); setSel(null); }, onOpenFull: onOpenInspectorScreen })
        : React.createElement(EmptyState, { icon: 'workflows', title: 'Nothing selected', sub: 'Pick a node to edit its config, or drag one from the palette.' })));
}

/* ---- Minimap ---- */
function Minimap({ nodes, bounds, pan, zoom, surfRef }) {
  const { nodeHeight, NODE_W } = window.WorkflowNodeKit;
  const W = 168, H = 110;
  const bw = bounds.maxX - bounds.minX, bh = bounds.maxY - bounds.minY;
  const k = Math.min(W / bw, H / bh);
  const rect = surfRef.current ? surfRef.current.getBoundingClientRect() : { width: 800, height: 600 };
  const vx = (-pan.x / zoom - bounds.minX) * k, vy = (-pan.y / zoom - bounds.minY) * k;
  const vw = (rect.width / zoom) * k, vh = (rect.height / zoom) * k;
  return React.createElement('div', { 'data-nopan': true, className: 'card', style: { position: 'absolute', bottom: 14, right: 14, width: W, height: H, zIndex: 30, overflow: 'hidden', boxShadow: 'var(--sh-1)', padding: 0 } },
    React.createElement('svg', { width: W, height: H },
      nodes.map(n => { const cat = DATA.CAT_BY_TYPE[n.type] || 'control'; const w = (n.type === 'start' || n.type === 'end') ? 44 : NODE_W;
        return React.createElement('rect', { key: n.id, x: (n.position.x - bounds.minX) * k, y: (n.position.y - bounds.minY) * k, width: w * k, height: nodeHeight(n) * k, rx: 2, fill: window.WorkflowNodeKit.CAT_COLOR[cat], opacity: 0.55 }); }),
      React.createElement('rect', { x: Math.max(0, vx), y: Math.max(0, vy), width: Math.min(W, vw), height: Math.min(H, vh), fill: 'none', stroke: 'var(--accent)', strokeWidth: 1.5, rx: 3 })));
}

window.WorkflowCanvas = WorkflowCanvas;
