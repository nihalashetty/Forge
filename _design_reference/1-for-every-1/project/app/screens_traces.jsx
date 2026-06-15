/* Forge — Traces: runs list, span waterfall, cost donut. */
function TracesScreen() {
  const [sel, setSel] = useState(DATA.TRACE_RUNS[0]);
  const [tab, setTab] = useState('waterfall');
  const totalDur = 4200;
  return React.createElement('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
    /* runs list */
    React.createElement('div', { style: { width: 300, flex: 'none', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)' } },
      React.createElement('div', { className: 'col gap2', style: { padding: '14px 14px 10px', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', { className: 'row spread' }, React.createElement('div', { className: 't-h1' }, 'Traces'), React.createElement('button', { className: 'btn btn-ghost btn-sm' }, React.createElement(Icon, { name: 'filter', size: 14 }), 'Filter')),
        React.createElement('div', { className: 'row gap2', style: { background: 'var(--bg-3)', borderRadius: 7, padding: '0 9px', height: 30 } }, React.createElement(Icon, { name: 'search', size: 14, style: { color: 'var(--fg-2)' } }), React.createElement('input', { placeholder: 'Filter runs…', style: { flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 12.5, fontFamily: 'var(--font-ui)', color: 'var(--fg-0)' } }))),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 8 } }, DATA.TRACE_RUNS.map(r => {
        const on = sel.id === r.id;
        return React.createElement('button', { key: r.id, onClick: () => setSel(r), className: 'col', style: { width: '100%', textAlign: 'left', padding: '11px 12px', borderRadius: 9, marginBottom: 4, gap: 6, cursor: 'pointer', border: '1px solid ' + (on ? 'var(--accent)' : 'transparent'), background: on ? 'var(--accent-glow)' : 'transparent' } },
          React.createElement('div', { className: 'row spread' }, React.createElement('span', { style: { fontSize: 13, fontWeight: 600 } }, r.workflow), React.createElement(StatusPill, { status: r.status })),
          React.createElement('div', { className: 'row gap3', style: { fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' } }, React.createElement('span', null, r.started), React.createElement('span', null, r.dur), React.createElement('span', null, r.tokens), React.createElement('span', { style: { color: 'var(--accent)' } }, r.cost)));
      }))),
    /* detail */
    React.createElement('div', { className: 'grow col', style: { minHeight: 0 } },
      React.createElement('div', { className: 'row spread', style: { padding: '14px 22px', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', null,
          React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 't-h1' }, sel.workflow), React.createElement(StatusPill, { status: sel.status }), React.createElement('span', { className: 'chip chip-mono' }, 'trace_' + sel.id)),
          React.createElement('div', { className: 'row gap3', style: { marginTop: 4, fontSize: 12, color: 'var(--fg-2)' } }, React.createElement('span', null, '⏱ ' + sel.dur), React.createElement('span', null, '◇ ' + sel.tokens + ' tokens'), React.createElement('span', null, '$ ' + sel.cost), React.createElement('span', null, '⚡ via ' + sel.trigger))),
        React.createElement('div', { className: 'row gap2' }, React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'workflows', size: 15 }), 'Open in canvas'), React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'play', size: 15 }), 'Replay'))),
      React.createElement('div', { style: { padding: '0 22px', borderBottom: '1px solid var(--line)' } }, React.createElement(Tabs, { tabs: [{ value: 'waterfall', label: 'Waterfall' }, { value: 'cost', label: 'Cost & tokens' }, { value: 'io', label: 'Input / Output' }], value: tab, onChange: setTab })),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 22 } },
        tab === 'waterfall' && React.createElement(Waterfall, { totalDur }),
        tab === 'cost' && React.createElement(CostBreakdown, null),
        tab === 'io' && React.createElement(TraceIO, null))));
}

