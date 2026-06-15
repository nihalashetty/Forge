/* Forge — Node design language: typed nodes, ports, glowing edges. */

const NODE_W = 184;
function nodeHeight(n) {
  const meta = DATA.NODE_META[n.type];
  const isMini = n.type === 'start' || n.type === 'end';
  if (isMini) return 44;
  const lines = (n.summary || []).length;
  return 52 + (n.title ? 0 : 0) + lines * 17 + (n.mw ? 22 : 0) + 12;
}
const CAT_COLOR = {
  control: 'var(--io-control)', agent: 'var(--accent)', json: 'var(--io-json)',
  vector: 'var(--io-vector)', human: 'var(--warn)', signal: 'var(--signal)',
};

/* Port positions (graph coords) for an edge endpoint */
function portPos(node, side) {
  const h = nodeHeight(node);
  const isMini = node.type === 'start' || node.type === 'end';
  const w = isMini ? 44 : NODE_W;
  return {
    x: node.position.x + (side === 'out' ? w : 0),
    y: node.position.y + h / 2,
  };
}

/* Cubic bezier path between two points, horizontal flow */
function edgePath(a, b) {
  const dx = Math.max(40, Math.abs(b.x - a.x) * 0.5);
  return `M ${a.x} ${a.y} C ${a.x + dx} ${a.y}, ${b.x - dx} ${b.y}, ${b.x} ${b.y}`;
}

function WorkflowNode({ node, selected, active, done, dimmed, onSelect, onDragStart, scale }) {
  const meta = DATA.NODE_META[node.type] || {};
  const cat = DATA.CAT_BY_TYPE[node.type] || 'control';
  const color = CAT_COLOR[cat];
  const isMini = node.type === 'start' || node.type === 'end';
  const h = nodeHeight(node);
  const w = isMini ? 44 : NODE_W;
  const borderColor = active ? color : selected ? 'var(--accent)' : done ? color : 'var(--line-strong)';
  const ring = active ? `0 0 0 2px ${color}, 0 0 26px color-mix(in srgb, ${color} 60%, transparent)`
    : selected ? `0 0 0 2px var(--accent), 0 0 0 5px var(--accent-glow)` : 'var(--node-shadow)';

  if (isMini) {
    return React.createElement('div', { 'data-node': node.id,
      onMouseDown: e => onDragStart(e, node), onClick: e => { e.stopPropagation(); onSelect(node.id); },
      style: { position: 'absolute', left: node.position.x, top: node.position.y, width: w, height: h, borderRadius: 22, cursor: 'grab',
        background: 'var(--bg-2)', border: `1.5px solid ${borderColor}`, boxShadow: ring, display: 'flex', alignItems: 'center', justifyContent: 'center', color,
        opacity: dimmed ? 0.4 : 1, transition: 'box-shadow .25s, border-color .25s, opacity .25s' } },
      React.createElement(Icon, { name: meta.icon, size: 20 }),
      React.createElement(Port, { side: node.type === 'start' ? 'out' : 'in', color, y: h / 2, on: w }),
      active && React.createElement(ActivePip, { color }));
  }

  return React.createElement('div', { 'data-node': node.id,
    onClick: e => { e.stopPropagation(); onSelect(node.id); },
    style: { position: 'absolute', left: node.position.x, top: node.position.y, width: w, borderRadius: 11,
      background: 'var(--bg-2)', border: `1px solid ${borderColor}`, boxShadow: ring, overflow: 'visible',
      opacity: dimmed ? 0.4 : 1, transition: 'box-shadow .25s, border-color .25s, opacity .25s' } },
    /* header (drag handle) */
    React.createElement('div', { onMouseDown: e => onDragStart(e, node),
      style: { display: 'flex', alignItems: 'center', gap: 8, padding: '8px 10px', cursor: 'grab', borderBottom: '1px solid var(--line)',
        background: `linear-gradient(90deg, color-mix(in srgb, ${color} 12%, var(--bg-2)), var(--bg-2))`, borderRadius: '11px 11px 0 0' } },
      React.createElement('div', { style: { width: 22, height: 22, borderRadius: 6, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: `color-mix(in srgb, ${color} 18%, var(--bg-2))`, color } },
        React.createElement(Icon, { name: meta.icon, size: 14 })),
      React.createElement('div', { className: 'grow', style: { minWidth: 0 } },
        React.createElement('div', { className: 'truncate', style: { fontSize: 12.5, fontWeight: 700, fontFamily: 'var(--font-display)', letterSpacing: '-.01em' } }, node.title || meta.label),
        React.createElement('div', { className: 'truncate', style: { fontSize: 10, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)', textTransform: 'lowercase' } }, node.type)),
      done && React.createElement('div', { style: { color: color, flex: 'none' } }, React.createElement(Icon, { name: 'check', size: 14 }))),
    /* body: summary chips */
    React.createElement('div', { style: { padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 4 } },
      (node.summary || []).map((s, i) => React.createElement('div', { key: i, style: { fontSize: 10.5, color: 'var(--fg-1)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, s)),
      node.mw && React.createElement('div', { className: 'row gap1', style: { marginTop: 2 } },
        node.mw.slice(0, 4).map((m, i) => React.createElement('div', { key: i, title: m, style: { width: 7, height: 7, borderRadius: 2, background: (DATA.MW_META[m] || {}).color || 'var(--fg-2)' } })),
        React.createElement('span', { style: { fontSize: 9.5, color: 'var(--fg-2)', marginLeft: 2 } }, node.mw.length + ' middleware'))),
    /* router cases as out-ports */
    node.cases
      ? node.cases.map((c, i) => React.createElement(Port, { key: c, side: 'out', color: i === 0 ? color : 'var(--io-control)', y: 30 + i * 17, on: w, label: c }))
      : React.createElement(Port, { side: 'out', color, y: h / 2, on: w }),
    React.createElement(Port, { side: 'in', color, y: h / 2, on: w }),
    active && React.createElement(ActivePip, { color }));
}

function Port({ side, color, y, on, label }) {
  return React.createElement('div', { style: { position: 'absolute', top: y, [side === 'out' ? 'left' : 'left']: side === 'out' ? on : 0, transform: 'translate(-50%,-50%)', zIndex: 3 } },
    React.createElement('div', { style: { width: 10, height: 10, borderRadius: '50%', background: 'var(--bg-2)', border: `2px solid ${color}`, boxShadow: `0 0 6px color-mix(in srgb, ${color} 60%, transparent)` } }));
}

function ActivePip({ color }) {
  return React.createElement('div', { style: { position: 'absolute', top: -7, right: -7, width: 16, height: 16, borderRadius: '50%', background: color, display: 'flex', alignItems: 'center', justifyContent: 'center', boxShadow: `0 0 12px ${color}` } },
    React.createElement('div', { style: { width: 6, height: 6, borderRadius: '50%', background: '#fff', animation: 'pulse 1s infinite' } }));
}

window.WorkflowNodeKit = { WorkflowNode, portPos, edgePath, nodeHeight, NODE_W, CAT_COLOR };
