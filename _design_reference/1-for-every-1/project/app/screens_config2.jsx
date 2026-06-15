/* Forge: shared ListScaffold + Tool Builder (projection meter) + Auth Providers. */

function ListScaffold({ title, sub, action, children }) {
  return React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '22px 28px' } },
    React.createElement('div', { className: 'fade-up', style: { maxWidth: 1120, margin: '0 auto' } },
      React.createElement('div', { className: 'row spread', style: { marginBottom: 18 } },
        React.createElement('div', null, React.createElement('div', { className: 't-display', style: { fontSize: 21 } }, title), sub && React.createElement('div', { className: 'fg-1', style: { marginTop: 3, maxWidth: 560 } }, sub)),
        action),
      children));
}

/* ============ TOOL BUILDER ============ */
function ToolBuilderScreen({ tool, onBack }) {
  const t = tool || DATA.TOOLS.find(x => x.id === 't_refund');
  const [tab, setTab] = useState('request');
  const [tested, setTested] = useState(false);
  const [testing, setTesting] = useState(false);
  const [projExpr, setProjExpr] = useState('{ id: id, status: status, amount: total.amount, currency: total.currency }');
  const [animKey, setAnimKey] = useState(0);

  const runTest = () => { setTesting(true); setTested(false); setTimeout(() => { setTesting(false); setTested(true); setAnimKey(k => k + 1); }, 1100); };

  const rawJson = `{
  "id": "ord_8842",
  "status": "shipped",
  "customer": { "id": "cus_204", "email": "j@acme.dev", "tier": "pro" },
  "line_items": [
    { "sku": "SKU-1", "name": "Widget Pro", "qty": 2, "price": 4900 },
    { "sku": "SKU-2", "name": "Care Plan", "qty": 1, "price": 1200 }
  ],
  "total": { "amount": 11000, "currency": "USD", "tax": 880 },
  "shipping": { "carrier": "UPS", "tracking": "1Z…", "eta": "2026-06-09" },
  "_meta": { "trace": "…", "raw_provider_blob": "…1.1KB…" }
}`;
  const projJson = `{
  "id": "ord_8842",
  "status": "shipped",
  "amount": 11000,
  "currency": "USD"
}`;

  return React.createElement('div', { className: 'col', style: { flex: 1, minHeight: 0 } },
    React.createElement('div', { className: 'row spread', style: { padding: '14px 22px', borderBottom: '1px solid var(--line)', flex: 'none' } },
      React.createElement('div', { className: 'row gap3' },
        React.createElement(Tile, { icon: DATA.KIND_ICON[t.kind], color: 'var(--io-tool)', size: 40, glow: true }),
        React.createElement('div', null,
          React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 't-display mono', style: { fontSize: 18 } }, t.name), React.createElement('span', { className: 'chip chip-mono' }, DATA.KIND_LABEL[t.kind]), React.createElement(StatusPill, { status: tested ? 'pass' : t.tested })),
          React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 2 } }, t.desc))),
      React.createElement('div', { className: 'row gap2' },
        React.createElement('button', { className: 'btn btn-secondary', onClick: runTest, disabled: testing }, React.createElement(Icon, { name: testing ? 'refresh' : 'validate', size: 15, style: testing ? { animation: 'spin 1s linear infinite' } : {} }), testing ? 'Testing…' : 'Test'),
        React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'save', size: 15 }), 'Save v' + (t.version + 1)))),
    React.createElement('div', { style: { flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 0 } },
      /* LEFT: config */
      React.createElement('div', { className: 'col', style: { borderRight: '1px solid var(--line)', minHeight: 0 } },
        React.createElement('div', { style: { padding: '0 18px', borderBottom: '1px solid var(--line)' } }, React.createElement(Tabs, { tabs: [{ value: 'request', label: 'Request' }, { value: 'schema', label: 'Input schema' }, { value: 'projection', label: 'Projection' }, { value: 'auth', label: 'Auth' }], value: tab, onChange: setTab })),
        React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 18 } },
          tab === 'request' && React.createElement('div', { className: 'fade-in' },
            React.createElement(Field, { label: 'Method & URL' },
              React.createElement('div', { className: 'row gap2' },
                React.createElement('div', { style: { width: 92, position: 'relative' } }, React.createElement('select', { className: 'select', defaultValue: t.method || 'POST' }, ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map(m => React.createElement('option', { key: m }, m))), React.createElement(Icon, { name: 'chevdown', size: 13, style: { position: 'absolute', right: 8, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } })),
                React.createElement('input', { className: 'input mono', defaultValue: t.url, style: { flex: 1 } }))),
            React.createElement(Field, { label: 'Headers' }, React.createElement(KVEditor, { rows: [['Content-Type', 'application/json'], ['X-CSRF-Token', '{{ auth.orders_session.csrf }}']] })),
            React.createElement(Field, { label: 'Body template', help: 'Mustache over validated tool input + run state.' },
              React.createElement('textarea', { className: 'textarea mono', rows: 4, defaultValue: '{\n  "amount": {{ input.amount }},\n  "reason": "{{ input.reason }}"\n}' }))),
          tab === 'schema' && React.createElement('div', { className: 'fade-in' },
            React.createElement('div', { className: 'fg-1', style: { marginBottom: 12 } }, 'What the model is allowed to pass. Becomes the JSON Schema in the tool spec.'),
            ['order_id · string · required', 'amount · integer · required', 'reason · string · optional'].map((s, i) =>
              React.createElement('div', { key: i, className: 'row gap2', style: { padding: '10px 12px', border: '1px solid var(--line)', borderRadius: 9, marginBottom: 8 } },
                React.createElement(Icon, { name: 'drag', size: 14, style: { color: 'var(--fg-2)' } }),
                React.createElement('span', { className: 'mono-sm grow', style: { fontWeight: 600 } }, s.split(' · ')[0]),
                React.createElement('span', { className: 'typechip' }, s.split(' · ')[1]),
                s.includes('required') && React.createElement('span', { className: 'pill pill-err', style: { height: 17 } }, 'required'))),
            React.createElement('button', { className: 'btn btn-ghost btn-sm' }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Add field')),
          tab === 'projection' && React.createElement('div', { className: 'fade-in' },
            React.createElement('div', { className: 'card', style: { padding: 12, marginBottom: 14, background: 'var(--signal-glow)', borderColor: 'transparent' } },
              React.createElement('div', { className: 'row gap2' }, React.createElement(Icon, { name: 'bolt', size: 16, style: { color: 'var(--signal)' } }), React.createElement('span', { style: { fontSize: 12.5, fontWeight: 600, color: 'var(--signal)' } }, 'Projection trims the raw response before it reaches the model context.'))),
            React.createElement(Field, { label: 'Projection expression', help: 'JMESPath. Only projected keys count toward context tokens.' },
              React.createElement('textarea', { className: 'textarea mono', rows: 3, value: projExpr, onChange: e => { setProjExpr(e.target.value); setAnimKey(k => k + 1); } })),
            React.createElement(Field, { label: 'On error' }, React.createElement('div', { style: { position: 'relative' } }, React.createElement('select', { className: 'select', defaultValue: 'return_message' }, [['return_message', 'Return error message to model'], ['raise', 'Raise & stop run'], ['retry', 'Retry (handled by middleware)']].map(o => React.createElement('option', { key: o[0], value: o[0] }, o[1]))), React.createElement(Icon, { name: 'chevdown', size: 13, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } })))),
          tab === 'auth' && React.createElement('div', { className: 'fade-in' },
            React.createElement(Field, { label: 'Auth provider', help: 'Reusable credential + session strategy. Secrets resolved at call time.' },
              React.createElement('div', { style: { position: 'relative' } }, React.createElement('select', { className: 'select', defaultValue: t.auth || '' }, [React.createElement('option', { key: 'none', value: '' }, 'None'), ...DATA.AUTH_PROVIDERS.map(p => React.createElement('option', { key: p.id, value: p.id }, p.name + ' · ' + p.kind))]), React.createElement(Icon, { name: 'chevdown', size: 13, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } }))),
            t.auth && React.createElement('div', { className: 'card', style: { padding: 14, background: 'var(--bg-3)', borderColor: 'var(--line)' } },
              React.createElement('div', { className: 'row spread', style: { marginBottom: 8 } }, React.createElement('span', { className: 't-h3 mono' }, t.auth), React.createElement(StatusPill, { status: 'pass' })),
              React.createElement('div', { className: 'col gap1 mono-sm fg-1' }, ['strategy · csrf_session', 'login · POST /auth/session', 'csrf header · X-CSRF-Token', 'session ttl · 1800s · auto-refresh'].map((s, i) => React.createElement('div', { key: i }, s)))))) ),
      /* RIGHT: the projection meter + before/after (signature) */
      React.createElement('div', { className: 'col', style: { minHeight: 0, background: 'var(--bg-0)' } },
        React.createElement('div', { className: 'row spread', style: { padding: '12px 18px', borderBottom: '1px solid var(--line)' } },
          React.createElement('div', { className: 'row gap2' }, React.createElement(Icon, { name: 'play', size: 15, style: { color: 'var(--signal)' } }), React.createElement('span', { className: 't-h2' }, 'Live response')),
          React.createElement('span', { className: 'chip chip-mono' }, '200 OK · 142ms')),
        React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 18 } },
          /* THE TOKEN METER — signature moment */
          React.createElement('div', { className: 'card', style: { padding: 16, marginBottom: 16 } },
            React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } },
              React.createElement('span', { className: 't-h2' }, 'Context cost'),
              React.createElement('button', { className: 'btn btn-ghost btn-sm', onClick: () => setAnimKey(k => k + 1) }, React.createElement(Icon, { name: 'refresh', size: 13 }), 'Replay')),
            React.createElement(BigTokenMeter, { raw: 1240, projected: 92, animateKey: animKey })),
          React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 } },
            React.createElement('div', null,
              React.createElement('div', { className: 'row gap2', style: { marginBottom: 6 } }, React.createElement('span', { className: 't-micro' }, 'Raw response'), React.createElement('span', { className: 'chip', style: { height: 18, color: 'var(--fg-2)' } }, '1,240 tok')),
              React.createElement(CodeBlock, { code: rawJson, maxHeight: 320 })),
            React.createElement('div', null,
              React.createElement('div', { className: 'row gap2', style: { marginBottom: 6 } }, React.createElement('span', { className: 't-micro', style: { color: 'var(--signal)' } }, 'Projected → model'), React.createElement('span', { className: 'pill pill-ok', style: { height: 18 } }, '92 tok')),
              React.createElement('div', { style: { borderRadius: 8, boxShadow: '0 0 0 1px var(--signal), 0 0 18px var(--signal-glow)' } }, React.createElement(CodeBlock, { code: projJson, maxHeight: 320 })))))) ));
}

