/* Forge app shell: global rail, topbar, project sidebar, command palette, assistant. */

const GLOBAL_RAIL = [
  { id: 'dashboard', label: 'Home', icon: 'dashboard' },
];

/* ---------------- Theme toggle hook ---------------- */
function useTheme() {
  const [theme, setTheme] = useState(() => document.documentElement.getAttribute('data-theme') || 'light');
  useEffect(() => { document.documentElement.setAttribute('data-theme', theme); }, [theme]);
  return [theme, setTheme];
}

/* ---------------- Global left rail (icon strip) ---------------- */
function GlobalRail({ theme, setTheme, onCommand, onAssistant }) {
  return React.createElement('div', { style: { width: 56, flex: 'none', background: 'var(--bg-1)', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '10px 0', gap: 4 } },
    React.createElement('div', { style: { width: 34, height: 34, borderRadius: 9, background: 'linear-gradient(140deg,var(--accent-bright),var(--accent-dim))', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#fff', marginBottom: 8, boxShadow: '0 2px 10px var(--accent-glow)' } },
      React.createElement(Icon, { name: 'flame', size: 20 })),
    React.createElement(RailBtn, { icon: 'search', label: 'Search  ⌘K', onClick: onCommand }),
    React.createElement(RailBtn, { icon: 'sparkles', label: 'Forge Assistant', onClick: onAssistant }),
    React.createElement('div', { style: { flex: 1 } }),
    React.createElement(RailBtn, { icon: 'theme', label: theme === 'dark' ? 'Light mode' : 'Dark console', onClick: () => setTheme(theme === 'dark' ? 'light' : 'dark') }),
    React.createElement(RailBtn, { icon: 'help', label: 'Docs & help' }),
    React.createElement('div', { style: { marginTop: 6 } }, React.createElement(Avatar, { name: 'Riley Cho', size: 30 })));
}
function RailBtn({ icon, label, onClick, active }) {
  const [hv, setHv] = useState(false);
  return React.createElement('div', { style: { position: 'relative' }, onMouseEnter: () => setHv(true), onMouseLeave: () => setHv(false) },
    React.createElement('button', { className: 'iconbtn' + (active ? ' active' : ''), style: { width: 38, height: 38 }, onClick },
      React.createElement(Icon, { name: icon, size: 19 })),
    hv && React.createElement('div', { className: 'tooltip-pop', style: { left: 46, top: 9 } }, label));
}

/* ---------------- Topbar ---------------- */
function Topbar({ crumbs, right, onCommand }) {
  return React.createElement('div', { style: { height: 52, flex: 'none', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 12, background: 'var(--bg-1)' } },
    React.createElement('div', { className: 'row gap2', style: { minWidth: 0 } },
      crumbs.map((c, i) => React.createElement('div', { key: i, className: 'row gap2', style: { minWidth: 0 } },
        i > 0 && React.createElement(Icon, { name: 'chevright', size: 15, style: { color: 'var(--fg-2)', flex: 'none' } }),
        React.createElement('button', { onClick: c.onClick, disabled: !c.onClick,
          className: i === crumbs.length - 1 ? 't-h1' : '',
          style: { background: 'none', border: 'none', cursor: c.onClick ? 'pointer' : 'default', padding: 0, fontFamily: i === crumbs.length - 1 ? 'var(--font-display)' : 'var(--font-ui)', fontSize: i === crumbs.length - 1 ? 16 : 13, fontWeight: 600, color: i === crumbs.length - 1 ? 'var(--fg-0)' : 'var(--fg-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' } }, c.label),
        c.badge))),
    React.createElement('div', { style: { flex: 1 } }),
    React.createElement('button', { className: 'row gap2', onClick: onCommand, style: { height: 32, padding: '0 10px 0 10px', border: '1px solid var(--line-strong)', borderRadius: 6, background: 'var(--bg-1)', cursor: 'pointer', color: 'var(--fg-2)', fontSize: 12.5 } },
      React.createElement(Icon, { name: 'search', size: 15 }), React.createElement('span', { style: { width: 110, textAlign: 'left' } }, 'Search…'), React.createElement('span', { className: 'kbd' }, '⌘K')),
    right);
}

/* ---------------- Project sidebar ---------------- */
function ProjectSidebar({ project, active, onNav, onBack }) {
  return React.createElement('div', { style: { width: 224, flex: 'none', background: 'var(--bg-1)', borderRight: '1px solid var(--line)', display: 'flex', flexDirection: 'column' } },
    React.createElement('button', { onClick: onBack, className: 'row gap2', style: { padding: '12px 14px 10px', background: 'none', border: 'none', borderBottom: '1px solid var(--line)', cursor: 'pointer', textAlign: 'left' } },
      React.createElement(Tile, { icon: 'layers', color: 'var(--accent)', size: 32 }),
      React.createElement('div', { className: 'col', style: { minWidth: 0, flex: 1 } },
        React.createElement('div', { className: 't-h2 truncate' }, project.name),
        React.createElement('div', { className: 'fg-2 t-caption row gap1' }, React.createElement(StatusPill, { status: project.status }))),
      React.createElement(Icon, { name: 'chevdown', size: 15, style: { color: 'var(--fg-2)' } })),
    React.createElement('nav', { className: 'scroll-y', style: { flex: 1, padding: 8 } },
      DATA.PROJECT_NAV.map(n => {
        const on = active === n.id;
        return React.createElement('button', { key: n.id, onClick: () => onNav(n.id), className: 'sidenav-item' + (on ? ' active' : ''),
          style: { display: 'flex', alignItems: 'center', gap: 10, width: '100%', height: 34, padding: '0 10px', marginBottom: 1, borderRadius: 7, border: 'none', cursor: 'pointer', textAlign: 'left',
            color: on ? 'var(--accent)' : 'var(--fg-1)', fontSize: 13, fontWeight: on ? 600 : 500, fontFamily: 'var(--font-ui)', transition: 'color var(--dur-fast)' } },
          React.createElement(Icon, { name: n.icon, size: 17, style: { flex: 'none' } }),
          React.createElement('span', { className: 'grow truncate' }, n.label),
          n.count != null && React.createElement('span', { className: 'badge', style: on ? { background: 'var(--accent-glow)', color: 'var(--accent)' } : {} }, n.count));
      })),
    React.createElement('div', { style: { padding: 10, borderTop: '1px solid var(--line)' } },
      React.createElement('button', { className: 'btn btn-primary', style: { width: '100%' }, onClick: () => onNav('playground') },
        React.createElement(Icon, { name: 'play', size: 15 }), 'Open Playground')));
}

/* ---------------- Command palette ---------------- */
function CommandPalette({ open, onClose, onGo, projects }) {
  const [q, setQ] = useState('');
  const inputRef = useRef(null);
  useEffect(() => { if (open) { setQ(''); setTimeout(() => inputRef.current && inputRef.current.focus(), 30); } }, [open]);
  const cmds = useMemo(() => {
    const list = [
      { sec: 'Go to', label: 'Home / Dashboard', icon: 'dashboard', go: { name: 'dashboard' } },
      { sec: 'Go to', label: 'Workflow Canvas — Support Router', icon: 'workflows', go: { name: 'project', project: 'p_support', screen: 'workflow-canvas' } },
      { sec: 'Go to', label: 'Tool Builder — submit_refund', icon: 'tools', go: { name: 'project', project: 'p_support', screen: 'tool-builder' } },
      { sec: 'Go to', label: 'Agent Config — billing_agent', icon: 'agents', go: { name: 'project', project: 'p_support', screen: 'agent-config' } },
      { sec: 'Go to', label: 'Playground', icon: 'playground', go: { name: 'project', project: 'p_support', screen: 'playground' } },
      { sec: 'Go to', label: 'Traces', icon: 'traces', go: { name: 'project', project: 'p_support', screen: 'traces' } },
      { sec: 'Go to', label: 'Knowledge', icon: 'knowledge', go: { name: 'project', project: 'p_support', screen: 'knowledge' } },
      { sec: 'Go to', label: 'Settings & Secrets', icon: 'secret', go: { name: 'project', project: 'p_support', screen: 'settings' } },
      { sec: 'Actions', label: 'New project…', icon: 'plus', go: { name: 'onboarding' } },
      { sec: 'Actions', label: 'New workflow', icon: 'workflows', go: { name: 'project', project: 'p_support', screen: 'workflow-canvas' } },
      { sec: 'Actions', label: 'Run last workflow', icon: 'play', go: { name: 'project', project: 'p_support', screen: 'playground' } },
    ];
    projects.forEach(p => list.push({ sec: 'Projects', label: p.name, icon: 'layers', go: { name: 'project', project: p.id, screen: 'overview' } }));
    if (!q) return list;
    return list.filter(c => c.label.toLowerCase().includes(q.toLowerCase()));
  }, [q, projects]);
  const groups = useMemo(() => { const g = {}; cmds.forEach(c => { (g[c.sec] = g[c.sec] || []).push(c); }); return g; }, [cmds]);
  if (!open) return null;
  return React.createElement('div', { className: 'fade-in', style: { position: 'fixed', inset: 0, zIndex: 8500, background: 'rgba(8,10,14,.45)', backdropFilter: 'blur(3px)', display: 'flex', justifyContent: 'center', paddingTop: '12vh' }, onMouseDown: onClose },
    React.createElement('div', { className: 'card fade-up', style: { width: 600, maxWidth: '92vw', height: 'fit-content', maxHeight: '70vh', boxShadow: 'var(--sh-pop)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }, onMouseDown: e => e.stopPropagation() },
      React.createElement('div', { className: 'row gap2', style: { padding: '12px 14px', borderBottom: '1px solid var(--line)' } },
        React.createElement(Icon, { name: 'search', size: 18, style: { color: 'var(--fg-2)' } }),
        React.createElement('input', { ref: inputRef, value: q, onChange: e => setQ(e.target.value), placeholder: 'Search projects, workflows, tools, actions…',
          style: { flex: 1, border: 'none', outline: 'none', background: 'none', fontSize: 15, color: 'var(--fg-0)', fontFamily: 'var(--font-ui)' } }),
        React.createElement('span', { className: 'kbd' }, 'esc')),
      React.createElement('div', { className: 'scroll-y', style: { padding: 8 } },
        Object.entries(groups).map(([sec, items]) => React.createElement('div', { key: sec, style: { marginBottom: 6 } },
          React.createElement('div', { className: 't-micro', style: { padding: '6px 8px 4px' } }, sec),
          items.map((c, i) => React.createElement('button', { key: i, onClick: () => { onGo(c.go); onClose(); },
            className: 'row gap3', style: { width: '100%', padding: '8px 8px', border: 'none', background: 'none', cursor: 'pointer', borderRadius: 7, textAlign: 'left', color: 'var(--fg-1)' },
            onMouseEnter: e => e.currentTarget.style.background = 'var(--bg-3)', onMouseLeave: e => e.currentTarget.style.background = 'none' },
            React.createElement(Icon, { name: c.icon, size: 16, style: { color: 'var(--fg-2)' } }),
            React.createElement('span', { style: { fontSize: 13.5, color: 'var(--fg-0)' } }, c.label))))))));
}

/* ---------------- Forge Assistant (right dock) ---------------- */
function AssistantPanel({ open, onClose }) {
  const msgs = [
    { who: 'a', text: 'I can scaffold nodes, write tool projections, or explain a trace. What are we building?' },
    { who: 'u', text: 'Add a refund approval step after the billing agent.' },
    { who: 'a', text: 'Done — I inserted a Human Input node "Approve Refund" wired between billing_agent and End, with approve · edit · reject actions. Want me to gate it to refunds over $100?' },
  ];
  return React.createElement(Drawer, { open, onClose, width: 380, title: 'Forge Assistant', sub: 'Builds and edits on the canvas' },
    React.createElement('div', { className: 'col gap4', style: { padding: 16 } },
      msgs.map((m, i) => React.createElement('div', { key: i, className: 'row', style: { gap: 9, alignItems: 'flex-start', flexDirection: m.who === 'u' ? 'row-reverse' : 'row' } },
        m.who === 'a' ? React.createElement(Tile, { icon: 'sparkles', color: 'var(--accent)', size: 28 }) : React.createElement(Avatar, { name: 'Riley Cho', size: 28 }),
        React.createElement('div', { style: { maxWidth: 250, padding: '9px 12px', borderRadius: 11, fontSize: 13, lineHeight: '19px', background: m.who === 'u' ? 'var(--accent)' : 'var(--bg-3)', color: m.who === 'u' ? 'var(--fg-on-accent)' : 'var(--fg-0)', borderTopRightRadius: m.who === 'u' ? 3 : 11, borderTopLeftRadius: m.who === 'a' ? 3 : 11 } }, m.text))),
      React.createElement('div', { className: 'row gap2 wrap', style: { marginTop: 4 } },
        ['Gate to refunds > $100', 'Explain this trace', 'Add a fallback model'].map(s =>
          React.createElement('button', { key: s, className: 'chip', style: { cursor: 'pointer' } }, s)))),
    React.createElement('div', { style: { position: 'sticky', bottom: 0, padding: 12, borderTop: '1px solid var(--line)', background: 'var(--bg-1)' } },
      React.createElement('div', { className: 'row gap2', style: { background: 'var(--bg-3)', border: '1px solid var(--line)', borderRadius: 10, padding: '6px 6px 6px 12px' } },
        React.createElement('input', { placeholder: 'Ask or instruct…', style: { flex: 1, border: 'none', background: 'none', outline: 'none', fontSize: 13, color: 'var(--fg-0)', fontFamily: 'var(--font-ui)' } }),
        React.createElement('button', { className: 'btn btn-primary btn-sm' }, React.createElement(Icon, { name: 'bolt', size: 14 })))));
}

Object.assign(window, { useTheme, GlobalRail, Topbar, ProjectSidebar, CommandPalette, AssistantPanel, RailBtn });
