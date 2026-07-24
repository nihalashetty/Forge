"use client";
/* Forge - root app: routing + chrome assembly (a single navigable SPA, like the handoff). */
import { ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Topbar, ProjectSidebar, CommandPalette, AssistantPanel, Crumb } from "@/components/shell";
import { DashboardScreen, OnboardingScreen, ProjectCard } from "@/components/screens/home";
import { AnalyticsScreen } from "@/components/screens/analytics";
import { PlaygroundScreen } from "@/components/screens/playground";
import { ToolsScreen, ToolBuilderScreen } from "@/components/screens/tools";
import { WorkflowsScreen, WorkflowCanvas } from "@/components/screens/workflows";
import { AgentsScreen, AgentConfigScreen } from "@/components/screens/agents";
import { ComponentsScreen, ComponentBuilderScreen } from "@/components/screens/components";
import { EmbedScreen } from "@/components/screens/embed";
import { KnowledgeScreen } from "@/components/screens/knowledge";
import { TracesScreen } from "@/components/screens/traces";
import { AuthProvidersScreen } from "@/components/screens/auth";
import { SettingsScreen } from "@/components/screens/settings";
import { ConnectScreen } from "@/components/screens/deploy";
import { McpClientsScreen } from "@/components/screens/mcp";
import { ChannelsScreen, TriggersScreen, DatasetsScreen, HandoffScreen } from "@/components/screens/platform";
import { Icon } from "@/components/icons";
import { api, Agent, ComponentT, DashboardStats, Project, Tool, Workflow } from "@/lib/api";
import { groundedWorkflow } from "@/lib/graph";
import { spark } from "@/lib/data";
import { AuthGate } from "@/components/login";

type View = { name: "dashboard" | "onboarding" | "project"; project?: string; screen?: string };

const SCREEN_LABEL: Record<string, string> = {
  overview: "Overview", workflows: "Workflows", "workflow-canvas": "Support Router",
  agents: "Agents", "agent-config": "billing_agent", tools: "Tools", "tool-builder": "Tool", components: "Components", "component-builder": "Component",
  auth: "Auth Providers", knowledge: "Knowledge", playground: "Playground", traces: "Traces",
  connect: "Connect", mcp: "External MCP", settings: "Settings",
  channels: "Channels", triggers: "Triggers", datasets: "Evaluations", handoff: "Agent inbox", embed: "Embed",
};
const PARENT: Record<string, [string, string]> = {
  "workflow-canvas": ["workflows", "Workflows"], "agent-config": ["agents", "Agents"], "tool-builder": ["tools", "Tools"], "component-builder": ["components", "Components"],
};

