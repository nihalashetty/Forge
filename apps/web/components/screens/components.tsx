"use client";
/* Components screen (Feature 2 — generative UI): author UI widgets (HTML + CSS + props +
   button actions) that an agent can render in chat. Code-first editor (no visual builder,
   per product direction) with a live sandboxed preview. A new component pre-loads with a
   simple 2-column table example. */
import { useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { Tile } from "../primitives";
import { api, ComponentT } from "@/lib/api";
import { ComponentRenderer } from "../component-renderer";

const DEFAULT_HTML = `<div class="card">
  <div class="title">{{title}}</div>
  <table>
    {{#col1}}<thead><tr><th>{{col1}}</th><th>{{col2}}</th></tr></thead>{{/col1}}
    <tbody>
      {{#rows}}
      <tr><td>{{label}}</td><td>{{value}}</td></tr>
      {{/rows}}
    </tbody>
  </table>
</div>`;

const DEFAULT_CSS = `.card { font-family: system-ui, -apple-system, sans-serif; border: 1px solid #e3e3e8; border-radius: 12px; overflow: hidden; max-width: 420px; background: #fff; color: #1a1a1f; }
.title { font-weight: 600; font-size: 14px; padding: 10px 14px; border-bottom: 1px solid #e3e3e8; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 8px 14px; border-bottom: 1px solid #f0f0f3; }
th { color: #6b6b76; font-weight: 600; background: #fafafb; }
tbody tr:last-child td { border-bottom: none; }`;

const DEFAULT_PROPS_SCHEMA = {
  type: "object",
  properties: {
    title: { type: "string", description: "Card title" },
    col1: { type: "string", description: "First column header" },
    col2: { type: "string", description: "Second column header" },
    rows: { type: "array", description: "Rows, each an object with label and value" },
  },
  required: ["title"],
};

const DEFAULT_SAMPLE = {
  title: "Weather — London",
  col1: "Day",
  col2: "Forecast",
  rows: [
    { label: "Mon", value: "Sunny · 24°C" },
    { label: "Tue", value: "Cloudy · 21°C" },
    { label: "Wed", value: "Rain · 18°C" },
  ],
};

const NEW_COMPONENT = {
  name: "new_component",
  title: "New component",
  description: "A UI component the agent can render for the user.",
  html: DEFAULT_HTML,
  css: DEFAULT_CSS,
  props_schema: DEFAULT_PROPS_SCHEMA,
  sample_props: DEFAULT_SAMPLE,
  actions: [],
};

export function ComponentsScreen({ project, onOpen }: { project: any; onOpen: (c: ComponentT) => void }) {
  const [items, setItems] = useState<ComponentT[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!project?.id) return;
    setLoaded(false);
    setErr(null);
    api
      .listComponents(project.id)
      .then((c) => {
        setItems(c);
        setLoaded(true);
      })
      .catch((e) => {
        setErr(String(e.message || e));
        setLoaded(true);
      });
  }, [project?.id]);

  async function create() {
    if (creating) return;
    setCreating(true);
    try {
      const c = await api.createComponent(project.id, NEW_COMPONENT as any);
      onOpen(c);
    } catch (e: any) {
      setErr(String(e.message || e));
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="col grow scroll-y" style={{ minHeight: 0 }}>
      <div className="row spread" style={{ padding: "16px 20px", borderBottom: "1px solid var(--line)" }}>
        <div className="row gap2">
          <Tile icon="grid" color="var(--io-vector)" size={30} />
          <div>
            <div className="t-h2">Components</div>
            <div className="fg-2 t-caption">UI widgets the agent can render in chat — attached to agents like tools.</div>
          </div>
        </div>
        <button className="btn btn-primary btn-sm" onClick={create} disabled={creating || !project}>
          <Icon name="plus" size={14} />
          New component
        </button>
      </div>
      <div style={{ padding: 20 }}>
        {err && <div className="card" style={{ padding: 14, color: "var(--err)", marginBottom: 12 }}>{err}</div>}
        {!loaded && <div className="fg-2 t-body-sm">Loading…</div>}
        {loaded && items.length === 0 && !err && (
          <div className="col center" style={{ minHeight: 220, gap: 8, color: "var(--fg-2)", textAlign: "center" }}>
            <Tile icon="grid" color="var(--io-vector)" size={40} />
            <div className="t-h3" style={{ color: "var(--fg-1)" }}>No components yet</div>
            <div className="t-caption" style={{ maxWidth: 380 }}>
              Author an HTML/CSS widget — a table, product card, or form — then attach it to an agent. The agent renders it in chat when relevant.
            </div>
            <button className="btn btn-secondary btn-sm" onClick={create} disabled={creating} style={{ marginTop: 6 }}>
              <Icon name="plus" size={14} />
              New component
            </button>
          </div>
        )}
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
          {items.map((c) => (
            <button
              key={c.id}
              className="card card-hover col"
              style={{ padding: 14, textAlign: "left", alignItems: "stretch", gap: 6 }}
              onClick={() => onOpen(c)}
            >
              <div className="row gap2" style={{ alignItems: "center" }}>
                <Tile icon="grid" color="var(--io-vector)" size={26} />
                <div className="grow" style={{ minWidth: 0 }}>
                  <div className="t-h3 truncate">{c.title || c.name}</div>
                  <div className="mono-sm fg-2 truncate">{c.name}</div>
                </div>
                {!c.enabled && (
                  <span className="pill pill-muted">
                    <span className="dot" />
                    off
                  </span>
                )}
              </div>
              <div
                className="t-caption fg-2"
                style={{ overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}
              >
                {c.description || "—"}
              </div>
              <div className="row gap2" style={{ marginTop: 2 }}>
                <span className="typechip">{c.kind}</span>
                <span className="typechip">v{c.version}</span>
                {Array.isArray(c.actions) && c.actions.length > 0 && (
                  <span className="typechip">
                    {c.actions.length} action{c.actions.length !== 1 ? "s" : ""}
                  </span>
                )}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function tryParse(text: string): { value: any; error: string | null } {
  if (!text.trim()) return { value: undefined, error: null };
  try {
    return { value: JSON.parse(text), error: null };
  } catch (e: any) {
    return { value: undefined, error: String(e.message || e) };
  }
}

export function ComponentBuilderScreen({
  project,
  componentId,
  onBack,
}: {
  project: any;
  componentId?: string;
  onBack: () => void;
}) {
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState("new_component");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [html, setHtml] = useState(DEFAULT_HTML);
  const [css, setCss] = useState(DEFAULT_CSS);
  const [propsText, setPropsText] = useState(JSON.stringify(DEFAULT_PROPS_SCHEMA, null, 2));
  const [sampleText, setSampleText] = useState(JSON.stringify(DEFAULT_SAMPLE, null, 2));
  const [actionsText, setActionsText] = useState("[]");
  const [enabled, setEnabled] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!project?.id || !componentId) {
      setLoaded(true);
      return;
    }
    setLoaded(false);
    api
      .getComponent(project.id, componentId)
      .then((c) => {
        setName(c.name);
        setTitle(c.title || "");
        setDescription(c.description || "");
        setHtml(c.html || "");
        setCss(c.css || "");
        setPropsText(JSON.stringify(c.props_schema || {}, null, 2));
        setSampleText(JSON.stringify(c.sample_props || {}, null, 2));
        setActionsText(JSON.stringify(c.actions || [], null, 2));
        setEnabled(c.enabled);
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, [project?.id, componentId]);

  const sample = useMemo(() => tryParse(sampleText), [sampleText]);
  const actionsParsed = useMemo(() => tryParse(actionsText), [actionsText]);
  const propsParsed = useMemo(() => tryParse(propsText), [propsText]);
  const previewProps = sample.error ? {} : sample.value || {};
  const previewActions = actionsParsed.error ? [] : actionsParsed.value || [];

  async function save() {
    if (saving) return;
    if (propsParsed.error) return setStatus("Props schema is not valid JSON.");
    if (sample.error) return setStatus("Sample props is not valid JSON.");
    if (actionsParsed.error) return setStatus("Actions is not valid JSON.");
    setSaving(true);
    setStatus(null);
    const body = {
      name: name.trim().replace(/\s+/g, "_") || "component",
      title: title || null,
      description,
      html,
      css,
      props_schema: propsParsed.value || {},
      sample_props: sample.value || {},
      actions: previewActions,
      enabled,
    };
    try {
      if (componentId) await api.updateComponent(project.id, componentId, body);
      else await api.createComponent(project.id, body as any);
      setStatus("Saved.");
      setTimeout(() => setStatus(null), 1600);
    } catch (e: any) {
      setStatus(`Save failed: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  async function del() {
    if (!componentId) return;
    if (!window.confirm(`Delete component "${name}"?`)) return;
    await api.deleteComponent(project.id, componentId);
    onBack();
  }

  const codeStyle: any = { fontFamily: "var(--font-mono)", fontSize: 12, lineHeight: "18px", minHeight: 120, resize: "vertical" };
  const field = (label: string, node: any, help?: string, helpErr?: boolean) => (
    <div style={{ marginBottom: 12 }}>
      <label className="field-label">{label}</label>
      {node}
      {help && <div className="field-help" style={helpErr ? { color: "var(--err)" } : undefined}>{help}</div>}
    </div>
  );

  if (!loaded) return <div className="col center grow" style={{ color: "var(--fg-2)" }}>Loading…</div>;

  return (
    <div className="col grow" style={{ minHeight: 0 }}>
      <div className="row spread" style={{ padding: "12px 20px", borderBottom: "1px solid var(--line)", flex: "none" }}>
        <div className="row gap2">
          <button className="iconbtn" onClick={onBack} title="Back to Components">
            <Icon name="chevleft" size={17} />
          </button>
          <div>
            <div className="t-h2">{title || name}</div>
            <div className="mono-sm fg-2">{name}</div>
          </div>
        </div>
        <div className="row gap2" style={{ alignItems: "center" }}>
          {status && (
            <span
              className="t-caption"
              style={{ color: status.includes("fail") || status.includes("not valid") ? "var(--err)" : "var(--ok)" }}
            >
              {status}
            </span>
          )}
          <label className="row gap2" style={{ alignItems: "center", fontSize: 12, color: "var(--fg-1)", cursor: "pointer" }}>
            <span className={"toggle" + (enabled ? " on" : "")} onClick={() => setEnabled((v) => !v)} role="switch" aria-checked={enabled} />
            enabled
          </label>
          {componentId && (
            <button className="btn btn-danger btn-sm" onClick={del}>
              Delete
            </button>
          )}
          <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
            <Icon name={saving ? "refresh" : "check"} size={14} style={saving ? { animation: "spin 1s linear infinite" } : {}} />
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        <div className="scroll-y" style={{ flex: 1, minWidth: 0, padding: 20, borderRight: "1px solid var(--line)" }}>
          {field("Name", <input className="input mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="product_card" />, "Machine name — this is the tool name the agent calls.")}
          {field("Title", <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Product card" />)}
          {field(
            "Description",
            <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="When should the agent render this?" style={{ minHeight: 56 }} />,
            "Model-facing: tells the agent when to use this component.",
          )}
          {field(
            "HTML",
            <textarea className="textarea" value={html} onChange={(e) => setHtml(e.target.value)} style={codeStyle} spellCheck={false} />,
            'Mustache: {{prop}} for values, {{#rows}}…{{/rows}} to loop. Buttons: add data-forge-action="id".',
          )}
          {field("CSS", <textarea className="textarea" value={css} onChange={(e) => setCss(e.target.value)} style={codeStyle} spellCheck={false} />)}
          {field(
            "Props schema (JSON)",
            <textarea className="textarea" value={propsText} onChange={(e) => setPropsText(e.target.value)} style={{ ...codeStyle, borderColor: propsParsed.error ? "var(--err)" : undefined }} spellCheck={false} />,
            propsParsed.error ? `Invalid JSON: ${propsParsed.error}` : "JSON Schema for the props the agent supplies.",
            !!propsParsed.error,
          )}
          {field(
            "Sample props (JSON)",
            <textarea className="textarea" value={sampleText} onChange={(e) => setSampleText(e.target.value)} style={{ ...codeStyle, borderColor: sample.error ? "var(--err)" : undefined }} spellCheck={false} />,
            sample.error ? `Invalid JSON: ${sample.error}` : "Drives the live preview.",
            !!sample.error,
          )}
          {field(
            "Actions (JSON)",
            <textarea className="textarea" value={actionsText} onChange={(e) => setActionsText(e.target.value)} style={{ ...codeStyle, minHeight: 80, borderColor: actionsParsed.error ? "var(--err)" : undefined }} spellCheck={false} />,
            actionsParsed.error ? `Invalid JSON: ${actionsParsed.error}` : 'Buttons: [{ "id": "add", "label": "Add", "message": "Add {{props.title}} to cart" }]',
            !!actionsParsed.error,
          )}
        </div>
        <div className="scroll-y" style={{ width: 460, flex: "none", padding: 20, background: "var(--bg-0)" }}>
          <div className="t-micro" style={{ marginBottom: 10 }}>Live preview</div>
          <div className="card" style={{ padding: 14 }}>
            <ComponentRenderer def={{ name, html, css, actions: previewActions }} props={previewProps} onAction={(a, f) => setStatus(`Action "${a}" → ${JSON.stringify(f)}`)} />
          </div>
          <div className="field-help" style={{ marginTop: 10 }}>
            Rendered in a sandboxed iframe with your sample props. Clicking a button shows what it would send back to the agent.
          </div>
        </div>
      </div>
    </div>
  );
}
