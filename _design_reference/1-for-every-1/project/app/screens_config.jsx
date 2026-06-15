/* Forge config screens: Agents, Agent Config (middleware stack), Tools, Tool Builder, Auth Providers. */

/* ============ AGENTS LIST ============ */
function AgentsScreen({ onOpen }) {
  return React.createElement(ListScaffold, {
    title: 'Agents', sub: 'Reusable agent presets — model, tools, and a middleware stack.',
    action: React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'plus', size: 15 }), 'New agent'),
    children: React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(290px,1fr))', gap: 14 } },
      DATA.AGENTS.map(a => React.createElement('div', { key: a.id, className: 'card card-hover', style: { padding: 16 }, onClick: () => onOpen(a) },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } },
          React.createElement(Tile, { icon: a.flavor === 'deep_agent' ? 'n_deepagent' : 'n_agent', color: 'var(--accent)', size: 38 }),
          React.createElement('span', { className: 'chip chip-mono' }, a.flavor === 'deep_agent' ? 'deep agent' : 'agent')),
        React.createElement('div', { className: 't-h2 mono', style: { fontSize: 14 } }, a.name),
        React.createElement('div', { className: 'mono-sm fg-2', style: { marginTop: 3 } }, a.model.split(':')[1]),
        React.createElement('div', { className: 'divider', style: { margin: '12px 0' } }),
        React.createElement('div', { className: 'row gap3' },
          React.createElement('span', { className: 'row gap1 fg-1 t-caption' }, React.createElement(Icon, { name: 'tools', size: 14, style: { color: 'var(--signal)' } }), a.tools + ' tools'),
          React.createElement('span', { className: 'row gap1 fg-1 t-caption' }, React.createElement(Icon, { name: 'layers', size: 14, style: { color: 'var(--io-json)' } }), a.mw + ' middleware'),
          React.createElement('span', { className: 'grow' }),
          React.createElement('span', { className: 'fg-2 t-caption' }, a.updated)))) )
  });
}

