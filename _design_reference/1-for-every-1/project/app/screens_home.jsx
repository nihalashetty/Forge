/* Forge home screens: Dashboard, Project Overview, Onboarding wizard. */

/* ============ DASHBOARD ============ */
function DashboardScreen({ onOpenProject, onNewProject }) {
  const kpis = [
    { label: 'Runs · 7 days', value: '3,892', delta: '+12%', up: true, spark: DATA.spark(16, 50, 22), color: 'var(--accent)' },
    { label: 'Success rate', value: '98.4%', delta: '+0.6%', up: true, spark: DATA.spark(16, 80, 8), color: 'var(--ok)' },
    { label: 'Avg latency', value: '3.1s', delta: '−0.4s', up: true, spark: DATA.spark(16, 40, 14), color: 'var(--signal)' },
    { label: 'Spend · 7 days', value: '$142.80', delta: '+$18', up: false, spark: DATA.spark(16, 36, 18), color: 'var(--io-json)' },
  ];
  return React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '28px 32px' } },
    React.createElement('div', { className: 'fade-up', style: { maxWidth: 1180, margin: '0 auto' } },
      React.createElement('div', { className: 'row spread', style: { marginBottom: 22, alignItems: 'flex-end' } },
        React.createElement('div', null,
          React.createElement('div', { className: 't-display-lg' }, 'Welcome back, Riley'),
          React.createElement('div', { className: 'fg-1', style: { marginTop: 4 } }, 'Self-hosted agent platform · 6 projects · all systems nominal')),
        React.createElement('div', { className: 'row gap2' },
          React.createElement('button', { className: 'btn btn-secondary' }, React.createElement(Icon, { name: 'traces', size: 15 }), 'View all traces'),
          React.createElement('button', { className: 'btn btn-primary', onClick: onNewProject }, React.createElement(Icon, { name: 'plus', size: 15 }), 'New project'))),
      /* KPI row */
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 24 } },
        kpis.map((k, i) => React.createElement('div', { key: i, className: 'card', style: { padding: 16 } },
          React.createElement('div', { className: 'row spread', style: { marginBottom: 8 } },
            React.createElement('span', { className: 't-micro' }, k.label),
            React.createElement('span', { className: 'pill ' + (k.up ? 'pill-ok' : 'pill-warn'), style: { height: 18 } }, k.delta)),
          React.createElement('div', { className: 'row spread', style: { alignItems: 'flex-end' } },
            React.createElement('div', { className: 't-display', style: { fontSize: 26 } }, k.value),
            React.createElement(Sparkline, { data: k.spark, w: 88, h: 30, color: k.color }))))),
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1.5fr 1fr', gap: 20 } },
        /* Projects */
        React.createElement('div', null,
          React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } },
            React.createElement('div', { className: 't-h1' }, 'Projects'),
            React.createElement('button', { className: 'btn btn-ghost btn-sm' }, 'All', React.createElement(Icon, { name: 'chevright', size: 14 }))),
          React.createElement('div', { className: 'col gap3' },
            DATA.PROJECTS.map(p => React.createElement('div', { key: p.id, className: 'card card-hover', style: { padding: 14 }, onClick: () => onOpenProject(p.id) },
              React.createElement('div', { className: 'row gap3' },
                React.createElement(Tile, { icon: 'layers', color: p.status === 'draft' ? 'var(--fg-2)' : 'var(--accent)', size: 40 }),
                React.createElement('div', { className: 'grow', style: { minWidth: 0 } },
                  React.createElement('div', { className: 'row gap2' }, React.createElement('span', { className: 't-h2' }, p.name), React.createElement(StatusPill, { status: p.status })),
                  React.createElement('div', { className: 'fg-2 t-caption row gap3', style: { marginTop: 3 } },
                    React.createElement('span', null, p.workflows + ' workflows'), React.createElement('span', null, p.tools + ' tools'),
                    React.createElement('span', null, 'edited ' + p.edited))),
                React.createElement('div', { className: 'col', style: { alignItems: 'flex-end', gap: 4 } },
                  React.createElement(Sparkline, { data: p.spark, w: 92, h: 26, color: 'var(--accent)' }),
                  React.createElement('div', { className: 'fg-2 t-caption' }, p.runs7d.toLocaleString() + ' runs / 7d'))))))),
        /* Recent runs */
        React.createElement('div', null,
          React.createElement('div', { className: 'row spread', style: { marginBottom: 12 } },
            React.createElement('div', { className: 't-h1' }, 'Recent runs'),
            React.createElement('span', { className: 'pill pill-info', style: { height: 18 } }, React.createElement('span', { className: 'dot' }), 'live')),
          React.createElement('div', { className: 'card', style: { overflow: 'hidden' } },
            DATA.RECENT_RUNS.map((r, i) => React.createElement('div', { key: r.id, className: 'row gap3', style: { padding: '11px 14px', borderBottom: i < DATA.RECENT_RUNS.length - 1 ? '1px solid var(--line)' : 'none', cursor: 'pointer' },
              onMouseEnter: e => e.currentTarget.style.background = 'var(--bg-3)', onMouseLeave: e => e.currentTarget.style.background = 'none' },
              React.createElement('div', { style: { width: 7, height: 7, borderRadius: '50%', flex: 'none', background: r.status === 'done' ? 'var(--ok)' : r.status === 'error' ? 'var(--err)' : 'var(--warn)' } }),
              React.createElement('div', { className: 'grow', style: { minWidth: 0 } },
                React.createElement('div', { className: 'truncate', style: { fontSize: 13, fontWeight: 600 } }, r.workflow),
                React.createElement('div', { className: 'fg-2 t-caption truncate' }, r.project + ' · ' + r.trigger)),
              React.createElement('div', { className: 'col', style: { alignItems: 'flex-end' } },
                React.createElement('div', { className: 'mono-sm', style: { color: 'var(--fg-1)' } }, r.tokens),
                React.createElement('div', { className: 'fg-2 t-caption' }, r.dur + ' · ' + r.time)))),
            React.createElement('button', { className: 'btn btn-ghost btn-sm', style: { width: '100%', borderRadius: 0, height: 38 } }, 'Open Traces'))))));
}

