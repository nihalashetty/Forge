/* Forge — Inspector (canvas right panel) + SchemaForm primitive. */

/* SchemaForm: renders config fields from a lightweight schema. */
function SchemaForm({ fields, values, onChange }) {
  const v = values || {};
  return React.createElement('div', null, fields.map(f => {
    const val = v[f.key] != null ? v[f.key] : f.default;
    const set = (x) => onChange && onChange(f.key, x);
    let control;
    if (f.type === 'toggle') {
      return React.createElement('div', { key: f.key, className: 'row spread', style: { padding: '10px 0', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', { style: { paddingRight: 12 } }, React.createElement('div', { style: { fontSize: 13, fontWeight: 600 } }, f.label), f.help && React.createElement('div', { className: 'field-help', style: { marginTop: 2 } }, f.help)),
        React.createElement(Toggle, { on: !!val, onChange: set, signal: f.signal }));
    }
    if (f.type === 'select') control = React.createElement('div', { style: { position: 'relative' } },
      React.createElement('select', { className: 'select', value: val, onChange: e => set(e.target.value) }, f.options.map(o => React.createElement('option', { key: o.value || o, value: o.value || o }, o.label || o))),
      React.createElement(Icon, { name: 'chevdown', size: 14, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } }));
    else if (f.type === 'textarea') control = React.createElement('textarea', { className: 'textarea' + (f.mono ? ' mono' : ''), value: val || '', onChange: e => set(e.target.value), rows: f.rows || 3, placeholder: f.placeholder });
    else if (f.type === 'slider') control = React.createElement('div', { className: 'row gap3' },
      React.createElement('input', { type: 'range', min: f.min, max: f.max, step: f.step || 1, value: val, onChange: e => set(+e.target.value), style: { flex: 1, accentColor: 'var(--accent)' } }),
      React.createElement('span', { className: 'mono-sm', style: { width: 44, textAlign: 'right', color: 'var(--fg-1)' } }, val));
    else if (f.type === 'chips') control = React.createElement('div', { className: 'row gap1 wrap' }, (f.options).map(o => {
      const on = (val || []).includes(o);
      return React.createElement('button', { key: o, onClick: () => set(on ? val.filter(x => x !== o) : [...(val || []), o]), className: 'chip', style: { cursor: 'pointer', borderColor: on ? 'var(--accent)' : 'var(--line)', color: on ? 'var(--accent)' : 'var(--fg-1)', background: on ? 'var(--accent-glow)' : 'var(--bg-3)' } }, o); }));
    else control = React.createElement('input', { className: 'input' + (f.mono ? ' mono' : ''), value: val || '', onChange: e => set(e.target.value), placeholder: f.placeholder });
    return React.createElement(Field, { key: f.key, label: f.label, help: f.help }, control);
  }));
}

/* Node-type → config schema (representative) */
function schemaFor(node) {
  const t = node.type;
  if (t === 'agent' || t === 'deep_agent') return [
    { key: 'name', label: 'Name', default: node.title || '', placeholder: 'billing_agent' },
    { key: 'model', label: 'Model', type: 'select', default: 'anthropic:claude-sonnet-4-6', options: DATA.MODELS.map(m => ({ value: m.id, label: m.name })) },
    { key: 'prompt', label: 'System prompt', type: 'textarea', rows: 4, default: 'You are a billing support agent. Be concise and never invent refund amounts.' },
    { key: 'temp', label: 'Temperature', type: 'slider', min: 0, max: 1, step: 0.05, default: 0.2 },
  ];
  if (t === 'router') return [
    { key: 'mode', label: 'Routing mode', type: 'select', default: 'expression', options: [{ value: 'expression', label: 'Expression' }, { value: 'llm', label: 'LLM classifier' }] },
    { key: 'expr', label: 'Expression', mono: true, default: 'state.intent', help: 'JMESPath over run state' },
    { key: 'cases', label: 'Cases', type: 'chips', options: ['billing', 'technical', 'default'], default: node.cases || [] },
  ];
  if (t === 'retrieval') return [
    { key: 'sources', label: 'Sources', type: 'chips', options: ['Help Center', 'Billing FAQ', 'API Reference', 'Policies'], default: ['Help Center', 'Billing FAQ', 'Policies'] },
    { key: 'topk', label: 'Top K', type: 'slider', min: 1, max: 20, default: 5 },
    { key: 'hybrid', label: 'Hybrid search + rerank', type: 'toggle', default: true, signal: true },
  ];
  if (t === 'qa_lookup') return [
    { key: 'kind', label: 'Pair kind', type: 'select', default: 'faq', options: ['faq', 'error_workaround', 'canned_reply'] },
    { key: 'threshold', label: 'Match threshold', type: 'slider', min: 0.5, max: 1, step: 0.01, default: 0.85 },
    { key: 'deflect', label: 'Deflect on match (skip agent)', type: 'toggle', default: true },
  ];
  if (t === 'human_input') return [
    { key: 'prompt', label: 'Prompt to reviewer', type: 'textarea', rows: 2, default: 'Approve this refund?' },
    { key: 'actions', label: 'Allowed actions', type: 'chips', options: ['approve', 'edit', 'reject'], default: ['approve', 'edit', 'reject'] },
    { key: 'timeout', label: 'Timeout (min)', type: 'slider', min: 0, max: 120, default: 30 },
  ];
  return [
    { key: 'name', label: 'Name', default: node.title || DATA.NODE_META[t]?.label || '' },
    { key: 'notes', label: 'Notes', type: 'textarea', rows: 2, placeholder: 'Optional description…' },
  ];
}

function Inspector({ node, tab, setTab, onUpdate, onDelete, onOpenFull }) {
  const meta = DATA.NODE_META[node.type] || {};
  const cat = DATA.CAT_BY_TYPE[node.type] || 'control';
  const color = window.WorkflowNodeKit.CAT_COLOR[cat];
  const [vals, setVals] = useState({});
  useEffect(() => { setVals({}); }, [node.id]);
  const isAgent = node.type === 'agent' || node.type === 'deep_agent';
  const tabs = isAgent ? ['config', 'tools', 'middleware'] : ['config', 'ports'];

  return React.createElement('div', { className: 'col', style: { height: '100%' } },
    React.createElement('div', { style: { padding: '12px 14px', borderBottom: '1px solid var(--line)' } },
      React.createElement('div', { className: 'row gap2', style: { marginBottom: 10 } },
        React.createElement(Tile, { icon: meta.icon, color, size: 32 }),
        React.createElement('div', { className: 'grow', style: { minWidth: 0 } },
          React.createElement('input', { value: node.title || meta.label, onChange: e => onUpdate({ title: e.target.value }),
            style: { width: '100%', border: 'none', background: 'none', outline: 'none', fontSize: 15, fontWeight: 700, fontFamily: 'var(--font-display)', color: 'var(--fg-0)' } }),
          React.createElement('div', { className: 'mono-sm', style: { color: 'var(--fg-2)' } }, node.type)),
        React.createElement(Menu, { trigger: React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'more', size: 17 })),
          items: [{ icon: 'copy', label: 'Duplicate' }, { icon: 'external', label: 'Open full editor', onClick: () => onOpenFull && onOpenFull(node) }, { divider: true }, { icon: 'trash', label: 'Delete node', danger: true, onClick: onDelete }] })),
      React.createElement('div', { className: 'row gap2' },
        isAgent && React.createElement('button', { className: 'btn btn-secondary btn-sm grow', onClick: () => onOpenFull && onOpenFull(node) }, React.createElement(Icon, { name: 'sliders', size: 14 }), 'Full editor'),
        React.createElement('button', { className: 'btn btn-ghost btn-sm', title: 'Run from here' }, React.createElement(Icon, { name: 'play', size: 14 })))),
    React.createElement('div', { style: { padding: '0 14px' } }, React.createElement(Tabs, { tabs, value: tab, onChange: setTab })),
    React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 14 } },
      tab === 'config' && React.createElement(SchemaForm, { fields: schemaFor(node), values: vals, onChange: (k, x) => setVals(s => ({ ...s, [k]: x })) }),
      tab === 'tools' && React.createElement(InspectorTools, null),
      tab === 'middleware' && React.createElement(InspectorMiddleware, null),
      tab === 'ports' && React.createElement(InspectorPorts, { node })),
    React.createElement('div', { style: { padding: 12, borderTop: '1px solid var(--line)' } },
      React.createElement('div', { style: { fontSize: 11, color: 'var(--fg-2)', marginBottom: 6 } }, 'Context contribution'),
      React.createElement(TokenMeter, { compact: false, raw: 1240, projected: 92, max: 1400, animateKey: node.id })));
}