/* ============ AGENT CONFIG (middleware stack signature) ============ */
function AgentConfigScreen({ agent, onBack }) {
  const a = agent || DATA.AGENTS[0];
  const [tab, setTab] = useState('build');
  const [stack, setStack] = useState(DATA.AGENT_MW_STACK.map((m, i) => ({ ...m, _id: i })));
  const [addOpen, setAddOpen] = useState(false);
  const [tools, setTools] = useState(DATA.TOOLS.filter(t => ['t_get_order', 't_get_invoice'].includes(t.id)).map(t => t.id));
  const dragRef = useRef(null);

  const move = (from, to) => setStack(s => { const c = [...s]; const [m] = c.splice(from, 1); c.splice(to, 0, m); return c; });

  const rawTotal = stack.filter(m => m.enabled).reduce((a) => a + 0, 8200) + 8200;
  const projTotal = 3400;

  return React.createElement('div', { className: 'col', style: { flex: 1, minHeight: 0 } },
    /* sub-header */
    React.createElement('div', { className: 'row spread', style: { padding: '14px 22px', borderBottom: '1px solid var(--line)', flex: 'none' } },
      React.createElement('div', { className: 'row gap3' },
        React.createElement(Tile, { icon: a.flavor === 'deep_agent' ? 'n_deepagent' : 'n_agent', color: 'var(--accent)', size: 40, glow: true }),
        React.createElement('div', null,
          React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 't-display mono', style: { fontSize: 19 } }, a.name), React.createElement('span', { className: 'chip chip-mono' }, a.flavor === 'deep_agent' ? 'deep agent' : 'agent')),
          React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 2 } }, a.model, ' · used by 2 workflows'))),
      React.createElement('div', { className: 'row gap2' },
        React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'play', size: 15 }), 'Test in playground'),
        React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'save', size: 15 }), 'Save preset'))),
    React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '20px 22px' } },
      React.createElement('div', { style: { maxWidth: 980, margin: '0 auto', display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 22, alignItems: 'start' } },
        /* LEFT: model + prompt + tools */
        React.createElement('div', { className: 'col gap5' },
          React.createElement('div', { className: 'card', style: { padding: 18 } },
            React.createElement('div', { className: 't-h1', style: { marginBottom: 14 } }, 'Model & prompt'),
            React.createElement(Field, { label: 'Model', help: 'Provider:model — tools and vision support detected automatically.' },
              React.createElement('div', { style: { position: 'relative' } },
                React.createElement('select', { className: 'select', defaultValue: a.model }, DATA.MODELS.map(m => React.createElement('option', { key: m.id, value: m.id }, m.provider + ' · ' + m.name))),
                React.createElement(Icon, { name: 'chevdown', size: 14, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } }))),
            React.createElement(Field, { label: 'System prompt' },
              React.createElement('textarea', { className: 'textarea', rows: 4, defaultValue: 'You are a billing support agent for Acme. Resolve order and invoice questions. Never invent refund amounts — always call get_order first. Escalate refunds over $100 for human approval.' })),
            React.createElement('div', { className: 'row gap4' },
              React.createElement(Field, { label: 'Temperature' }, React.createElement('input', { className: 'input', defaultValue: '0.2' })),
              React.createElement(Field, { label: 'Max tokens' }, React.createElement('input', { className: 'input', defaultValue: '1024' })))),
          React.createElement('div', { className: 'card', style: { padding: 18 } },
            React.createElement('div', { className: 'row spread', style: { marginBottom: 14 } }, React.createElement('div', { className: 't-h1' }, 'Tools'), React.createElement('button', { className: 'btn btn-secondary btn-sm' }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Bind tool')),
            React.createElement('div', { className: 'col gap2' }, DATA.TOOLS.slice(0, 4).map(t => {
              const on = tools.includes(t.id);
              return React.createElement('div', { key: t.id, className: 'row gap3', style: { padding: '9px 11px', borderRadius: 9, border: '1px solid ' + (on ? 'var(--line-strong)' : 'var(--line)'), opacity: on ? 1 : 0.6 } },
                React.createElement(Icon, { name: DATA.KIND_ICON[t.kind], size: 16, style: { color: 'var(--io-tool)' } }),
                React.createElement('div', { className: 'grow', style: { minWidth: 0 } }, React.createElement('div', { className: 'mono-sm', style: { fontWeight: 600 } }, t.name), React.createElement('div', { className: 'truncate fg-2 t-caption' }, t.desc)),
                React.createElement(Toggle, { on, onChange: () => setTools(s => on ? s.filter(x => x !== t.id) : [...s, t.id]) }));
            })))),
        /* RIGHT: middleware stack */
        React.createElement('div', { className: 'card', style: { padding: 18, position: 'sticky', top: 0 } },
          React.createElement('div', { className: 'row spread', style: { marginBottom: 4 } }, React.createElement('div', { className: 't-h1' }, 'Middleware stack'), React.createElement('button', { className: 'btn btn-secondary btn-sm', onClick: () => setAddOpen(true) }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Add')),
          React.createElement('div', { className: 'fg-2 t-caption', style: { marginBottom: 14 } }, 'Wraps the agent in order, top → bottom. Drag to reorder.'),
          React.createElement('div', { className: 'col', style: { gap: 0, position: 'relative' } },
            React.createElement('div', { style: { position: 'absolute', left: 17, top: 14, bottom: 30, width: 2, background: 'linear-gradient(var(--accent),var(--signal))', opacity: 0.35 } }),
            stack.map((m, i) => React.createElement(MwStackItem, { key: m._id, m, i, last: i === stack.length - 1,
              onToggle: () => setStack(s => s.map((x, j) => j === i ? { ...x, enabled: !x.enabled } : x)),
              onRemove: () => setStack(s => s.filter((_, j) => j !== i)),
              onDragStart: () => dragRef.current = i, onDrop: () => { if (dragRef.current != null && dragRef.current !== i) move(dragRef.current, i); dragRef.current = null; } })),
            React.createElement('div', { className: 'row gap2', style: { paddingLeft: 6, marginTop: 4 } },
              React.createElement('div', { style: { width: 24, height: 24, borderRadius: '50%', border: '2px dashed var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--accent)', background: 'var(--bg-1)', zIndex: 1 } }, React.createElement(Icon, { name: 'n_agent', size: 13 })),
              React.createElement('span', { className: 'fg-1', style: { fontSize: 12.5, fontWeight: 600 } }, 'core agent loop'))),
          React.createElement('div', { className: 'divider', style: { margin: '16px 0 14px' } }),
          React.createElement('div', { style: { fontSize: 11, color: 'var(--fg-2)', marginBottom: 8 } }, 'Estimated context per turn'),
          React.createElement(TokenMeter, { raw: rawTotal, projected: projTotal, max: rawTotal * 1.1, animateKey: stack.map(s => s.enabled).join() })))),
    React.createElement(MwAddModal, { open: addOpen, onClose: () => setAddOpen(false), onAdd: (type) => { setStack(s => [...s, { type, enabled: true, summary: 'newly added · configure', _id: Date.now() }]); setAddOpen(false); } }));
}