/* ============ PROJECT OVERVIEW ============ */
function OverviewScreen({ project, onNav }) {
  const health = [
    { label: 'Workflows', value: project.workflows, icon: 'workflows', screen: 'workflows', color: 'var(--accent)' },
    { label: 'Agents', value: DATA.AGENTS.length, icon: 'agents', screen: 'agents', color: 'var(--io-json)' },
    { label: 'Tools', value: project.tools, icon: 'tools', screen: 'tools', color: 'var(--signal)' },
    { label: 'Knowledge sources', value: DATA.KB_SOURCES.length, icon: 'knowledge', screen: 'knowledge', color: 'var(--io-vector)' },
  ];
  return React.createElement('div', { className: 'scroll-y', style: { flex: 1, padding: '24px 28px' } },
    React.createElement('div', { className: 'fade-up', style: { maxWidth: 1080, margin: '0 auto' } },
      React.createElement('div', { className: 'row spread', style: { marginBottom: 20 } },
        React.createElement('div', null,
          React.createElement('div', { className: 't-display' }, project.name),
          React.createElement('div', { className: 'fg-1', style: { marginTop: 3 } }, 'Visual agent workspace · published ', React.createElement('b', null, 'Support Router v7'), ' to widget + MCP')),
        React.createElement('div', { className: 'row gap2' },
          React.createElement('button', { className: 'btn btn-secondary', onClick: () => onNav('traces') }, React.createElement(Icon, { name: 'traces', size: 15 }), 'Traces'),
          React.createElement('button', { className: 'btn btn-primary', onClick: () => onNav('workflow-canvas') }, React.createElement(Icon, { name: 'workflows', size: 15 }), 'Open canvas'))),
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 22 } },
        health.map((h, i) => React.createElement('button', { key: i, className: 'card card-hover', style: { padding: 16, textAlign: 'left', background: 'var(--bg-1)' }, onClick: () => onNav(h.screen) },
          React.createElement('div', { className: 'row spread' }, React.createElement(Tile, { icon: h.icon, color: h.color, size: 34 }), React.createElement(Icon, { name: 'chevright', size: 16, style: { color: 'var(--fg-2)' } })),
          React.createElement('div', { className: 't-display', style: { fontSize: 28, marginTop: 12 } }, h.value),
          React.createElement('div', { className: 'fg-2 t-caption' }, h.label)))),
      React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 20 } },
        React.createElement('div', { className: 'card', style: { padding: 18 } },
          React.createElement('div', { className: 'row spread', style: { marginBottom: 14 } }, React.createElement('div', { className: 't-h1' }, 'Workflows'), React.createElement('button', { className: 'btn btn-ghost btn-sm', onClick: () => onNav('workflow-canvas') }, React.createElement(Icon, { name: 'plus', size: 14 }), 'New')),
          React.createElement('div', { className: 'col gap2' },
            [['Support Router', 'published', '1,840 runs', 'v7'], ['Refund Flow', 'published', '410 runs', 'v3'], ['Lead Qualifier', 'draft', '— runs', 'v1'], ['Onboarding Helper', 'draft', '40 runs', 'v2']].map((w, i) =>
              React.createElement('button', { key: i, className: 'row gap3', onClick: () => onNav('workflow-canvas'), style: { padding: '10px 12px', borderRadius: 8, border: '1px solid var(--line)', background: 'var(--bg-1)', cursor: 'pointer', textAlign: 'left' },
                onMouseEnter: e => e.currentTarget.style.borderColor = 'var(--accent)', onMouseLeave: e => e.currentTarget.style.borderColor = 'var(--line)' },
                React.createElement(Tile, { icon: 'workflows', color: 'var(--accent)', size: 30 }),
                React.createElement('div', { className: 'grow' }, React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, w[0]), React.createElement('div', { className: 'fg-2 t-caption' }, w[2] + ' · ' + w[3])),
                React.createElement(StatusPill, { status: w[1] === 'published' ? 'active' : 'draft', label: w[1] === 'published' ? 'Published' : 'Draft' })))) ),
        React.createElement('div', { className: 'card', style: { padding: 18 } },
          React.createElement('div', { className: 't-h1', style: { marginBottom: 14 } }, 'Deployment'),
          React.createElement('div', { className: 'col gap3' },
            [['widget', 'Chat Widget', 'Live · acme.dev', 'var(--accent)', 'widget'], ['connect', 'MCP Server', '3 tools exposed', 'var(--signal)', 'connect'], ['playground', 'Playground', 'Internal testing', 'var(--io-json)', 'playground']].map((d, i) =>
              React.createElement('button', { key: i, className: 'row gap3', onClick: () => onNav(d[4]), style: { padding: '10px 12px', borderRadius: 8, border: '1px solid var(--line)', background: 'var(--bg-1)', cursor: 'pointer', textAlign: 'left' } },
                React.createElement(Tile, { icon: d[0], color: d[3], size: 30 }),
                React.createElement('div', { className: 'grow' }, React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, d[1]), React.createElement('div', { className: 'fg-2 t-caption' }, d[2])),
                React.createElement(Icon, { name: 'chevright', size: 16, style: { color: 'var(--fg-2)' } })))))) ));
}