/* Big token meter for the tool builder hero */
function BigTokenMeter({ raw, projected, animateKey }) {
  const [phase, setPhase] = useState('raw');
  useEffect(() => { setPhase('raw'); const t = setTimeout(() => setPhase('proj'), 480); return () => clearTimeout(t); }, [animateKey]);
  const pct = phase === 'proj' ? (projected / raw) * 100 : 100;
  const saved = Math.round((1 - projected / raw) * 100);
  return React.createElement('div', { className: 'col gap3' },
    React.createElement('div', { style: { position: 'relative', height: 38, borderRadius: 10, background: 'var(--bg-3)', overflow: 'hidden', border: '1px solid var(--line)' } },
      React.createElement('div', { style: { position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', paddingLeft: 12, fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' } }, 'raw 1,240 tok'),
      React.createElement('div', { style: { position: 'absolute', top: 0, bottom: 0, left: 0, width: pct + '%', borderRadius: 10, background: phase === 'proj' ? 'linear-gradient(90deg, var(--signal-dim), var(--signal))' : 'linear-gradient(90deg, var(--accent-dim), var(--accent))', transition: 'width .7s var(--ease), background .4s', boxShadow: phase === 'proj' ? '0 0 16px var(--signal-glow)' : 'none', display: 'flex', alignItems: 'center', paddingLeft: 12, overflow: 'hidden' } },
        React.createElement('span', { style: { fontSize: 12, fontWeight: 700, color: '#fff', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' } }, phase === 'proj' ? projected + ' tok' : '1,240 tok'))),
    React.createElement('div', { className: 'row spread' },
      React.createElement('span', { className: 'fg-2 t-caption' }, phase === 'proj' ? 'Projected payload sent to the model' : 'Full provider response'),
      React.createElement('span', { className: 'pill pill-ok' }, React.createElement(Icon, { name: 'bolt', size: 12 }), saved + '% fewer tokens · ~93% cost saved')));
}

function KVEditor({ rows }) {
  return React.createElement('div', { className: 'col gap2' },
    rows.map((r, i) => React.createElement('div', { key: i, className: 'row gap2' },
      React.createElement('input', { className: 'input mono', defaultValue: r[0], style: { flex: '0 0 36%' } }),
      React.createElement('input', { className: 'input mono', defaultValue: r[1], style: { flex: 1 } }),
      React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'x', size: 14 })))),
    React.createElement('button', { className: 'btn btn-ghost btn-sm', style: { alignSelf: 'flex-start' } }, React.createElement(Icon, { name: 'plus', size: 13 }), 'Add header'));
}

/* ============ AUTH PROVIDERS ============ */
function AuthProvidersScreen() {
  const [sel, setSel] = useState(DATA.AUTH_PROVIDERS[0]);
  const [tab, setTab] = useState('config');
  const KIND = { csrf_session: 'CSRF + session', oauth2_client_credentials: 'OAuth2 client-creds', bearer: 'Bearer token', basic: 'Basic auth' };
  return React.createElement('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
    React.createElement('div', { style: { width: 280, flex: 'none', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)' } },
      React.createElement('div', { className: 'row spread', style: { padding: '14px 16px', borderBottom: '1px solid var(--line)' } }, React.createElement('div', { className: 't-h1' }, 'Auth Providers'), React.createElement('button', { className: 'btn btn-primary btn-sm' }, React.createElement(Icon, { name: 'plus', size: 14 }))),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 8 } }, DATA.AUTH_PROVIDERS.map(p => {
        const on = sel.id === p.id;
        return React.createElement('button', { key: p.id, onClick: () => setSel(p), className: 'col', style: { width: '100%', textAlign: 'left', padding: '11px 12px', borderRadius: 9, marginBottom: 4, border: '1px solid ' + (on ? 'var(--accent)' : 'transparent'), background: on ? 'var(--accent-glow)' : 'transparent', cursor: 'pointer', gap: 4 } },
          React.createElement('div', { className: 'row spread' }, React.createElement('span', { className: 'mono-sm', style: { fontWeight: 700, color: 'var(--fg-0)' } }, p.name), React.createElement(StatusPill, { status: p.tested })),
          React.createElement('div', { className: 'row gap2', style: { fontSize: 11, color: 'var(--fg-2)' } }, React.createElement('span', null, KIND[p.kind]), React.createElement('span', null, '· ' + p.usedBy + ' tools')));
      }))),
    React.createElement('div', { className: 'grow scroll-y', style: { padding: 24 } },
      React.createElement('div', { style: { maxWidth: 620 } },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 18 } },
          React.createElement('div', { className: 'row gap3' }, React.createElement(Tile, { icon: 'auth', color: 'var(--accent)', size: 40, glow: true }),
            React.createElement('div', null, React.createElement('div', { className: 't-display mono', style: { fontSize: 18 } }, sel.name), React.createElement('div', { className: 'fg-2 t-caption' }, KIND[sel.kind] + ' · ttl ' + sel.ttl + 's'))),
          React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'validate', size: 15 }), 'Test connection')),
        React.createElement('div', { className: 'card', style: { padding: 18, marginBottom: 16 } },
          React.createElement('div', { className: 't-h2', style: { marginBottom: 14 } }, 'Strategy'),
          React.createElement(Field, { label: 'Type' }, React.createElement('div', { style: { position: 'relative' } }, React.createElement('select', { className: 'select', value: sel.kind, onChange: () => {} }, Object.entries(KIND).map(([k, v]) => React.createElement('option', { key: k, value: k }, v))), React.createElement(Icon, { name: 'chevdown', size: 13, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } }))),
          sel.kind === 'csrf_session' && React.createElement('div', null,
            React.createElement('div', { className: 'row gap4' }, React.createElement(Field, { label: 'Login URL' }, React.createElement('input', { className: 'input mono', defaultValue: 'https://api.acme.dev/auth/session' })), React.createElement(Field, { label: 'Method' }, React.createElement('input', { className: 'input mono', defaultValue: 'POST' }))),
            React.createElement('div', { className: 'row gap4' }, React.createElement(Field, { label: 'CSRF header' }, React.createElement('input', { className: 'input mono', defaultValue: 'X-CSRF-Token' })), React.createElement(Field, { label: 'CSRF JSON path' }, React.createElement('input', { className: 'input mono', defaultValue: 'data.csrfToken' }))),
            React.createElement(Field, { label: 'Session TTL', help: 'Auto re-login on expiry or 401.' }, React.createElement('input', { className: 'input mono', defaultValue: '1800' })))),
        React.createElement('div', { className: 'card', style: { padding: 18 } },
          React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } }, React.createElement('div', { className: 't-h2' }, 'Credentials'), React.createElement('span', { className: 'chip', style: { color: 'var(--fg-2)' } }, React.createElement(Icon, { name: 'secret', size: 12 }), 'from secret store')),
          React.createElement(Field, { label: 'Username' }, React.createElement('input', { className: 'input mono', defaultValue: 'svc_orders' })),
          React.createElement(Field, { label: 'Password / secret ref' }, React.createElement('div', { className: 'row gap2' }, React.createElement('input', { className: 'input mono', type: 'password', defaultValue: 'orders_api_creds:v3', style: { flex: 1 } }), React.createElement('button', { className: 'iconbtn', style: { border: '1px solid var(--line-strong)' } }, React.createElement(Icon, { name: 'eye', size: 15 })))))) ));
}

Object.assign(window, { ListScaffold, ToolBuilderScreen, BigTokenMeter, KVEditor, AuthProvidersScreen });
