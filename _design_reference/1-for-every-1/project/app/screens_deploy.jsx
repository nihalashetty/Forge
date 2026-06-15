/* Forge — Deploy: Widget configurator, Connect (MCP), Settings & Secrets. */

/* ============ WIDGET ============ */
function WidgetScreen() {
  const [cfg, setCfg] = useState({ accent: '#E8541F', title: 'Acme Support', greeting: 'Hi! How can I help with your order today?', position: 'right', launcher: 'bubble', avatar: true, branding: true });
  const set = (k, v) => setCfg(c => ({ ...c, [k]: v }));
  const swatches = ['#E8541F', '#2F6FD0', '#0E9C90', '#7C5CE0', '#11161C'];
  return React.createElement('div', { style: { flex: 1, display: 'flex', minHeight: 0 } },
    /* config */
    React.createElement('div', { style: { width: 360, flex: 'none', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)' } },
      React.createElement('div', { className: 'row spread', style: { padding: '16px 18px', borderBottom: '1px solid var(--line)' } }, React.createElement('div', null, React.createElement('div', { className: 't-h1' }, 'Chat Widget'), React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 2 } }, 'Embeddable web chat')), React.createElement('span', { className: 'pill pill-ok' }, React.createElement('span', { className: 'dot' }), 'Live')),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: 18 } },
        React.createElement('div', { className: 't-micro', style: { marginBottom: 10 } }, 'Appearance'),
        React.createElement(Field, { label: 'Accent color' }, React.createElement('div', { className: 'row gap2' }, swatches.map(s => React.createElement('button', { key: s, onClick: () => set('accent', s), style: { width: 30, height: 30, borderRadius: 8, background: s, cursor: 'pointer', border: '2px solid ' + (cfg.accent === s ? 'var(--fg-0)' : 'transparent'), boxShadow: cfg.accent === s ? '0 0 0 2px var(--bg-1) inset' : 'none' } })))),
        React.createElement(Field, { label: 'Header title' }, React.createElement('input', { className: 'input', value: cfg.title, onChange: e => set('title', e.target.value) })),
        React.createElement(Field, { label: 'Greeting message' }, React.createElement('textarea', { className: 'textarea', rows: 2, value: cfg.greeting, onChange: e => set('greeting', e.target.value) })),
        React.createElement(Field, { label: 'Launcher' }, React.createElement(Segmented, { options: [{ value: 'bubble', label: 'Bubble' }, { value: 'bar', label: 'Bar' }, { value: 'custom', label: 'Custom' }], value: cfg.launcher, onChange: v => set('launcher', v) })),
        React.createElement(Field, { label: 'Position' }, React.createElement(Segmented, { options: [{ value: 'left', label: 'Bottom-left' }, { value: 'right', label: 'Bottom-right' }], value: cfg.position, onChange: v => set('position', v) })),
        React.createElement('div', { className: 'divider', style: { margin: '14px 0' } }),
        React.createElement('div', { className: 't-micro', style: { marginBottom: 6 } }, 'Behaviour'),
        [['avatar', 'Show agent avatar'], ['branding', 'Show "Powered by Forge"']].map(o => React.createElement('div', { key: o[0], className: 'row spread', style: { padding: '9px 0' } }, React.createElement('span', { style: { fontSize: 13 } }, o[1]), React.createElement(Toggle, { on: cfg[o[0]], onChange: v => set(o[0], v) }))),
        React.createElement(Field, { label: 'Allowed domains', help: 'CORS — the widget only loads on these origins.' }, React.createElement('input', { className: 'input mono', defaultValue: 'acme.dev, *.acme.dev' })))),
    /* preview */
    React.createElement('div', { className: 'grow col', style: { minHeight: 0, background: 'var(--bg-0)' } },
      React.createElement('div', { className: 'row spread', style: { padding: '12px 22px', borderBottom: '1px solid var(--line)' } }, React.createElement('span', { className: 't-h2' }, 'Live preview'), React.createElement('div', { className: 'row gap2' }, React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'external', size: 15 }), 'Open standalone'), React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'save', size: 15 }), 'Publish'))),
      React.createElement('div', { className: 'grow', style: { position: 'relative', overflow: 'hidden', padding: 24 } },
        /* faux website */
        React.createElement('div', { style: { position: 'absolute', inset: 24, borderRadius: 12, background: 'var(--bg-2)', border: '1px solid var(--line)', overflow: 'hidden' } },
          React.createElement('div', { className: 'row gap2', style: { padding: '12px 16px', borderBottom: '1px solid var(--line)' } }, React.createElement('div', { style: { width: 22, height: 22, borderRadius: 6, background: 'var(--bg-3)' } }), React.createElement('div', { style: { width: 80, height: 9, borderRadius: 4, background: 'var(--bg-3)' } }), React.createElement('div', { className: 'grow' }), [1, 2, 3].map(i => React.createElement('div', { key: i, style: { width: 44, height: 9, borderRadius: 4, background: 'var(--bg-3)' } }))),
          React.createElement('div', { style: { padding: 28 } }, React.createElement('div', { style: { width: 260, height: 22, borderRadius: 6, background: 'var(--bg-3)', marginBottom: 12 } }), React.createElement('div', { style: { width: 420, height: 11, borderRadius: 4, background: 'var(--bg-3)', marginBottom: 7 } }), React.createElement('div', { style: { width: 380, height: 11, borderRadius: 4, background: 'var(--bg-3)' } }))),
        /* widget panel */
        React.createElement('div', { style: { position: 'absolute', bottom: 40, [cfg.position]: 40, width: 320, display: 'flex', flexDirection: 'column', alignItems: cfg.position === 'left' ? 'flex-start' : 'flex-end', gap: 14 } },
          React.createElement('div', { className: 'fade-up card', style: { width: '100%', overflow: 'hidden', boxShadow: 'var(--sh-pop)' } },
            React.createElement('div', { className: 'row gap2', style: { padding: '14px 16px', background: cfg.accent, color: '#fff' } }, cfg.avatar && React.createElement('div', { style: { width: 30, height: 30, borderRadius: '50%', background: 'rgba(255,255,255,.25)', display: 'flex', alignItems: 'center', justifyContent: 'center' } }, React.createElement(Icon, { name: 'n_agent', size: 16 })), React.createElement('div', { className: 'grow' }, React.createElement('div', { style: { fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-display)' } }, cfg.title), React.createElement('div', { style: { fontSize: 11, opacity: .85 } }, 'Typically replies instantly')), React.createElement(Icon, { name: 'minus', size: 18 })),
            React.createElement('div', { style: { padding: 16, display: 'flex', flexDirection: 'column', gap: 12, minHeight: 180, background: 'var(--bg-1)' } },
              React.createElement('div', { className: 'row gap2', style: { alignItems: 'flex-start' } }, cfg.avatar && React.createElement('div', { style: { width: 24, height: 24, borderRadius: '50%', background: 'var(--bg-3)', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', color: cfg.accent } }, React.createElement(Icon, { name: 'n_agent', size: 13 })), React.createElement('div', { style: { padding: '9px 12px', borderRadius: 12, borderTopLeftRadius: 3, background: 'var(--bg-3)', fontSize: 12.5, lineHeight: '17px', maxWidth: 220 } }, cfg.greeting)),
              React.createElement('div', { style: { alignSelf: 'flex-end', padding: '9px 12px', borderRadius: 12, borderTopRightRadius: 3, background: cfg.accent, color: '#fff', fontSize: 12.5 } }, "Where's my order?")),
            React.createElement('div', { className: 'row gap2', style: { padding: 12, borderTop: '1px solid var(--line)', background: 'var(--bg-1)' } }, React.createElement('div', { className: 'grow', style: { height: 34, borderRadius: 9, background: 'var(--bg-3)', display: 'flex', alignItems: 'center', padding: '0 12px', fontSize: 12.5, color: 'var(--fg-2)' } }, 'Message…'), React.createElement('div', { style: { width: 34, height: 34, borderRadius: 9, background: cfg.accent, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff' } }, React.createElement(Icon, { name: 'play', size: 15 }))),
            cfg.branding && React.createElement('div', { style: { padding: '6px', textAlign: 'center', fontSize: 10, color: 'var(--fg-2)', background: 'var(--bg-1)', borderTop: '1px solid var(--line)' } }, 'Powered by ', React.createElement('b', null, 'Forge'))),
          React.createElement('div', { style: { width: cfg.launcher === 'bar' ? 'auto' : 56, height: 56, padding: cfg.launcher === 'bar' ? '0 20px' : 0, borderRadius: cfg.launcher === 'bar' ? 28 : '50%', background: cfg.accent, boxShadow: '0 8px 24px ' + cfg.accent + '66', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, color: '#fff' } }, React.createElement(Icon, { name: 'msg', size: 24 }), cfg.launcher === 'bar' && React.createElement('span', { style: { fontWeight: 700, fontFamily: 'var(--font-display)' } }, 'Chat')))),
      React.createElement('div', { style: { padding: '14px 22px', borderTop: '1px solid var(--line)' } }, React.createElement('div', { className: 't-micro', style: { marginBottom: 6 } }, 'Embed snippet'), React.createElement(CodeBlock, { code: `<script src="https://cdn.forge.sh/widget.js"\n  data-project="customer-support"\n  data-workflow="support-router"\n  data-accent="${cfg.accent}"\n  data-position="${cfg.position}" async><\/script>`, lang: 'html' }))));
}

/* ============ CONNECT (MCP) ============ */
function ConnectScreen() {
  const [exposed, setExposed] = useState({ get_order: true, get_invoice: true, submit_refund: false, search_catalog: true });
  return React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '24px 28px' } },
    React.createElement('div', { style: { maxWidth: 880, margin: '0 auto' } },
      React.createElement('div', { className: 'row gap3', style: { marginBottom: 22 } }, React.createElement(Tile, { icon: 'connect', color: 'var(--signal)', size: 46, glow: true }), React.createElement('div', null, React.createElement('div', { className: 't-display', style: { fontSize: 21 } }, 'Connect this project'), React.createElement('div', { className: 'fg-1', style: { marginTop: 3 } }, 'Expose tools and workflows as an MCP server, or call them over REST. Consumers authenticate with a scoped key.'))),
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 20 } },
        [['MCP Server', 'connect', 'var(--signal)', 'mcp.forge.sh/customer-support', 'Streamable HTTP · 3 tools'], ['REST API', 'globe', 'var(--io-json)', 'api.forge.sh/v1/customer-support', 'OpenAPI 3.1 · run + resume']].map((c, i) =>
          React.createElement('div', { key: i, className: 'card', style: { padding: 16 } },
            React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } }, React.createElement(Tile, { icon: c[1], color: c[2], size: 34 }), React.createElement('span', { className: 'pill pill-ok' }, React.createElement('span', { className: 'dot' }), 'Enabled')),
            React.createElement('div', { className: 't-h2' }, c[0]), React.createElement('div', { className: 'fg-2 t-caption', style: { margin: '2px 0 10px' } }, c[4]),
            React.createElement('div', { className: 'row gap2', style: { background: 'var(--bg-0)', border: '1px solid var(--line)', borderRadius: 8, padding: '7px 10px' } }, React.createElement('span', { className: 'mono-sm grow truncate', style: { color: 'var(--fg-1)' } }, c[3]), React.createElement('button', { className: 'iconbtn', style: { width: 24, height: 24 } }, React.createElement(Icon, { name: 'copy', size: 14 })))))),
      React.createElement('div', { className: 'card', style: { padding: 18, marginBottom: 20 } },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 6 } }, React.createElement('div', { className: 't-h1' }, 'Exposed tools'), React.createElement('span', { className: 'chip chip-mono' }, Object.values(exposed).filter(Boolean).length + ' of 4 enabled')),
        React.createElement('div', { className: 'fg-2 t-caption', style: { marginBottom: 14 } }, 'Only enabled tools appear in the MCP tool list. Sensitive tools should stay off or require approval.'),
        React.createElement('div', { className: 'col gap2' }, DATA.TOOLS.filter(t => exposed[t.name] !== undefined).map(t => React.createElement('div', { key: t.id, className: 'row gap3', style: { padding: '10px 12px', borderRadius: 9, border: '1px solid var(--line)' } },
          React.createElement(Icon, { name: DATA.KIND_ICON[t.kind], size: 16, style: { color: 'var(--io-tool)' } }),
          React.createElement('div', { className: 'grow' }, React.createElement('span', { className: 'mono-sm', style: { fontWeight: 600 } }, t.name), t.name === 'submit_refund' && React.createElement('span', { className: 'pill pill-warn', style: { marginLeft: 8, height: 17 } }, 'sensitive')),
          React.createElement('span', { className: 'mono-sm fg-2' }, 'GET'),
          React.createElement(Toggle, { on: exposed[t.name], onChange: v => setExposed(s => ({ ...s, [t.name]: v })), signal: true })))) ),
      React.createElement('div', { className: 'card', style: { padding: 18 } },
        React.createElement('div', { className: 't-h1', style: { marginBottom: 4 } }, 'Client config'),
        React.createElement('div', { className: 'fg-2 t-caption', style: { marginBottom: 12 } }, 'Drop into any MCP-compatible client (Claude Desktop, IDE agents, etc.)'),
        React.createElement(CodeBlock, { code: '{\n  "mcpServers": {\n    "acme-support": {\n      "url": "https://mcp.forge.sh/customer-support",\n      "headers": { "Authorization": "Bearer fk_live_••••2f9a" }\n    }\n  }\n}' }))));
}

