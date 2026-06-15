/* Forge — Knowledge: sources, Q&A pairs, search debugger. */
function KnowledgeScreen() {
  const [tab, setTab] = useState('sources');
  return React.createElement('div', { className: 'col', style: { flex: 1, minHeight: 0 } },
    React.createElement('div', { style: { padding: '18px 28px 0', flex: 'none' } },
      React.createElement('div', { className: 'row spread', style: { marginBottom: 14 } },
        React.createElement('div', null, React.createElement('div', { className: 't-display', style: { fontSize: 21 } }, 'Knowledge'), React.createElement('div', { className: 'fg-1', style: { marginTop: 3 } }, 'Vector + keyword sources, semantic Q&A pairs, and a live retrieval debugger.')),
        React.createElement('div', { className: 'row gap2' },
          tab === 'sources' && React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'refresh', size: 15 }), 'Reindex'),
          React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: tab === 'qa' ? 'plus' : 'upload', size: 15 }), tab === 'qa' ? 'New pair' : 'Add source'))),
      React.createElement(Tabs, { tabs: [{ value: 'sources', label: 'Sources', count: DATA.KB_SOURCES.length }, { value: 'qa', label: 'Q&A Pairs', count: DATA.QA_PAIRS.length }, { value: 'search', label: 'Search debugger' }], value: tab, onChange: setTab })),
    React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '18px 28px' } },
      React.createElement('div', { style: { maxWidth: 1080, margin: '0 auto' } },
        tab === 'sources' && React.createElement(KnowledgeSources, null),
        tab === 'qa' && React.createElement(KnowledgeQA, null),
        tab === 'search' && React.createElement(SearchDebugger, null))));
}

function KnowledgeSources() {
  const KIND_ICON = { url: 'globe', file: 'file', s3: 'db', text: 'msg' };
  return React.createElement('div', { className: 'col gap5' },
    React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 } },
      [['Indexed chunks', '942', 'coins'], ['Embedding model', 'text-embedding-3-small', 'n_llm'], ['Index size', '8.6 MB · pgvector', 'db']].map((s, i) =>
        React.createElement('div', { key: i, className: 'card', style: { padding: 14 } }, React.createElement('div', { className: 'row gap2', style: { marginBottom: 6 } }, React.createElement(Icon, { name: s[2], size: 15, style: { color: 'var(--fg-2)' } }), React.createElement('span', { className: 't-micro' }, s[0])), React.createElement('div', { className: 'mono', style: { fontSize: 16, fontWeight: 600, color: 'var(--fg-0)' } }, s[1])))),
    React.createElement('div', { className: 'card', style: { overflow: 'hidden' } },
      React.createElement('table', { className: 'tbl' },
        React.createElement('thead', null, React.createElement('tr', null, ['Source', 'Type', 'Chunks', 'Size', 'Status', 'Updated', ''].map((h, i) => React.createElement('th', { key: i }, h)))),
        React.createElement('tbody', null, DATA.KB_SOURCES.map(k => React.createElement('tr', { key: k.id, className: 'row' },
          React.createElement('td', null, React.createElement('div', { className: 'row gap2' }, React.createElement(Tile, { icon: KIND_ICON[k.kind], color: 'var(--io-vector)', size: 28 }), React.createElement('span', { style: { fontWeight: 600 } }, k.name))),
          React.createElement('td', null, React.createElement('span', { className: 'chip chip-mono' }, k.kind)),
          React.createElement('td', { className: 'mono-sm fg-1' }, k.chunks || '—'),
          React.createElement('td', { className: 'mono-sm fg-1' }, k.size),
          React.createElement('td', null, k.status === 'processing'
            ? React.createElement('div', { className: 'row gap2' }, React.createElement('div', { className: 'progress', style: { width: 60 } }, React.createElement('i', { style: { width: k.prog + '%' } })), React.createElement('span', { className: 'mono-sm fg-2' }, k.prog + '%'))
            : React.createElement(StatusPill, { status: k.status })),
          React.createElement('td', { className: 'fg-2 t-caption' }, k.updated),
          React.createElement('td', null, React.createElement(Menu, { trigger: React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'more', size: 16 })), items: [{ icon: 'refresh', label: 'Reindex' }, { icon: 'eye', label: 'Preview chunks' }, { divider: true }, { icon: 'trash', label: 'Remove', danger: true }] }))))))) );
}