function InspectorTools() {
  const tools = DATA.TOOLS.slice(0, 3);
  return React.createElement('div', null,
    React.createElement('div', { className: 'row spread', style: { marginBottom: 8 } }, React.createElement('span', { className: 't-micro' }, 'Bound tools'), React.createElement('button', { className: 'btn btn-ghost btn-sm' }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Add')),
    React.createElement('div', { className: 'col gap2' }, tools.map(t => React.createElement('div', { key: t.id, className: 'row gap2', style: { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--line)' } },
      React.createElement(Icon, { name: DATA.KIND_ICON[t.kind], size: 15, style: { color: 'var(--io-tool)' } }),
      React.createElement('div', { className: 'grow' }, React.createElement('div', { className: 'mono-sm', style: { fontWeight: 600, color: 'var(--fg-0)' } }, t.name), React.createElement('div', { className: 'fg-2 t-caption' }, DATA.KIND_LABEL[t.kind])),
      React.createElement(TokenMeter, { compact: true, raw: t.rawTok, projected: t.projTok, animateKey: t.id })))));
}
function InspectorMiddleware() {
  return React.createElement('div', null,
    React.createElement('div', { className: 'row spread', style: { marginBottom: 8 } }, React.createElement('span', { className: 't-micro' }, 'Stack · runs top→down'), React.createElement('button', { className: 'btn btn-ghost btn-sm' }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Add')),
    React.createElement('div', { className: 'col gap2' }, DATA.AGENT_MW_STACK.map((m, i) => {
      const meta = DATA.MW_META[m.type];
      return React.createElement('div', { key: i, className: 'row gap2', style: { padding: '8px 10px', borderRadius: 8, border: '1px solid var(--line)', opacity: m.enabled ? 1 : 0.55 } },
        React.createElement(Icon, { name: 'drag', size: 14, style: { color: 'var(--fg-2)', cursor: 'grab' } }),
        React.createElement('div', { style: { width: 8, height: 8, borderRadius: 2, background: meta.color, flex: 'none' } }),
        React.createElement('div', { className: 'grow', style: { minWidth: 0 } }, React.createElement('div', { style: { fontSize: 12.5, fontWeight: 600 } }, meta.name), React.createElement('div', { className: 'truncate fg-2 t-caption mono-sm' }, m.summary)),
        React.createElement(Toggle, { on: m.enabled, onChange: () => {} }));
    })));
}
function InspectorPorts({ node }) {
  const ins = [{ name: 'in', io: 'messages' }], outs = node.cases ? node.cases.map(c => ({ name: c, io: 'control' })) : [{ name: 'out', io: 'messages' }];
  const Row = (p, dir) => React.createElement('div', { key: dir + p.name, className: 'row gap2', style: { padding: '7px 0', borderBottom: '1px solid var(--line)' } },
    React.createElement('div', { style: { width: 9, height: 9, borderRadius: '50%', border: '2px solid ' + (DATA.IO_COLOR[p.io] || 'var(--fg-2)') } }),
    React.createElement('span', { className: 'mono-sm grow', style: { fontWeight: 600 } }, p.name),
    React.createElement('span', { className: 'typechip' }, p.io));
  return React.createElement('div', null,
    React.createElement('div', { className: 't-micro', style: { marginBottom: 4 } }, 'Inputs'), ins.map(p => Row(p, 'in')),
    React.createElement('div', { className: 't-micro', style: { margin: '12px 0 4px' } }, 'Outputs'), outs.map(p => Row(p, 'out')));
}

Object.assign(window, { SchemaForm, schemaFor, Inspector, InspectorTools, InspectorMiddleware, InspectorPorts });