/* ============ SETTINGS & SECRETS ============ */
function SettingsScreen() {
  const [tab, setTab] = useState('secrets');
  const KIND = { csrf_session: 'var(--accent)', api_key: 'var(--io-json)', oauth2: 'var(--signal)', bearer: 'var(--io-vector)' };
  return React.createElement('div', { className: 'col', style: { flex: 1, minHeight: 0 } },
    React.createElement('div', { style: { padding: '18px 28px 0' } },
      React.createElement('div', { className: 't-display', style: { fontSize: 21, marginBottom: 14 } }, 'Settings'),
      React.createElement(Tabs, { tabs: [{ value: 'secrets', label: 'Secrets' }, { value: 'general', label: 'General' }, { value: 'members', label: 'Members' }, { value: 'audit', label: 'Audit log' }], value: tab, onChange: setTab })),
    React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '20px 28px' } },
      React.createElement('div', { style: { maxWidth: 880, margin: '0 auto' } },
        tab === 'secrets' && React.createElement('div', null,
          React.createElement('div', { className: 'card', style: { padding: 14, marginBottom: 16, background: 'var(--info-bg)', borderColor: 'transparent' } }, React.createElement('div', { className: 'row gap2' }, React.createElement(Icon, { name: 'secret', size: 16, style: { color: 'var(--info)' } }), React.createElement('span', { style: { fontSize: 12.5, color: 'var(--info)' } }, 'Secrets are encrypted at rest and never returned to the UI. Tools reference them by name; only the runtime can decrypt.'))),
          React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } }, React.createElement('div', { className: 't-h1' }, 'Secret store'), React.createElement('button', { className: 'btn btn-primary' }, React.createElement(Icon, { name: 'plus', size: 15 }), 'Add secret')),
          React.createElement('div', { className: 'card', style: { overflow: 'hidden' } }, React.createElement('table', { className: 'tbl' },
            React.createElement('thead', null, React.createElement('tr', null, ['Name', 'Type', 'Value', 'Version', 'Last used', ''].map((h, i) => React.createElement('th', { key: i }, h)))),
            React.createElement('tbody', null, DATA.SECRETS.map(s => React.createElement('tr', { key: s.id, className: 'row' },
              React.createElement('td', null, React.createElement('div', { className: 'row gap2' }, React.createElement('div', { style: { width: 8, height: 8, borderRadius: 2, background: KIND[s.kind] || 'var(--fg-2)' } }), React.createElement('span', { className: 'mono-sm', style: { fontWeight: 600 } }, s.name))),
              React.createElement('td', null, React.createElement('span', { className: 'chip chip-mono' }, s.kind)),
              React.createElement('td', { className: 'mono-sm fg-2' }, '••••••••••••'),
              React.createElement('td', { className: 'mono-sm fg-1' }, 'v' + s.version),
              React.createElement('td', { className: 'fg-2 t-caption' }, s.used),
              React.createElement('td', null, React.createElement(Menu, { trigger: React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'more', size: 16 })), items: [{ icon: 'rotate', label: 'Rotate' }, { icon: 'edit', label: 'Update value' }, { divider: true }, { icon: 'trash', label: 'Delete', danger: true }] })))))) ),
        tab === 'general' && React.createElement('div', { style: { maxWidth: 540 } },
          React.createElement('div', { className: 'card', style: { padding: 18, marginBottom: 16 } }, React.createElement('div', { className: 't-h1', style: { marginBottom: 14 } }, 'Project'), React.createElement(Field, { label: 'Name' }, React.createElement('input', { className: 'input', defaultValue: 'Customer Support' })), React.createElement(Field, { label: 'Slug', help: 'Used in API + MCP URLs.' }, React.createElement('input', { className: 'input mono', defaultValue: 'customer-support' })), React.createElement(Field, { label: 'Default model' }, React.createElement('div', { style: { position: 'relative' } }, React.createElement('select', { className: 'select', defaultValue: 'anthropic:claude-sonnet-4-6' }, DATA.MODELS.map(m => React.createElement('option', { key: m.id, value: m.id }, m.name))), React.createElement(Icon, { name: 'chevdown', size: 13, style: { position: 'absolute', right: 9, top: 9, pointerEvents: 'none', color: 'var(--fg-2)' } })))),
          React.createElement('div', { className: 'card', style: { padding: 18, border: '1px solid var(--err)' } }, React.createElement('div', { className: 't-h2', style: { color: 'var(--err)', marginBottom: 6 } }, 'Danger zone'), React.createElement('div', { className: 'row spread' }, React.createElement('div', null, React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, 'Delete project'), React.createElement('div', { className: 'fg-2 t-caption' }, 'Removes all workflows, tools, and traces.')), React.createElement('button', { className: 'btn btn-danger' }, 'Delete')))),
        tab === 'members' && React.createElement('div', { className: 'card', style: { overflow: 'hidden' } }, React.createElement('table', { className: 'tbl' },
          React.createElement('thead', null, React.createElement('tr', null, ['Member', 'Role', 'Last active', ''].map((h, i) => React.createElement('th', { key: i }, h)))),
          React.createElement('tbody', null, [['Riley Cho', 'riley@acme.dev', 'Owner', 'now'], ['Sam Okafor', 'sam@acme.dev', 'Editor', '2h ago'], ['Devon Park', 'devon@acme.dev', 'Viewer', '1d ago']].map((m, i) => React.createElement('tr', { key: i, className: 'row' },
            React.createElement('td', null, React.createElement('div', { className: 'row gap2' }, React.createElement(Avatar, { name: m[0], size: 28 }), React.createElement('div', null, React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, m[0]), React.createElement('div', { className: 'fg-2 t-caption' }, m[1])))),
            React.createElement('td', null, React.createElement('span', { className: 'chip' }, m[2])), React.createElement('td', { className: 'fg-2 t-caption' }, m[3]), React.createElement('td', null, React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'more', size: 16 }))))))),
        tab === 'audit' && React.createElement('div', { className: 'card', style: { overflow: 'hidden' } }, React.createElement('table', { className: 'tbl tbl-dense' },
          React.createElement('thead', null, React.createElement('tr', null, ['Time', 'Action', 'Actor', 'Resource'].map((h, i) => React.createElement('th', { key: i }, h)))),
          React.createElement('tbody', null, DATA.AUDIT.map((a, i) => React.createElement('tr', { key: i },
            React.createElement('td', { className: 'mono-sm fg-2' }, a.at), React.createElement('td', null, React.createElement('span', { className: 'typechip', style: { color: a.action.includes('secret') ? 'var(--accent)' : 'var(--signal)' } }, a.action)), React.createElement('td', { className: 'mono-sm fg-1' }, a.actor), React.createElement('td', { className: 'fg-1', style: { fontSize: 12.5 } }, a.resource)))))))))) );
}

Object.assign(window, { WidgetScreen, ConnectScreen, SettingsScreen });