function KnowledgeQA() {
  const KIND = { faq: ['pill-info', 'FAQ'], error_workaround: ['pill-warn', 'Workaround'], canned_reply: ['pill-muted', 'Canned'] };
  return React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 } },
    DATA.QA_PAIRS.map(q => React.createElement('div', { key: q.id, className: 'card', style: { padding: 16 } },
      React.createElement('div', { className: 'row spread', style: { marginBottom: 10 } },
        React.createElement('span', { className: 'pill ' + KIND[q.kind][0] }, React.createElement('span', { className: 'dot' }), KIND[q.kind][1]),
        React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 'row gap1 fg-2 t-caption' }, React.createElement(Icon, { name: 'check', size: 13 }), q.upvotes), React.createElement('button', { className: 'iconbtn', style: { width: 24, height: 24 } }, React.createElement(Icon, { name: 'edit', size: 14 })))),
      React.createElement('div', { className: 'row gap2', style: { marginBottom: 8 } }, React.createElement(Icon, { name: 'n_qa', size: 15, style: { color: 'var(--io-vector)', flex: 'none', marginTop: 2 } }), React.createElement('div', { style: { fontSize: 14, fontWeight: 600, lineHeight: '19px' } }, q.q)),
      React.createElement('div', { className: 'fg-1', style: { fontSize: 13, lineHeight: '19px', paddingLeft: 23 } }, q.a),
      React.createElement('div', { className: 'divider', style: { margin: '12px 0 10px' } }),
      React.createElement('div', { className: 'row spread' }, React.createElement('div', { className: 'row gap1 wrap' }, q.tags.map(t => React.createElement('span', { key: t, className: 'chip', style: { height: 20 } }, t))), React.createElement('span', { className: 'fg-2 t-caption' }, 'used ' + q.used)))));
}

function SearchDebugger() {
  const [q, setQ] = useState('how do refunds work');
  const [mode, setMode] = useState('hybrid');
  const [ran, setRan] = useState(true);
  return React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: 20, alignItems: 'start' } },
    React.createElement('div', { className: 'card', style: { padding: 18 } },
      React.createElement('div', { className: 't-h1', style: { marginBottom: 14 } }, 'Query'),
      React.createElement(Field, { label: 'Search text' }, React.createElement('textarea', { className: 'textarea', rows: 2, value: q, onChange: e => setQ(e.target.value) })),
      React.createElement(Field, { label: 'Strategy' }, React.createElement(Segmented, { options: [{ value: 'vector', label: 'Vector' }, { value: 'fts', label: 'Keyword' }, { value: 'hybrid', label: 'Hybrid + rerank' }], value: mode, onChange: setMode })),
      React.createElement('div', { className: 'row gap4', style: { marginTop: 4 } }, React.createElement(Field, { label: 'Top K' }, React.createElement('input', { className: 'input', defaultValue: '5' })), React.createElement(Field, { label: 'Min score' }, React.createElement('input', { className: 'input', defaultValue: '0.65' }))),
      React.createElement('button', { className: 'btn btn-primary', style: { width: '100%', marginTop: 6 }, onClick: () => setRan(true) }, React.createElement(Icon, { name: 'search', size: 15 }), 'Run search')),
    React.createElement('div', null,
      React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } }, React.createElement('div', { className: 't-h1' }, 'Results'), React.createElement('span', { className: 'chip chip-mono' }, '4 hits · 38ms')),
      React.createElement('div', { className: 'col gap2' }, DATA.SEARCH_HITS.map((h, i) => React.createElement('div', { key: i, className: 'card', style: { padding: 14 } },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 8 } },
          React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 'badge badge-accent' }, '#' + (i + 1)), React.createElement('span', { className: 'mono-sm', style: { fontWeight: 600, color: 'var(--fg-0)' } }, h.title)),
          React.createElement('span', { className: 'pill ' + (h.fused > 0.8 ? 'pill-ok' : 'pill-muted') }, h.fused.toFixed(2))),
        React.createElement('div', { className: 'fg-1', style: { fontSize: 12.5, lineHeight: '17px', marginBottom: 10 } }, h.text),
        React.createElement('div', { className: 'row gap4' },
          [['vector', h.vec, 'var(--io-vector)'], ['keyword', h.fts, 'var(--signal)'], ['fused', h.fused, 'var(--accent)']].map(s => React.createElement('div', { key: s[0], className: 'col gap1', style: { flex: 1 } },
            React.createElement('div', { className: 'row spread' }, React.createElement('span', { style: { fontSize: 10, color: 'var(--fg-2)', textTransform: 'uppercase', letterSpacing: '.04em' } }, s[0]), React.createElement('span', { className: 'mono-sm', style: { color: s[2] } }, s[1].toFixed(2))),
            React.createElement('div', { className: 'progress', style: { height: 4 } }, React.createElement('i', { style: { width: s[1] * 100 + '%', background: s[2] } })))))))) ));
}

Object.assign(window, { KnowledgeScreen, KnowledgeSources, KnowledgeQA, SearchDebugger });