function App() {
  const [view, setView] = useState<View>({ name: "dashboard" });
  const [cmdOpen, setCmdOpen] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selTool, setSelTool] = useState<Tool | null>(null);
  const [selWorkflow, setSelWorkflow] = useState<Workflow | null>(null);
  const [selAgent, setSelAgent] = useState<Agent | null>(null);
  const [selComponent, setSelComponent] = useState<ComponentT | null>(null);
  const [refreshNonce, setRefreshNonce] = useState(0);
  // Dashboard stats are fetched ONCE here and shared: the project cards read `.projects`
  // (per-project counts) and the DashboardScreen reuses the same object for its KPIs -
  // no second /stats/dashboard call.
  const [dashboard, setDashboard] = useState<DashboardStats | null>(null);
  const projStats = useMemo(() => dashboard?.projects || {}, [dashboard]);

  const reloadProjects = () =>
    api.listProjects().then((p) => { setProjects(p); setLoaded(true); }).catch(() => setLoaded(true));
  useEffect(() => { reloadProjects(); }, []);
  useEffect(() => { api.dashboardStats().then(setDashboard).catch(() => setDashboard(null)); }, [refreshNonce]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setCmdOpen((o) => !o); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const cards: ProjectCard[] = useMemo(
    () =>
      projects.map((p, i) => {
        const s = projStats[p.id] || { workflows: 0, tools: 0, runs_7d: 0 };
        return {
          id: p.id, name: p.name, slug: p.slug, status: p.status,
          workflows: s.workflows, tools: s.tools, runs7d: s.runs_7d,
          spark: spark(14, 24 + i * 6, 16), edited: "recently",
        };
      }),
    [projects, projStats],
  );

  const project = view.project ? cards.find((c) => c.id === view.project) || projects.find((p) => p.id === view.project) : null;
  const go = (v: View) => setView(v);
  const navScreen = (screen: string) => setView((v) => ({ ...v, name: "project", screen }));
  async function deleteProject(projectToDelete: { id: string; name: string }, opts?: { skipConfirm?: boolean }) {
    if (!opts?.skipConfirm && !window.confirm(`Delete project "${projectToDelete.name}"?\n\nThis removes its workflows, agents, tools, auth providers, knowledge, secrets, runs, and traces. This cannot be undone.`)) return;
    await api.deleteProject(projectToDelete.id);
    setProjects((prev) => prev.filter((p) => p.id !== projectToDelete.id));
    setRefreshNonce((n) => n + 1); // refetches dashboard stats (drops the deleted project's counts)
    if (view.project === projectToDelete.id) {
      setSelWorkflow(null);
      setSelAgent(null);
      setSelTool(null);
      go({ name: "dashboard" });
    }
    await reloadProjects();
  }

  // breadcrumbs
  let crumbs: Crumb[] = [{ label: "Forge", onClick: () => go({ name: "dashboard" }) }];
  if (view.name === "dashboard") crumbs = [{ label: "Home" }];
  else if (view.name === "onboarding") crumbs.push({ label: "New project" });
  else if (view.name === "project" && project) {
    crumbs.push({ label: (project as any).name, onClick: () => navScreen("overview") });
    if (view.screen && view.screen !== "overview") {
      const parent = PARENT[view.screen];
      if (parent) crumbs.push({ label: parent[1], onClick: () => navScreen(parent[0]) });
      const leaf =
        view.screen === "tool-builder" && selTool ? selTool.name :
        view.screen === "workflow-canvas" && selWorkflow ? selWorkflow.name :
        view.screen === "agent-config" && selAgent ? selAgent.name :
        view.screen === "component-builder" && selComponent ? selComponent.name :
        SCREEN_LABEL[view.screen] || view.screen;
      crumbs.push({ label: leaf });
    }
  }

  // The canvas registers its save() here so the top-bar Publish can flush unsaved edits
  // before publishing (otherwise Publish would ship the last-saved version, not the canvas).
  const canvasFlushRef = useRef<(() => Promise<void>) | null>(null);
  const [publishState, setPublishState] = useState<"idle" | "publishing" | "published" | "error">("idle");
  async function publishWorkflow() {
    if (!project || !selWorkflow) return;
    setPublishState("publishing");
    try {
      if (canvasFlushRef.current) await canvasFlushRef.current();
      await api.publishWorkflow((project as any).id, selWorkflow.id);
      setPublishState("published");
      setRefreshNonce((n) => n + 1);
      setTimeout(() => setPublishState("idle"), 1600);
    } catch {
      setPublishState("error");
      setTimeout(() => setPublishState("idle"), 2400);
    }
  }

  const topRight: ReactNode =
    view.name === "project" && view.screen === "workflow-canvas" ? (
      <div className="row gap2">
        <button className="btn btn-secondary btn-sm" onClick={() => setAssistantOpen(true)}><Icon name="sparkles" size={14} />Assistant</button>
        <button className="btn btn-primary btn-sm" onClick={publishWorkflow} disabled={!selWorkflow || publishState === "publishing"}>
          <Icon name={publishState === "published" ? "check" : "bolt"} size={14} />
          {publishState === "publishing" ? "Publishing…" : publishState === "published" ? "Published" : publishState === "error" ? "Invalid - fix problems" : "Publish"}
        </button>
      </div>
    ) : (
      <div className="row gap2">
        <button className="btn btn-secondary" onClick={() => setAssistantOpen(true)} style={{ height: 32 }}><Icon name="sparkles" size={14} />Forge Assistant</button>
      </div>
    );

  const body = (() => {
    if (view.name === "dashboard")
      return <DashboardScreen projects={cards} loaded={loaded} stats={dashboard} onOpenProject={(id) => go({ name: "project", project: id, screen: "overview" })} onNewProject={() => go({ name: "onboarding" })} onDeleteProject={deleteProject} />;
    if (view.name === "onboarding")
      return (
        <OnboardingScreen
          onCreate={async ({ name, template, keys }) => {
            try {
              const p = await api.createProject({ name });
              // Persist provider keys as encrypted secrets + provider_credentials.
              const entered = Object.entries(keys || {}).filter(([, v]) => v && v.trim());
              if (entered.length) {
                const pc: Record<string, string> = {};
                for (const [prov, val] of entered) {
                  const secretName = `${prov}_key`;
                  await api.createSecret(p.id, { name: secretName, value: val, kind: "api_key" }).catch(() => {});
                  pc[prov] = `secret://proj/${secretName}`;
                }
                await api.updateProject(p.id, { config: { ...(p.config || {}), provider_credentials: pc } }).catch(() => {});
              }
              if (template === "support" || template === "rag") {
                const g = groundedWorkflow();
                const wf = await api.createWorkflow(p.id, { name: template === "rag" ? "RAG Q&A" : "Support agent", canvas: g.canvas, executable: g.executable });
                await api.publishWorkflow(p.id, wf.id).catch(() => {});
              }
              await reloadProjects();
              setRefreshNonce((n) => n + 1);
              go({ name: "project", project: p.id, screen: template === "blank" || template === "mcp" ? "overview" : "workflow-canvas" });
            } catch {
              go({ name: "dashboard" });
            }
          }}
          onCancel={() => go({ name: "dashboard" })}
        />
      );
    if (view.name === "project") {
      switch (view.screen) {
        case "overview": return <AnalyticsScreen project={project} onNav={navScreen} />;
        case "playground": return <PlaygroundScreen project={project} />;
        case "workflows": return <WorkflowsScreen project={project} onOpen={(w) => { setSelWorkflow(w); navScreen("workflow-canvas"); }} />;
        case "workflow-canvas": return <WorkflowCanvas project={project} workflowId={selWorkflow?.id} onWorkflowChange={setSelWorkflow} onBack={() => navScreen("workflows")} onRun={() => navScreen("playground")} onRegisterFlush={(fn) => { canvasFlushRef.current = fn; }} />;
        case "agents": return <AgentsScreen project={project} onOpen={(a) => { setSelAgent(a); navScreen("agent-config"); }} />;
        case "agent-config": return <AgentConfigScreen project={project} agentId={selAgent?.id} onBack={() => navScreen("agents")} />;
        case "tools": return <ToolsScreen project={project} onOpen={(t) => { setSelTool(t); navScreen("tool-builder"); }} />;
        case "tool-builder": return <ToolBuilderScreen project={project} toolId={selTool?.id} onBack={() => navScreen("tools")} />;
        case "components": return <ComponentsScreen project={project} onOpen={(c) => { setSelComponent(c); navScreen("component-builder"); }} />;
        case "component-builder": return <ComponentBuilderScreen project={project} componentId={selComponent?.id} onBack={() => navScreen("components")} />;
        case "auth": return <AuthProvidersScreen project={project} />;
        case "mcp": return <McpClientsScreen project={project} />;
        case "knowledge": return <KnowledgeScreen project={project} />;
        case "channels": return <ChannelsScreen project={project} />;
        case "triggers": return <TriggersScreen project={project} />;
        case "datasets": return <DatasetsScreen project={project} />;
        case "handoff": return <HandoffScreen project={project} />;
        case "traces": return <TracesScreen project={project} />;
        case "connect": return <ConnectScreen project={project} />;
        case "embed": return <EmbedScreen project={project} />;
        case "settings": return <SettingsScreen project={project} onDeleteProject={(p) => deleteProject(p, { skipConfirm: true })} />;
        default: return <AnalyticsScreen project={project} onNav={navScreen} />;
      }
    }
    return null;
  })();

  const showSidebar = view.name === "project";
  const sidebarActive =
    view.screen === "workflow-canvas" ? "workflows" :
    view.screen === "agent-config" ? "agents" :
    view.screen === "tool-builder" ? "tools" :
    view.screen === "component-builder" ? "components" : (view.screen || "overview");

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {showSidebar && project && sidebarOpen && <ProjectSidebar project={project} active={sidebarActive} onNav={navScreen} onBack={() => go({ name: "dashboard" })} refreshKey={refreshNonce} />}
      <AssistantPanel
        open={assistantOpen}
        onClose={() => setAssistantOpen(false)}
        project={project ? { id: (project as any).id, name: (project as any).name } : null}
        onMutate={() => { setRefreshNonce((n) => n + 1); reloadProjects(); }}
      />
      <div className="col grow" style={{ minWidth: 0 }}>
        <Topbar
          crumbs={crumbs}
          right={topRight}
          left={showSidebar ? (
            <button className="iconbtn" title={sidebarOpen ? "Hide project panel" : "Show project panel"} onClick={() => setSidebarOpen((s) => !s)}>
              <Icon name={sidebarOpen ? "chevleft" : "chevright"} size={17} />
            </button>
          ) : null}
          onCommand={() => setCmdOpen(true)}
        />
        <div key={view.name + (view.screen || "") + (view.project || "") + refreshNonce} className="col grow" style={{ minHeight: 0 }}>
          {body}
        </div>
      </div>
      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} onGo={go} projects={cards} />
    </div>
  );
}

export default function Page() {
  return (
    <AuthGate>
      <App />
    </AuthGate>
  );
}