/* ============ ONBOARDING WIZARD ============ */
function OnboardingScreen({ onCreate, onCancel }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [tmpl, setTmpl] = useState('blank');
  const [models, setModels] = useState({ anthropic: true, openai: false, google: false });
  const steps = ['Project', 'Models', 'First workflow'];
  const templates = [
    { id: 'blank', name: 'Blank canvas', desc: 'Start from an empty graph', icon: 'workflows' },
    { id: 'support', name: 'Support agent', desc: 'Router → agent → tools → HITL', icon: 'agents' },
    { id: 'rag', name: 'RAG Q&A', desc: 'Retrieval + grounded answers', icon: 'knowledge' },
    { id: 'mcp', name: 'MCP toolbox', desc: 'Expose tools over MCP', icon: 'connect' },
  ];
  return React.createElement('div', { className: 'col center', style: { flex: 1, padding: 24, background: 'var(--bg-0)' } },
    React.createElement('div', { className: 'card fade-up', style: { width: 640, maxWidth: '94vw', overflow: 'hidden' } },
      /* header w/ steps */
      React.createElement('div', { style: { padding: '18px 22px', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', { className: 'row spread', style: { marginBottom: 14 } },
          React.createElement('div', { className: 't-h1' }, 'New project'),
          React.createElement('button', { className: 'iconbtn', onClick: onCancel }, React.createElement(Icon, { name: 'x', size: 17 }))),
        React.createElement('div', { className: 'row gap2' }, steps.map((s, i) => React.createElement('div', { key: i, className: 'row gap2 grow' },
          React.createElement('div', { style: { width: 22, height: 22, borderRadius: '50%', flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)',
            background: i < step ? 'var(--accent)' : i === step ? 'var(--accent-glow)' : 'var(--bg-3)', color: i < step ? '#fff' : i === step ? 'var(--accent)' : 'var(--fg-2)', border: i === step ? '1px solid var(--accent)' : 'none' } },
            i < step ? React.createElement(Icon, { name: 'check', size: 13 }) : i + 1),
          React.createElement('span', { style: { fontSize: 12.5, fontWeight: 600, color: i <= step ? 'var(--fg-0)' : 'var(--fg-2)' } }, s),
          i < steps.length - 1 && React.createElement('div', { className: 'grow', style: { height: 1, background: i < step ? 'var(--accent)' : 'var(--line)' } }))))),
      React.createElement('div', { style: { padding: 22, minHeight: 280 } },
        step === 0 && React.createElement('div', { className: 'fade-in' },
          React.createElement(Field, { label: 'Project name', help: 'A workspace for related workflows, tools, and knowledge.', required: true },
            React.createElement('input', { className: 'input', autoFocus: true, value: name, onChange: e => setName(e.target.value), placeholder: 'e.g. Customer Support' })),
          React.createElement('div', { className: 'field-label' }, 'Start from'),
          React.createElement('div', { style: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 } },
            templates.map(t => React.createElement('button', { key: t.id, onClick: () => setTmpl(t.id), style: { textAlign: 'left', padding: 12, borderRadius: 10, cursor: 'pointer', background: 'var(--bg-1)', border: '1px solid ' + (tmpl === t.id ? 'var(--accent)' : 'var(--line)'), boxShadow: tmpl === t.id ? '0 0 0 3px var(--accent-glow)' : 'none' } },
              React.createElement('div', { className: 'row gap2', style: { marginBottom: 6 } }, React.createElement(Tile, { icon: t.icon, color: 'var(--accent)', size: 28 }), tmpl === t.id && React.createElement(Icon, { name: 'check', size: 16, style: { color: 'var(--accent)', marginLeft: 'auto' } })),
              React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, t.name),
              React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 2 } }, t.desc))))),
        step === 1 && React.createElement('div', { className: 'fade-in' },
          React.createElement('div', { className: 'fg-1', style: { marginBottom: 14 } }, 'Connect at least one model provider. Keys are stored encrypted in your secret store — they never leave your instance.'),
          [['anthropic', 'Anthropic', 'claude-sonnet-4-6, haiku-4-2'], ['openai', 'OpenAI', 'gpt-5.4, gpt-5.4-mini'], ['google', 'Google', 'gemini-3.1-pro, 3.5-flash']].map(p =>
            React.createElement('div', { key: p[0], className: 'row gap3', style: { padding: '12px 14px', borderRadius: 10, border: '1px solid var(--line)', marginBottom: 10 } },
              React.createElement(Tile, { icon: 'n_llm', color: 'var(--io-json)', size: 32 }),
              React.createElement('div', { className: 'grow' }, React.createElement('div', { style: { fontWeight: 600 } }, p[1]), React.createElement('div', { className: 'fg-2 t-caption' }, p[2])),
              models[p[0]]
                ? React.createElement('input', { className: 'input mono', style: { width: 180 }, type: 'password', defaultValue: 'sk-••••••••••••4f2a' })
                : null,
              React.createElement(Toggle, { on: models[p[0]], onChange: v => setModels(m => ({ ...m, [p[0]]: v })) })))),
        step === 2 && React.createElement('div', { className: 'fade-in col center', style: { textAlign: 'center', gap: 10, paddingTop: 16 } },
          React.createElement(Tile, { icon: 'check', color: 'var(--ok)', size: 52, glow: true }),
          React.createElement('div', { className: 't-h1' }, 'You\u2019re ready to forge'),
          React.createElement('div', { className: 'fg-1', style: { maxWidth: 380 } }, 'We\u2019ll open the canvas with a ', React.createElement('b', null, templates.find(t => t.id === tmpl).name.toLowerCase()), ' starter so you can drop your first node and run it in the playground.'))),
      React.createElement('div', { className: 'row spread', style: { padding: '14px 22px', borderTop: '1px solid var(--line)' } },
        React.createElement('button', { className: 'btn btn-ghost', onClick: () => step === 0 ? onCancel() : setStep(step - 1) }, step === 0 ? 'Cancel' : 'Back'),
        React.createElement('button', { className: 'btn btn-primary', disabled: step === 0 && !name, onClick: () => step < 2 ? setStep(step + 1) : onCreate() },
          step < 2 ? 'Continue' : 'Open canvas', React.createElement(Icon, { name: 'chevright', size: 15 })))));
}

Object.assign(window, { DashboardScreen, OverviewScreen, OnboardingScreen });