function Waterfall({ totalDur }) {
  const KIND_COLOR = { chain: 'var(--io-control)', node: 'var(--io-control)', agent: 'var(--accent)', llm: 'var(--io-json)', tool: 'var(--io-tool)', retriever: 'var(--io-vector)' };
  const [hover, setHover] = useState(null);
  return React.createElement('div', null,
    React.createElement('div', { className: 'row', style: { marginBottom: 10, paddingLeft: 220, gap: 0, fontSize: 10, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' } },
      [0, 1, 2, 3, 4].map(s => React.createElement('div', { key: s, style: { flex: 1 } }, s + '.0s'))),
    React.createElement('div', { className: 'col', style: { gap: 2 } }, DATA.SPANS.map(s => {
      const left = (s.start / totalDur) * 100, width = Math.max(1.5, (s.dur / totalDur) * 100);
      const color = KIND_COLOR[s.kind] || 'var(--fg-2)';
      const isH = hover === s.id;
      return React.createElement('div', { key: s.id, className: 'row', onMouseEnter: () => setHover(s.id), onMouseLeave: () => setHover(null), style: { height: 34, borderRadius: 7, background: isH ? 'var(--bg-3)' : 'transparent', alignItems: 'center' } },
        React.createElement('div', { className: 'row gap2', style: { width: 220, flex: 'none', paddingLeft: 6 + s.depth * 16, minWidth: 0 } },
          React.createElement('div', { style: { width: 7, height: 7, borderRadius: 2, background: color, flex: 'none' } }),
          React.createElement('span', { className: 'truncate', style: { fontSize: 12, fontWeight: s.depth === 0 ? 700 : 500, fontFamily: s.kind === 'tool' || s.kind === 'llm' ? 'var(--font-mono)' : 'var(--font-ui)' } }, s.name)),
        React.createElement('div', { className: 'grow', style: { position: 'relative', height: '100%' } },
          React.createElement('div', { style: { position: 'absolute', top: 9, left: left + '%', width: width + '%', height: 16, borderRadius: 5, background: `color-mix(in srgb, ${color} 78%, transparent)`, border: `1px solid ${color}`, display: 'flex', alignItems: 'center', paddingLeft: 6, boxShadow: isH ? `0 0 12px color-mix(in srgb,${color} 50%,transparent)` : 'none', transition: 'box-shadow .2s' } },
            React.createElement('span', { style: { fontSize: 9.5, color: '#fff', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', fontWeight: 600 } }, s.dur + 'ms'))),
        React.createElement('div', { style: { width: 90, flex: 'none', textAlign: 'right', paddingRight: 6, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' } }, s.tokens + ' · ' + s.cost));
    })),
    React.createElement('div', { className: 'card', style: { marginTop: 16, padding: 14, background: 'var(--warn-bg)', borderColor: 'transparent' } },
      React.createElement('div', { className: 'row gap2' }, React.createElement(Icon, { name: 'n_human', size: 16, style: { color: 'var(--warn)' } }), React.createElement('span', { style: { fontSize: 12.5, fontWeight: 600, color: 'var(--warn)' } }, 'Run interrupted at approve_refund — resumed after human approval (+1.4s wait, not billed).'))));
}

function CostBreakdown() {
  const segs = DATA.COST_BY_NODE.map(c => ({ value: Math.max(0.0003, c.cost), color: c.color }));
  return React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '260px 1fr', gap: 28, alignItems: 'start' } },
    React.createElement('div', { className: 'card', style: { padding: 20, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14 } },
      React.createElement(Donut, { segments: segs, size: 150, thickness: 20, center: React.createElement('div', { style: { textAlign: 'center' } }, React.createElement('div', { className: 't-display mono', style: { fontSize: 22 } }, '$0.038'), React.createElement('div', { className: 'fg-2 t-caption' }, 'total cost')) }),
      React.createElement('div', { className: 'col gap2', style: { width: '100%' } }, DATA.COST_BY_NODE.map(c => React.createElement('div', { key: c.name, className: 'row spread', style: { fontSize: 12 } }, React.createElement('span', { className: 'row gap2' }, React.createElement('span', { style: { width: 8, height: 8, borderRadius: 2, background: c.color } }), React.createElement('span', { className: 'fg-1' }, c.name)), React.createElement('span', { className: 'mono-sm' }, '$' + c.cost.toFixed(4)))))),
    React.createElement('div', { className: 'col gap4' },
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12 } }, [['Prompt tokens', '8,210'], ['Completion tokens', '4,190'], ['Tool tokens', '92 / 1,240 raw']].map((s, i) => React.createElement('div', { key: i, className: 'card', style: { padding: 14 } }, React.createElement('div', { className: 't-micro', style: { marginBottom: 5 } }, s[0]), React.createElement('div', { className: 'mono', style: { fontSize: 16, fontWeight: 600, color: 'var(--fg-0)' } }, s[1])))),
      React.createElement('div', { className: 'card', style: { padding: 16 } },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } }, React.createElement('span', { className: 't-h2' }, 'Projection savings on this run'), React.createElement('span', { className: 'pill pill-ok' }, React.createElement(Icon, { name: 'bolt', size: 12 }), '93% trimmed')),
        React.createElement(TokenMeter, { raw: 1240, projected: 92, max: 1400, animateKey: 'trace' }),
        React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 10 } }, 'get_order returned 1,240 tokens of raw JSON; projection sent 92 to the model — saving ~$0.011 on this single call.'))));
}

function TraceIO() {
  return React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 } },
    React.createElement('div', null, React.createElement('div', { className: 't-micro', style: { marginBottom: 8 } }, 'Input'), React.createElement(CodeBlock, { code: '{\n  "messages": [\n    { "role": "user",\n      "content": "I was charged twice for order #8842…" }\n  ],\n  "context": { "channel": "widget", "locale": "en-US" }\n}' })),
    React.createElement('div', null, React.createElement('div', { className: 't-micro', style: { marginBottom: 8 } }, 'Output'), React.createElement(CodeBlock, { code: '{\n  "messages": [ … ],\n  "final": "I can see order #8842 was charged twice…",\n  "actions": [ { "type": "refund", "status": "pending_approval", "amount": 11000 } ],\n  "usage": { "tokens": 12431, "cost": 0.038 }\n}' })));
}

Object.assign(window, { TracesScreen, Waterfall, CostBreakdown, TraceIO });