function MwStackItem({ m, i, last, onToggle, onRemove, onDragStart, onDrop }) {
  const meta = DATA.MW_META[m.type] || { name: m.type, color: 'var(--fg-2)', cat: '' };
  const [hover, setHover] = useState(false);
  return React.createElement('div', { draggable: true, onDragStart, onDragOver: e => e.preventDefault(), onDrop,
    onMouseEnter: () => setHover(true), onMouseLeave: () => setHover(false),
    className: 'row gap2', style: { padding: '8px 8px', marginBottom: 6, borderRadius: 10, border: '1px solid ' + (hover ? 'var(--line-strong)' : 'var(--line)'), background: 'var(--bg-1)', opacity: m.enabled ? 1 : 0.5, position: 'relative', zIndex: 1, cursor: 'default' } },
    React.createElement(Icon, { name: 'drag', size: 14, style: { color: 'var(--fg-2)', cursor: 'grab' } }),
    React.createElement('div', { style: { width: 24, height: 24, borderRadius: 7, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', background: `color-mix(in srgb, ${meta.color} 16%, var(--bg-1))`, border: `1px solid color-mix(in srgb, ${meta.color} 30%, transparent)` } },
      React.createElement('div', { style: { width: 8, height: 8, borderRadius: 2, background: meta.color } })),
    React.createElement('div', { className: 'grow', style: { minWidth: 0 } },
      React.createElement('div', { style: { fontSize: 12.5, fontWeight: 600 } }, meta.name),
      React.createElement('div', { className: 'truncate fg-2', style: { fontSize: 11, fontFamily: 'var(--font-mono)' } }, m.summary)),
    hover && React.createElement('button', { className: 'iconbtn', style: { width: 24, height: 24 }, onClick: onRemove }, React.createElement(Icon, { name: 'x', size: 14 })),
    React.createElement(Toggle, { on: m.enabled, onChange: onToggle }));
}

function MwAddModal({ open, onClose, onAdd }) {
  const [q, setQ] = useState('');
  return React.createElement(Modal, { open, onClose, title: 'Add middleware', width: 560 },
    React.createElement('div', { className: 'row gap2', style: { marginBottom: 14, background: 'var(--bg-3)', borderRadius: 8, padding: '0 10px', height: 34 } },
      React.createElement(Icon, { name: 'search', size: 15, style: { color: 'var(--fg-2)' } }),
      React.createElement('input', { value: q, onChange: e => setQ(e.target.value), placeholder: 'Search middleware…', autoFocus: true, style: { flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 13.5, fontFamily: 'var(--font-ui)', color: 'var(--fg-0)' } })),
    DATA.MIDDLEWARE_CATALOG.map(c => {
      const items = c.items.filter(it => it.name.toLowerCase().includes(q.toLowerCase()));
      if (!items.length) return null;
      return React.createElement('div', { key: c.cat, style: { marginBottom: 16 } },
        React.createElement('div', { className: 'row gap2', style: { marginBottom: 8 } }, React.createElement('div', { style: { width: 8, height: 8, borderRadius: 2, background: c.color } }), React.createElement('span', { className: 't-micro' }, c.cat)),
        React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 } }, items.map(it =>
          React.createElement('button', { key: it.type, onClick: () => onAdd(it.type), className: 'col', style: { textAlign: 'left', padding: 11, borderRadius: 9, border: '1px solid var(--line)', background: 'var(--bg-1)', cursor: 'pointer', gap: 3 },
            onMouseEnter: e => e.currentTarget.style.borderColor = c.color, onMouseLeave: e => e.currentTarget.style.borderColor = 'var(--line)' },
            React.createElement('div', { style: { fontSize: 12.5, fontWeight: 600 } }, it.name),
            React.createElement('div', { className: 'fg-2', style: { fontSize: 11, lineHeight: '15px' } }, it.desc)))));
    }));
}

