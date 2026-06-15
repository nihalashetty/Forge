/* Forge — root app: routing + chrome assembly. */
function App() {
  const [theme, setTheme] = useTheme();
  const [view, setView] = useState({ name: 'dashboard' });
  const [cmdOpen, setCmdOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [selAgent, setSelAgent] = useState(null);
  const [selTool, setSelTool] = useState(null);

  useEffect(() => {
    const h = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setCmdOpen(o => !o); }
    };
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, []);

  const project = view.project ? DATA.PROJECTS.find(p => p.id === view.project) : null;
  const go = (v) => { setView(v); };
  const navScreen = (screen) => setView(v => ({ ...v, name: 'project', screen }));

  /* crumbs */
  const screenLabel = {
    overview: 'Overview', workflows: 'Workflows', 'workflow-canvas': 'Support Router', agents: 'Agents', 'agent-config': (selAgent && selAgent.name) || 'billing_agent',
    tools: 'Tools', 'tool-builder': (selTool && selTool.name) || 'Tool', auth: 'Auth Providers', knowledge: 'Knowledge', playground: 'Playground', traces: 'Traces', widget: 'Widget', connect: 'Connect', settings: 'Settings',
  };
  let crumbs = [{ label: 'Forge', onClick: () => go({ name: 'dashboard' }) }];
  if (view.name === 'dashboard') crumbs = [{ label: 'Home' }];
  else if (view.name === 'onboarding') crumbs.push({ label: 'New project' });
  else if (view.name === 'project' && project) {
    crumbs.push({ label: project.name, onClick: () => navScreen('overview') });
    if (view.screen && view.screen !== 'overview') {
      const parent = { 'workflow-canvas': ['workflows', 'Workflows'], 'agent-config': ['agents', 'Agents'], 'tool-builder': ['tools', 'Tools'] }[view.screen];
      if (parent) crumbs.push({ label: parent[1], onClick: () => navScreen(parent[0]) });
      crumbs.push({ label: screenLabel[view.screen] });
    }
  }

  /* topbar right slot per screen */
  const topRight = (() => {
    if (view.name === 'project' && view.screen === 'workflow-canvas')
      return React.createElement('div', { className: 'row gap2' },
        React.createElement('div', { className: 'row', style: { marginRight: 4 } }, [0, 1, 2].map(i => React.createElement('div', { key: i, style: { marginLeft: i ? -8 : 0, zIndex: 3 - i } }, React.createElement(Avatar, { name: ['Sam O', 'Devon P', 'Ana R'][i], size: 26 })))),
        React.createElement('button', { className: 'btn btn-secondary btn-sm', onClick: () => setAssistantOpen(true) }, React.createElement(Icon, { name: 'sparkles', size: 14 }), 'Assistant'),
        React.createElement('button', { className: 'btn btn-primary btn-sm' }, React.createElement(Icon, { name: 'bolt', size: 14 }), 'Publish'));
    return React.createElement('div', { className: 'row gap2' },
      React.createElement('button', { className: 'iconbtn', onClick: () => setAssistantOpen(true) }, React.createElement(Icon, { name: 'sparkles', size: 18 })),
      React.createElement('button', { className: 'iconbtn' }, React.createElement(Icon, { name: 'bell', size: 18 })));
  })();

  /* screen body */
  const body = (() => {
    if (view.name === 'dashboard') return React.createElement(DashboardScreen, { onOpenProject: id => go({ name: 'project', project: id, screen: 'overview' }), onNewProject: () => go({ name: 'onboarding' }) });
    if (view.name === 'onboarding') return React.createElement(OnboardingScreen, { onCreate: () => go({ name: 'project', project: 'p_support', screen: 'workflow-canvas' }), onCancel: () => go({ name: 'dashboard' }) });
    if (view.name === 'project') {
      switch (view.screen) {
        case 'overview': return React.createElement(OverviewScreen, { project, onNav: navScreen });
        case 'workflows':
        case 'workflow-canvas': return React.createElement(WorkflowCanvas, { project, onOpenInspectorScreen: (node) => { setSelAgent(DATA.AGENTS.find(a => a.name === (node.title || '').toLowerCase().replace(' ', '_')) || DATA.AGENTS[0]); navScreen('agent-config'); } });
        case 'agents': return React.createElement(AgentsScreen, { onOpen: a => { setSelAgent(a); navScreen('agent-config'); } });
        case 'agent-config': return React.createElement(AgentConfigScreen, { agent: selAgent, onBack: () => navScreen('agents') });
        case 'tools': return React.createElement(ToolsScreen, { onOpen: t => { setSelTool(t); navScreen('tool-builder'); } });
        case 'tool-builder': return React.createElement(ToolBuilderScreen, { tool: selTool, onBack: () => navScreen('tools') });
        case 'auth': return React.createElement(AuthProvidersScreen, null);
        case 'knowledge': return React.createElement(KnowledgeScreen, null);
        case 'playground': return React.createElement(PlaygroundScreen, null);
        case 'traces': return React.createElement(TracesScreen, null);
        case 'widget': return React.createElement(WidgetScreen, null);
        case 'connect': return React.createElement(ConnectScreen, null);
        case 'settings': return React.createElement(SettingsScreen, null);
        default: return React.createElement(OverviewScreen, { project, onNav: navScreen });
      }
    }
    return null;
  })();

  const showSidebar = view.name === 'project';

  return React.createElement('div', { style: { display: 'flex', height: '100vh', overflow: 'hidden' } },
    React.createElement(GlobalRail, { theme, setTheme, onCommand: () => setCmdOpen(true), onAssistant: () => setAssistantOpen(true) }),
    showSidebar && React.createElement(ProjectSidebar, { project, active: view.screen === 'workflow-canvas' ? 'workflows' : view.screen === 'agent-config' ? 'agents' : view.screen === 'tool-builder' ? 'tools' : view.screen, onNav: navScreen, onBack: () => go({ name: 'dashboard' }) }),
    React.createElement('div', { className: 'col grow', style: { minWidth: 0 } },
      React.createElement(Topbar, { crumbs, right: topRight, onCommand: () => setCmdOpen(true) }),
      React.createElement('div', { key: view.name + (view.screen || '') + (view.project || ''), className: 'col grow', style: { minHeight: 0 } }, body)),
    React.createElement(CommandPalette, { open: cmdOpen, onClose: () => setCmdOpen(false), onGo: go, projects: DATA.PROJECTS }),
    React.createElement(AssistantPanel, { open: assistantOpen, onClose: () => setAssistantOpen(false) }));
}

ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App));