/* ============ TOOLS LIST ============ */
function ToolsScreen({ onOpen }) {
  const [view, setView] = useState('grid');
  return React.createElement(ListScaffold, {
    title: 'Tools', sub: 'External capabilities — REST, GraphQL, code, MCP, or builtins — with response projection.',
    action: React.createElement('div', { className: 'row gap2' },
      React.createElement(Segmented, { options: [{ value: 'grid', icon: 'grid' }, { value: 'list', icon: 'list' }], value: view, onChange: setView }),
      React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'plus', size: 15 }), 'New tool')),
    children: view === 'grid'
      ? React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(300px,1fr))', gap: 14 } },
          DATA.TOOLS.map(t => React.createElement('div', { key: t.id, className: 'card card-hover', style: { padding: 15 }, onClick: () => onOpen(t) },
            React.createElement('div', { className: 'row spread', style: { marginBottom: 10 } },
              React.createElement('div', { className: 'row gap2' }, React.createElement(Tile, { icon: DATA.KIND_ICON[t.kind], color: 'var(--io-tool)', size: 34 }), React.createElement('span', { className: 'chip chip-mono' }, DATA.KIND_LABEL[t.kind])),
              React.createElement(StatusPill, { status: t.tested })),
            React.createElement('div', { className: 'mono', style: { fontWeight: 600, fontSize: 13.5, color: 'var(--fg-0)' } }, t.name),
            React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 3, height: 32, overflow: 'hidden' } }, t.desc),
            React.createElement('div', { className: 'divider', style: { margin: '10px 0' } }),
            t.rawTok > 0
              ? React.createElement('div', { className: 'col gap1' }, React.createElement('div', { className: 'row spread', style: { fontSize: 10.5, color: 'var(--fg-2)' } }, React.createElement('span', null, 'response projection'), t.auth && React.createElement('span', { className: 'row gap1' }, React.createElement(Icon, { name: 'auth', size: 11 }), t.auth)), React.createElement(TokenMeter, { raw: t.rawTok, projected: t.projTok, animateKey: t.id }))
              : React.createElement('div', { className: 'fg-2 t-caption row gap2' }, React.createElement(Icon, { name: 'minus', size: 13 }), 'No projection configured'))))
      : React.createElement('div', { className: 'card', style: { overflow: 'hidden' } },
          React.createElement('table', { className: 'tbl' },
            React.createElement('thead', null, React.createElement('tr', null, ['Tool', 'Kind', 'Auth', 'Projection', 'Status', 'Ver', ''].map((h, i) => React.createElement('th', { key: i }, h)))),
            React.createElement('tbody', null, DATA.TOOLS.map(t => React.createElement('tr', { key: t.id, className: 'row', onClick: () => onOpen(t) },
              React.createElement('td', null, React.createElement('div', { className: 'row gap2' }, React.createElement(Icon, { name: DATA.KIND_ICON[t.kind], size: 15, style: { color: 'var(--io-tool)' } }), React.createElement('span', { className: 'mono-sm', style: { fontWeight: 600 } }, t.name))),
              React.createElement('td', null, React.createElement('span', { className: 'chip chip-mono' }, DATA.KIND_LABEL[t.kind])),
              React.createElement('td', { className: 'mono-sm fg-1' }, t.auth || '—'),
              React.createElement('td', null, t.rawTok > 0 ? React.createElement(TokenMeter, { compact: true, raw: t.rawTok, projected: t.projTok, animateKey: t.id }) : React.createElement('span', { className: 'fg-2' }, '—')),
              React.createElement('td', null, React.createElement(StatusPill, { status: t.tested })),
              React.createElement('td', { className: 'mono-sm fg-2' }, 'v' + t.version),
              React.createElement('td', null, React.createElement(Icon, { name: 'chevright', size: 16, style: { color: 'var(--fg-2)' } })))))))
  });
}

Object.assign(window, { AgentsScreen, AgentConfigScreen, ToolsScreen, MwStackItem, MwAddModal });
