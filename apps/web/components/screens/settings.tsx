"use client";
/* Settings: project config, split into a secondary-nav of focused sections
   (General · Members · API Keys · Model Pricing · Budgets · Knowledge · Versioning ·
   Observability · Advanced). Config-backed sections share one Save; Members, API Keys,
   Model Pricing and Secrets manage their own persistence. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { EmptyState, Field, Modal, Segmented, Tabs, Toggle } from "../primitives";
import { api, clearTokens, InviteResult, MeResult, ProjectVersion, Secret, TeamMember } from "@/lib/api";
import { MODELS } from "@/lib/data";

const ROLES = ["owner", "admin", "editor", "viewer", "connector"];

// Project default embedder when rag_defaults.embedding_model is unset. Matches the backend
// default (embeddings._DEFAULT_FASTEMBED) and the project.json schema default.
const DEFAULT_EMBEDDING_MODEL = "fastembed:BAAI/bge-small-en-v1.5";
const DEFAULT_CHILD_CHUNK_SIZE = 300;
const DEFAULT_RERANKER_MODEL = "Xenova/ms-marco-MiniLM-L-6-v2";
const RERANKER_OPTIONS = [
  { value: "Xenova/ms-marco-MiniLM-L-6-v2", label: "MiniLM-L6 · small, CPU-fast (default)" },
  { value: "BAAI/bge-reranker-base", label: "bge-reranker-base · heavier, more accurate" },
];

type SectionId =
  | "general" | "members" | "apikeys" | "pricing" | "budgets"
  | "knowledge" | "versioning" | "observability" | "advanced" | "history";

const SECTIONS: { id: SectionId; label: string; icon: string; savesConfig?: boolean }[] = [
  { id: "general", label: "General", icon: "sliders", savesConfig: true },
  { id: "members", label: "Members & Roles", icon: "user" },
  { id: "apikeys", label: "API Keys", icon: "secret" },
  { id: "pricing", label: "Model Pricing", icon: "coins" },
  { id: "budgets", label: "Budgets & Quotas", icon: "bolt", savesConfig: true },
  { id: "knowledge", label: "Knowledge & Embeddings", icon: "knowledge", savesConfig: true },
  { id: "versioning", label: "Versioning", icon: "clock", savesConfig: true },
  { id: "observability", label: "Observability & Retention", icon: "traces", savesConfig: true },
  { id: "advanced", label: "Advanced", icon: "settings", savesConfig: true },
  { id: "history", label: "History", icon: "clock" },
];

// Which settings section each project-config field belongs to, so History can group changes
// under the same tab names as the nav. Top-level snapshot fields + config.* keys. Anything not
// listed falls through to "advanced". Members/API-key values are intentionally NOT diffed here
// (Members isn't config-backed; secrets are masked below).
const FIELD_SECTION: Record<string, SectionId> = {
  name: "general", description: "general", slug: "general", status: "general", default_model: "general",
  budgets: "budgets",
  rag_defaults: "knowledge",
  version_history_limit: "versioning", versioning: "versioning",
  observability: "observability",
  scheduler: "advanced",
  model_pricing: "pricing",
  provider_credentials: "apikeys",
};
// The config-backed sections that get a History tab (Members isn't config-backed → skipped).
const HISTORY_TABS: SectionId[] = ["general", "budgets", "knowledge", "versioning", "observability", "apikeys", "pricing", "advanced"];
// Field paths whose values must never be shown in a diff (secrets / credentials).
const MASK_RE = /credential|secret|token|api[_-]?key|password/i;

export function SettingsScreen({ project, onDeleteProject }: { project: any; onDeleteProject?: (project: { id: string; name: string }) => Promise<void> | void }) {
  const [section, setSection] = useState<SectionId>("general");
  const [config, setConfig] = useState<Record<string, any>>({});
  const [meta, setMeta] = useState<{ name: string; description: string }>({ name: "", description: "" });
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  const [open, setOpen] = useState(false);
  const [secForm, setSecForm] = useState({ name: "", value: "", kind: "api_key" });
  const [pkeys, setPkeys] = useState<Record<string, string>>({});
  const [keySave, setKeySave] = useState<"idle" | "saving" | "saved">("idle");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [tenantId, setTenantId] = useState("");
  const [copiedWs, setCopiedWs] = useState(false);

  const reloadSecrets = useCallback(() => { if (project?.id) api.listSecrets(project.id).then(setSecrets).catch(() => {}); }, [project?.id]);
  useEffect(() => {
    if (!project?.id) return;
    api.getProject(project.id).then((p) => {
      setConfig(p.config || {});
      setMeta({ name: p.name || "", description: p.description || "" });
    }).catch(() => {});
    reloadSecrets();
  }, [project?.id, reloadSecrets]);
  // The workspace (tenant) id — surfaced in General so a user whose email spans multiple
  // workspaces can supply it at MCP OAuth login. Account-level, so it's independent of project.
  useEffect(() => { api.me().then((m) => setTenantId(m.tenant_id || "")).catch(() => {}); }, []);

  const setCfg = (patch: Record<string, any>) => setConfig((c) => ({ ...c, ...patch }));
  const features = config.features || {};
  const budgets = config.budgets || {};
  const rag = config.rag_defaults || {};
  const versioning = config.versioning || {};
  const observability = config.observability || {};
  const scheduler = config.scheduler || {};
  const embeddingModel = rag.embedding_model || DEFAULT_EMBEDDING_MODEL;
  const setRag = (patch: Record<string, any>) => setCfg({ rag_defaults: { ...rag, ...patch } });

  async function persist() {
    setSave("saving");
    try {
      await api.updateProject(project.id, { name: meta.name || undefined, description: meta.description, config });
      setSave("saved"); setTimeout(() => setSave("idle"), 1400);
    } catch { setSave("idle"); }
  }
  async function addSecret() {
    if (!secForm.name.trim()) return;
    await api.createSecret(project.id, { name: secForm.name, value: secForm.value, kind: secForm.kind });
    setOpen(false); setSecForm({ name: "", value: "", kind: "api_key" }); reloadSecrets();
  }
  async function removeSecret(name: string) {
    let used: { type: string; label: string }[] = [];
    try { used = (await api.secretUsage(project.id, name)).references; } catch { /* fall back to a plain confirm */ }
    const detail = used.length
      ? `\n\nIn use by ${used.length}:\n` + used.map((r) => `• ${r.label} - ${r.type.replace(/_/g, " ")}`).join("\n") + `\n\nDeleting will break these.`
      : "";
    if (!window.confirm(`Delete secret "${name}"?${detail}`)) return;
    await api.deleteSecret(project.id, name, true); reloadSecrets();
  }
  async function saveKeys() {
    setKeySave("saving");
    const pc = { ...(config.provider_credentials || {}) };
    for (const [prov, val] of Object.entries(pkeys)) {
      if (!val.trim()) continue;
      const name = `${prov}_key`;
      await api.createSecret(project.id, { name, value: val, kind: "api_key" });
      pc[prov] = `secret://proj/${name}`;
    }
    const newConfig = { ...config, provider_credentials: pc };
    setConfig(newConfig);
    await api.updateProject(project.id, { config: newConfig });
    setPkeys({}); reloadSecrets();
    setKeySave("saved"); setTimeout(() => setKeySave("idle"), 1400);
  }

  const PROVIDERS: [string, string][] = [["openai", "OpenAI"], ["anthropic", "Anthropic"], ["google_genai", "Google"]];
  const pcreds = config.provider_credentials || {};
  const activeMeta = SECTIONS.find((s) => s.id === section)!;

  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        {/* secondary nav */}
        <nav className="scroll-y" style={{ width: 224, flex: "none", borderRight: "1px solid var(--line)", background: "var(--bg-1)", padding: 10 }}>
          <div className="t-micro" style={{ padding: "6px 8px 8px" }}>Settings</div>
          {SECTIONS.map((s) => {
            const on = section === s.id;
            return (
              <button key={s.id} onClick={() => setSection(s.id)} className={"sidenav-item" + (on ? " active" : "")}
                style={{ display: "flex", alignItems: "center", gap: 10, width: "100%", height: 34, padding: "0 10px", marginBottom: 1, borderRadius: 7, border: "none", cursor: "pointer", textAlign: "left", color: on ? "var(--accent)" : "var(--fg-1)", fontSize: 13, fontWeight: on ? 600 : 500, fontFamily: "var(--font-ui)" }}>
                <Icon name={s.icon} size={16} style={{ flex: "none" }} />
                <span className="grow truncate">{s.label}</span>
              </button>
            );
          })}
        </nav>

        {/* content */}
        <div className="scroll-y grow" style={{ minWidth: 0 }}>
          <div className="fade-up" style={{ maxWidth: 960, margin: "0 auto", padding: "24px 28px" }}>
            <div className="row spread" style={{ marginBottom: 18 }}>
              <div className="t-display">{activeMeta.label}</div>
              {activeMeta.savesConfig && (
                <button className="btn btn-primary btn-sm" onClick={persist} disabled={save === "saving"}>
                  <Icon name={save === "saved" ? "check" : "save"} size={14} />{save === "saving" ? "Saving…" : save === "saved" ? "Saved" : "Save"}
                </button>
              )}
            </div>

            {section === "general" && (
              <>
                <Card title="Project">
                  <Field label="Name"><input className="input" value={meta.name} onChange={(e) => setMeta((m) => ({ ...m, name: e.target.value }))} placeholder="Project name" /></Field>
                  <Field label="Description" help="Shown on the dashboard and in the project header."><textarea className="textarea" rows={2} value={meta.description} onChange={(e) => setMeta((m) => ({ ...m, description: e.target.value }))} /></Field>
                </Card>
                <Card title="Default model">
                  <Field label="Default model" help="Used by new agents and single model-call nodes unless they override it.">
                    <select className="select" value={config.default_model || ""} onChange={(e) => setCfg({ default_model: e.target.value })}>
                      <option value="fake:echo">fake:echo (offline)</option>
                      {MODELS.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.provider}</option>)}
                    </select>
                  </Field>
                </Card>
                <Card title="Workspace">
                  <Field label="Workspace ID" help="Your workspace (tenant) identifier — account-level, NOT the project ID. You normally don't need it, but if you sign in over MCP OAuth and your email belongs to more than one workspace, paste this into the login screen's “Workspace id” field.">
                    <div className="row gap2">
                      <input className="input mono" readOnly value={tenantId} placeholder="…" onFocus={(e) => e.currentTarget.select()} style={{ flex: 1 }} />
                      <button className="btn btn-secondary btn-sm" disabled={!tenantId} onClick={() => { if (tenantId) { navigator.clipboard?.writeText(tenantId); setCopiedWs(true); setTimeout(() => setCopiedWs(false), 1400); } }}>
                        <Icon name={copiedWs ? "check" : "copy"} size={13} />{copiedWs ? "Copied" : "Copy"}
                      </button>
                    </div>
                  </Field>
                </Card>
              </>
            )}

            {section === "members" && <TeamCard />}

            {section === "apikeys" && (
              <>
                <Card title="Model providers" action={<button className="btn btn-primary btn-sm" onClick={saveKeys} disabled={keySave === "saving"}><Icon name={keySave === "saved" ? "check" : "save"} size={14} />{keySave === "saving" ? "Saving…" : keySave === "saved" ? "Saved" : "Save keys"}</button>}>
                  <div className="field-help" style={{ marginTop: 0, marginBottom: 6 }}>Keys are encrypted (Fernet), bound to this project&apos;s models, and never returned. They fall back to the server&apos;s env var if unset.</div>
                  {PROVIDERS.map(([prov, label]) => {
                    const configured = !!pcreds[prov];
                    return (
                      <div key={prov} className="row gap2" style={{ padding: "7px 0" }}>
                        <div style={{ width: 120, flex: "none" }} className="row gap2">
                          <Icon name="n_llm" size={15} style={{ color: configured ? "var(--ok)" : "var(--fg-2)" }} />
                          <span className="t-body-sm" style={{ fontWeight: 600 }}>{label}</span>
                        </div>
                        <input className="input mono" type="password" style={{ flex: 1 }}
                          placeholder={configured ? "•••• configured - re-enter to replace" : "sk-…"}
                          value={pkeys[prov] || ""} onChange={(e) => setPkeys((k) => ({ ...k, [prov]: e.target.value }))} />
                        {configured && <span className="pill pill-ok" style={{ height: 18 }}><span className="dot" />set</span>}
                      </div>
                    );
                  })}
                </Card>
                <Card title="Secrets" action={<button className="btn btn-secondary btn-sm" onClick={() => setOpen(true)}><Icon name="plus" size={14} />Add secret</button>}>
                  <div className="field-help" style={{ marginTop: 0, marginBottom: 8 }}>Write-only - values are encrypted (Fernet) and never returned. Reference as <span className="mono-sm">secret://proj/&lt;name&gt;</span>.</div>
                  {secrets.map((s) => (
                    <div key={s.id} className="row spread" style={{ padding: "8px 0", borderTop: "1px solid var(--line)" }}>
                      <div className="row gap2"><Icon name="secret" size={15} style={{ color: "var(--fg-2)" }} /><span className="mono-sm">{s.name}</span><span className="typechip">{s.kind}</span></div>
                      <div className="row gap2"><span className="mono-sm fg-2">••••••</span><span className="t-caption fg-2">v{s.version}</span><span className="iconbtn" role="button" title="Delete secret" onClick={() => removeSecret(s.name)}><Icon name="trash" size={13} /></span></div>
                    </div>
                  ))}
                  {secrets.length === 0 && <div className="fg-2 t-caption">No secrets yet.</div>}
                </Card>
              </>
            )}

            {section === "pricing" && <PricingCard />}

            {section === "budgets" && (
              <Card title="Budgets & quotas">
                <div className="field-help" style={{ marginTop: 0, marginBottom: 10 }}>Hard limits on model spend. A run is stopped if it would exceed the per-run cap; the monthly cap gates new runs once reached.</div>
                <div className="row gap3 wrap">
                  <Field label="Max $ / run"><input className="input mono" type="number" min={0} step={0.01} value={budgets.max_usd_per_run ?? ""} onChange={(e) => setCfg({ budgets: { ...budgets, max_usd_per_run: parseFloat(e.target.value) || undefined } })} /></Field>
                  <Field label="Monthly $ cap"><input className="input mono" type="number" min={0} step={1} value={budgets.monthly_usd_cap ?? ""} onChange={(e) => setCfg({ budgets: { ...budgets, monthly_usd_cap: parseFloat(e.target.value) || undefined } })} /></Field>
                </div>
                <Field label="Max tokens / run" help="Optional cap on total tokens for a single run."><input className="input mono" type="number" min={0} step={1000} value={budgets.max_tokens_per_run ?? ""} onChange={(e) => setCfg({ budgets: { ...budgets, max_tokens_per_run: parseInt(e.target.value, 10) || undefined } })} /></Field>
              </Card>
            )}

            {section === "knowledge" && (
              <Card title="Knowledge & embeddings">
                <Field label="Embedding model" help="Used to embed knowledge sources and search queries. Applies to the whole project - you can't mix embedders across files. Changing it changes the vector dimension, so re-embed existing sources afterward (the Knowledge tab flags mismatches).">
                  <select className="select" value={embeddingModel} onChange={(e) => setRag({ embedding_model: e.target.value })}>
                    <optgroup label="Local · open-source · free (offline)">
                      <option value={DEFAULT_EMBEDDING_MODEL}>bge-small · local, free (384-dim)</option>
                      <option value="fastembed:BAAI/bge-base-en-v1.5">bge-base · local, free (768-dim)</option>
                    </optgroup>
                    <optgroup label="OpenAI · billed per token (ingest + every query)">
                      <option value="openai:text-embedding-3-small">OpenAI 3-small · billed (1536-dim)</option>
                      <option value="openai:text-embedding-3-large">OpenAI 3-large · billed (3072-dim)</option>
                    </optgroup>
                  </select>
                </Field>
                {embeddingModel.startsWith("openai:") && (
                  <div className="field-help" style={{ marginTop: 0 }}>
                    {pcreds.openai
                      ? "OpenAI key configured under API Keys. Embeddings are billed per token at ingest and on every search."
                      : "⚠ No OpenAI key set — add one under API Keys, or embeddings fall back to the local model."}
                  </div>
                )}
                <Field label="Retrieval mode" help="How chunks are matched vs. handed to the agent. Chunk: search and return the same chunks. Parent/child: embed small child chunks for precise matching but feed the agent the larger parent passage for context. Changing this requires re-ingesting existing sources.">
                  <Segmented
                    options={[{ value: "chunk", label: "Chunk" }, { value: "parent_child", label: "Parent / child" }]}
                    value={rag.retrieval_mode || "chunk"}
                    onChange={(v) => setRag({ retrieval_mode: v })}
                  />
                </Field>
                {rag.retrieval_mode === "parent_child" && (
                  <Field label="Child chunk size" help="Size (chars) of the small child chunks that get embedded in parent/child mode. The parent window uses the chunk size set per source. Smaller children = more precise matches.">
                    <input className="input mono" type="number" placeholder={String(DEFAULT_CHILD_CHUNK_SIZE)}
                      value={rag.child_chunk_size ?? ""}
                      onChange={(e) => setRag({ child_chunk_size: parseInt(e.target.value, 10) || undefined })} />
                  </Field>
                )}
                <Field label="Reranker model" help="Local cross-encoder used when a retrieval node (or the search debugger) has rerank on. Runs offline on CPU, no API cost. Ignored unless rerank is enabled.">
                  <select className="select" value={rag.reranker_model || DEFAULT_RERANKER_MODEL} onChange={(e) => setRag({ reranker_model: e.target.value })}>
                    {RERANKER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </Field>
              </Card>
            )}

            {section === "versioning" && (
              <Card title="Version history">
                <div className="field-help" style={{ marginTop: 0, marginBottom: 10 }}>Every save/publish of a workflow, agent, tool, component, or auth provider captures a version you can inspect and restore from the editor&apos;s History panel.</div>
                <Field label="Versions kept per entity" help="Older versions beyond this count are pruned. Leave blank to keep all.">
                  <input className="input mono" type="number" min={1} step={1} style={{ width: 140 }} placeholder="unlimited"
                    value={config.version_history_limit ?? ""}
                    onChange={(e) => setCfg({ version_history_limit: parseInt(e.target.value, 10) || undefined })} />
                </Field>
                <label className="row spread" style={{ padding: "8px 0" }}>
                  <div><div className="t-body-sm" style={{ fontWeight: 600 }}>Snapshot on publish</div><div className="field-help" style={{ marginTop: 0 }}>Capture a labeled version each time a workflow is published.</div></div>
                  <Toggle on={versioning.snapshot_on_publish !== false} onChange={(v) => setCfg({ versioning: { ...versioning, snapshot_on_publish: v } })} />
                </label>
              </Card>
            )}

            {section === "observability" && (
              <>
                <Card title="Trace redaction">
                  <label className="row spread" style={{ padding: "8px 0" }}>
                    <div><div className="t-body-sm" style={{ fontWeight: 600 }}>Redact PII in traces</div><div className="field-help" style={{ marginTop: 0 }}>Mask emails, phone numbers, and card-like values in stored span inputs/outputs.</div></div>
                    <Toggle on={!!observability.redact_pii} onChange={(v) => setCfg({ observability: { ...observability, redact_pii: v } })} />
                  </label>
                  <label className="row spread" style={{ padding: "8px 0" }}>
                    <div><div className="t-body-sm" style={{ fontWeight: 600 }}>Store message bodies</div><div className="field-help" style={{ marginTop: 0 }}>Persist full user/assistant text on traces. Turn off to keep only metrics (tokens, latency, cost).</div></div>
                    <Toggle on={observability.store_message_bodies !== false} onChange={(v) => setCfg({ observability: { ...observability, store_message_bodies: v } })} />
                  </label>
                </Card>
                <Card title="Retention">
                  <Field label="Trace retention (days)" help="Traces and conversations older than this are eligible for purge. Leave blank to keep indefinitely.">
                    <input className="input mono" type="number" min={1} step={1} style={{ width: 140 }} placeholder="keep all"
                      value={observability.retention_days ?? ""}
                      onChange={(e) => setCfg({ observability: { ...observability, retention_days: parseInt(e.target.value, 10) || undefined } })} />
                  </Field>
                  <label className="row spread" style={{ padding: "8px 0" }}>
                    <div><div className="t-body-sm" style={{ fontWeight: 600 }}>Scheduled cleanup</div><div className="field-help" style={{ marginTop: 0 }}>Run a periodic job to purge data past the retention window.</div></div>
                    <Toggle on={!!scheduler.cleanup_enabled} onChange={(v) => setCfg({ scheduler: { ...scheduler, cleanup_enabled: v } })} />
                  </label>
                </Card>
              </>
            )}

            {section === "advanced" && (
              <>
                <Card title="Feature flags">
                  {[["code_nodes", "Code nodes", "Allow sandboxed code execution"], ["remote_sandbox", "Remote sandbox", "Use E2B/Modal/Daytona for code"], ["advanced_scripts", "Advanced scripts", "RestrictedPython custom auth scripts"]].map(([k, label, desc]) => (
                    <label key={k} className="row spread" style={{ padding: "8px 0" }}>
                      <div><div className="t-body-sm" style={{ fontWeight: 600 }}>{label}</div><div className="field-help" style={{ marginTop: 0 }}>{desc}</div></div>
                      <Toggle on={!!features[k]} onChange={(v) => setCfg({ features: { ...features, [k]: v } })} />
                    </label>
                  ))}
                </Card>
                {onDeleteProject && project?.id && (
                  <div className="card" style={{ padding: 18, marginBottom: 16, borderColor: "var(--err)" }}>
                    <div className="row spread" style={{ marginBottom: 12 }}><div className="t-h2" style={{ color: "var(--err)" }}>Danger zone</div></div>
                    <div className="field-help" style={{ marginTop: 0, marginBottom: 12 }}>Deleting this project removes its workflows, agents, tools, auth providers, knowledge, secrets, runs, and traces. This cannot be undone.</div>
                    {!confirmDelete ? (
                      <button className="btn btn-danger btn-sm" onClick={() => setConfirmDelete(true)}><Icon name="trash" size={14} />Delete project</button>
                    ) : (
                      <div className="row gap2 wrap" style={{ alignItems: "center" }}>
                        <span className="t-body-sm">Permanently delete <b>{project.name}</b>?</span>
                        <button className="btn btn-danger btn-sm" disabled={deleting}
                          onClick={async () => {
                            setDeleting(true);
                            try { await onDeleteProject({ id: project.id, name: project.name }); }
                            finally { setDeleting(false); setConfirmDelete(false); }
                          }}>
                          <Icon name="trash" size={14} />{deleting ? "Deleting…" : "Confirm delete"}
                        </button>
                        <button className="btn btn-ghost btn-sm" onClick={() => setConfirmDelete(false)} disabled={deleting}>Cancel</button>
                      </div>
                    )}
                  </div>
                )}
              </>
            )}

            {section === "history" && <SettingsHistory project={project} />}
          </div>
        </div>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title="Add secret" width={460}
        footer={<><button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={addSecret}>Save secret</button></>}>
        <Field label="Name"><input className="input mono" value={secForm.name} onChange={(e) => setSecForm((f) => ({ ...f, name: e.target.value }))} placeholder="openai_key" /></Field>
        <Field label="Value" help="Encrypted at rest; never shown again."><input className="input mono" type="password" value={secForm.value} onChange={(e) => setSecForm((f) => ({ ...f, value: e.target.value }))} /></Field>
        <Field label="Kind"><input className="input" value={secForm.kind} onChange={(e) => setSecForm((f) => ({ ...f, kind: e.target.value }))} /></Field>
      </Modal>
    </div>
  );
}

/* Settings > History: read-only per-section change log. Diffs consecutive project snapshots
   (captured on every settings save) and buckets each changed field under the matching settings
   tab. No restore - it's a log. Secret values are masked. */
function fmtVal(v: any): string {
  if (v === undefined || v === null || v === "") return "—";
  if (typeof v === "object") { try { return JSON.stringify(v); } catch { return String(v); } }
  return String(v);
}
function flattenConfig(snap: Record<string, any>): Record<string, any> {
  // Merge the top-level snapshot fields with config.* so section bucketing keys off the
  // outermost key (e.g. "budgets.max_usd" -> budgets), then flatten nested objects to dot-paths.
  const src = { name: snap.name, slug: snap.slug, description: snap.description, status: snap.status, ...(snap.config || {}) };
  const out: Record<string, any> = {};
  const walk = (o: any, prefix: string) => {
    for (const [k, v] of Object.entries(o || {})) {
      const path = prefix ? `${prefix}.${k}` : k;
      if (v && typeof v === "object" && !Array.isArray(v)) walk(v, path);
      else out[path] = v;
    }
  };
  walk(src, "");
  return out;
}
type FieldChange = { path: string; from: any; to: any };
type ChangeSet = { id: string; author?: string | null; at?: string | null; changes: FieldChange[] };

function SettingsHistory({ project }: { project: any }) {
  const [tab, setTab] = useState<SectionId>("general");
  const [versions, setVersions] = useState<ProjectVersion[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!project?.id) return;
    setVersions(null); setErr(null);
    api.projectConfigHistory(project.id).then(setVersions).catch((e) => setErr(String(e?.message || e)));
  }, [project?.id]);

  // The list is newest-first, so [i+1] is the older snapshot each version is diffed against.
  const changeSets: ChangeSet[] = useMemo(() => {
    if (!versions) return [];
    const out: ChangeSet[] = [];
    for (let i = 0; i < versions.length - 1; i++) {
      const newer = flattenConfig(versions[i].snapshot || {});
      const older = flattenConfig(versions[i + 1].snapshot || {});
      const changes: FieldChange[] = [];
      new Set([...Object.keys(newer), ...Object.keys(older)]).forEach((k) => {
        if (JSON.stringify(newer[k]) !== JSON.stringify(older[k])) changes.push({ path: k, from: older[k], to: newer[k] });
      });
      if (changes.length) out.push({ id: versions[i].id, author: versions[i].author_email, at: versions[i].created_at, changes });
    }
    return out;
  }, [versions]);

  const sectionOf = (path: string): SectionId => FIELD_SECTION[path.split(".")[0]] || "advanced";
  const forTab = changeSets
    .map((cs) => ({ ...cs, changes: cs.changes.filter((c) => sectionOf(c.path) === tab) }))
    .filter((cs) => cs.changes.length > 0);

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="field-help" style={{ marginTop: 0 }}>A read-only log of what changed in each settings section, newest first — captured on every save. Pick a section:</div>
      <Tabs
        equal
        tabs={HISTORY_TABS.map((id) => ({ value: id, label: SECTIONS.find((s) => s.id === id)!.label }))}
        value={tab}
        onChange={(v) => setTab(v as SectionId)}
      />
      {err && <div className="card" style={{ padding: 12, color: "var(--err)" }}>{err}</div>}
      {!err && versions === null && <div className="fg-2 t-caption">Loading history…</div>}
      {!err && versions !== null && forTab.length === 0 && (
        <div className="card col center" style={{ padding: 34, gap: 6, color: "var(--fg-2)" }}>
          <Icon name="clock" size={20} />
          <div className="t-body-sm">No changes recorded for this section.</div>
        </div>
      )}
      {forTab.map((cs) => (
        <div key={cs.id} className="card" style={{ padding: "12px 14px" }}>
          <div className="t-caption fg-2" style={{ marginBottom: 8 }}>{cs.author || "unknown"}{cs.at ? ` · ${new Date(cs.at).toLocaleString()}` : ""}</div>
          <div className="col" style={{ gap: 6 }}>
            {cs.changes.map((c) => {
              const mask = MASK_RE.test(c.path);
              return (
                <div key={c.path} className="row gap2" style={{ alignItems: "baseline", fontSize: 12 }}>
                  <span className="mono-sm" style={{ minWidth: 150, flex: "none", color: "var(--fg-1)" }}>{c.path}</span>
                  <span className="mono-sm fg-2 truncate" style={{ textDecoration: "line-through", minWidth: 0 }}>{mask ? "••••" : fmtVal(c.from)}</span>
                  <Icon name="chevright" size={12} style={{ color: "var(--fg-2)", flex: "none" }} />
                  <span className="mono-sm truncate" style={{ color: "var(--fg-0)", minWidth: 0 }}>{mask ? "••••" : fmtVal(c.to)}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

function TeamCard() {
  const [me, setMe] = useState<MeResult | null>(null);
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [invite, setInvite] = useState({ email: "", role: "editor" });
  const [msg, setMsg] = useState<string | null>(null);
  const [result, setResult] = useState<InviteResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const isAdmin = me ? me.role === "owner" || me.role === "admin" : false;
  const reload = useCallback(() => { api.listTeam().then(setMembers).catch(() => setMembers([])); }, []);
  useEffect(() => { api.me().then(setMe).catch(() => {}); }, []);
  useEffect(() => { if (isAdmin) reload(); }, [isAdmin, reload]);

  async function doInvite() {
    setMsg(null); setResult(null); setCopied(false);
    if (!invite.email.trim()) return;
    setBusy(true);
    try {
      const r = await api.inviteMember({ email: invite.email.trim(), role: invite.role });
      setResult(r);
      setInvite({ email: "", role: "editor" });
      reload();
    } catch { setMsg("Could not invite (that email may already be on the team)."); }
    finally { setBusy(false); }
  }
  function copyInvite() {
    if (!result?.invite_url) return;
    navigator.clipboard?.writeText(result.invite_url).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1800); }).catch(() => {});
  }
  async function setRole(uid: string, role: string) { try { await api.updateMember(uid, { role }); reload(); } catch { setMsg("Could not update role."); } }
  async function deactivate(uid: string) { if (!window.confirm("Deactivate this user?")) return; try { await api.deactivateMember(uid); reload(); } catch { setMsg("Could not deactivate."); } }
  function logout() { clearTokens(); window.location.reload(); }

  return (
    <Card title="Team & account" action={<button className="btn btn-secondary btn-sm" onClick={logout}><Icon name="external" size={14} />Sign out</button>}>
      {me && (
        <div className="field-help" style={{ marginTop: 0, marginBottom: 10 }}>
          Signed in as <b>{me.email || "(dev)"}</b> · role <span className="typechip">{me.role}</span>
          {me.is_fallback && <span className="pill pill-muted" style={{ height: 16, marginLeft: 8 }}>auth disabled (dev)</span>}
        </div>
      )}
      {isAdmin && (
        <>
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 12, marginBottom: 12, background: "var(--bg-1)" }}>
            <div className="t-body-sm" style={{ fontWeight: 600, marginBottom: 8 }}>Invite a teammate</div>
            <div className="row gap2" style={{ alignItems: "flex-end", flexWrap: "wrap" }}>
              <label className="col gap1" style={{ flex: "2 1 220px", minWidth: 200 }}>
                <span className="t-micro">Email</span>
                <input className="input" type="email" placeholder="teammate@company.com" value={invite.email}
                  onChange={(e) => setInvite((i) => ({ ...i, email: e.target.value }))} onKeyDown={(e) => { if (e.key === "Enter") doInvite(); }} />
              </label>
              <label className="col gap1" style={{ flex: "0 0 140px" }}>
                <span className="t-micro">Role</span>
                <select className="select" value={invite.role} onChange={(e) => setInvite((i) => ({ ...i, role: e.target.value }))}>
                  {ROLES.filter((r) => r !== "owner").map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
              </label>
              <button className="btn btn-primary btn-sm" onClick={doInvite} disabled={busy || !invite.email.trim()} style={{ height: 34 }}>
                <Icon name="plus" size={14} />{busy ? "Inviting…" : "Send invite"}
              </button>
            </div>
            <div className="field-help" style={{ marginTop: 8, marginBottom: 0 }}>
              They&apos;ll get an email with a secure link to set their own password and join the workspace.
            </div>
          </div>
          {result && (
            <div className="card" style={{ padding: 10, marginBottom: 12, background: "var(--bg-2)" }}>
              {result.email_sent ? (
                <div className="t-body-sm"><Icon name="check" size={13} style={{ color: "var(--ok)" }} /> Invitation emailed to <b>{result.email}</b>. They&apos;ll set their own password from the link.</div>
              ) : (
                <>
                  <div className="t-body-sm" style={{ marginBottom: 6 }}>Invite created for <b>{result.email}</b>. Email isn&apos;t configured on this server, so share this link - it lets them set their password and join:</div>
                  <div className="row gap2">
                    <input className="input mono" readOnly value={result.invite_url || ""} style={{ flex: 1, fontSize: 12 }} onFocus={(e) => e.currentTarget.select()} />
                    <button className="btn btn-secondary btn-sm" onClick={copyInvite}><Icon name={copied ? "check" : "copy"} size={13} />{copied ? "Copied" : "Copy"}</button>
                  </div>
                </>
              )}
            </div>
          )}
          {members.map((m) => (
            <div key={m.id} className="row spread" style={{ padding: "7px 0", borderTop: "1px solid var(--line)" }}>
              <div className="row gap2"><Icon name="user" size={15} style={{ color: "var(--fg-2)" }} /><span className="t-body-sm">{m.email}</span>{m.status !== "active" && <span className="pill pill-muted" style={{ height: 16 }}>{m.status}</span>}</div>
              <div className="row gap2">
                <select className="select" value={m.role} disabled={m.id === me?.id} onChange={(e) => setRole(m.id, e.target.value)}>
                  {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                {m.id !== me?.id && <button className="iconbtn" title="Deactivate" onClick={() => deactivate(m.id)}><Icon name="trash" size={14} /></button>}
              </div>
            </div>
          ))}
        </>
      )}
      {msg && <div className="t-caption" style={{ color: "var(--err)", marginTop: 8 }}>{msg}</div>}
    </Card>
  );
}

/* Model pricing editor - per-1M input/output token rates used to cost runs. Backed by
   GET/PUT /v1/pricing. Rows come from the known model catalog merged with any custom
   models already priced on the server. */
function PricingCard() {
  const [pricing, setPricing] = useState<Record<string, { input_per_1m: number; output_per_1m: number }>>({});
  const [draft, setDraft] = useState<Record<string, { input_per_1m: string; output_per_1m: string }>>({});
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");

  const reload = useCallback(() => {
    api.listPricing().then((p) => { setPricing(p || {}); setLoaded(true); }).catch((e) => { setErr(String(e?.message || e)); setLoaded(true); });
  }, []);
  useEffect(() => { reload(); }, [reload]);

  // Show every catalog model (minus the offline fake) plus any extra priced models.
  const rows = useMemo(() => {
    const ids = new Set<string>([...MODELS.filter((m) => m.id !== "fake:echo").map((m) => m.id), ...Object.keys(pricing)]);
    return Array.from(ids).sort();
  }, [pricing]);

  const val = (model: string, key: "input_per_1m" | "output_per_1m"): string => {
    const d = draft[model];
    if (d && d[key] !== undefined) return d[key];
    const p = pricing[model];
    return p && p[key] != null ? String(p[key]) : "";
  };
  const edit = (model: string, key: "input_per_1m" | "output_per_1m", v: string) =>
    setDraft((d) => ({ ...d, [model]: { input_per_1m: val(model, "input_per_1m"), output_per_1m: val(model, "output_per_1m"), [key]: v } }));

  async function saveAll() {
    setSave("saving");
    try {
      for (const [model, d] of Object.entries(draft)) {
        const input_per_1m = parseFloat(d.input_per_1m);
        const output_per_1m = parseFloat(d.output_per_1m);
        if (Number.isNaN(input_per_1m) && Number.isNaN(output_per_1m)) continue;
        await api.setPricing(model, { input_per_1m: input_per_1m || 0, output_per_1m: output_per_1m || 0 });
      }
      setDraft({});
      reload();
      setSave("saved"); setTimeout(() => setSave("idle"), 1400);
    } catch (e) { setErr(String((e as any)?.message || e)); setSave("idle"); }
  }

  const dirty = Object.keys(draft).length > 0;

  return (
    <Card title="Model pricing" action={<button className="btn btn-primary btn-sm" onClick={saveAll} disabled={!dirty || save === "saving"}><Icon name={save === "saved" ? "check" : "save"} size={14} />{save === "saving" ? "Saving…" : save === "saved" ? "Saved" : "Save pricing"}</button>}>
      <div className="field-help" style={{ marginTop: 0, marginBottom: 10 }}>Rates in USD per 1M tokens. Used to compute run cost in Traces and to enforce budgets.</div>
      {err && <div className="card" style={{ padding: 10, color: "var(--err)", marginBottom: 10 }}>{err}</div>}
      {!loaded ? (
        <div className="fg-2 t-caption">Loading pricing…</div>
      ) : rows.length === 0 ? (
        <EmptyState icon="coins" title="No models to price" sub="Add a model to the catalog to set its rates." />
      ) : (
        <table className="tbl tbl-dense">
          <thead><tr><th>Model</th><th style={{ textAlign: "right" }}>Input $/1M</th><th style={{ textAlign: "right" }}>Output $/1M</th></tr></thead>
          <tbody>
            {rows.map((model) => (
              <tr key={model}>
                <td><span className="mono-sm">{model}</span></td>
                <td style={{ textAlign: "right" }}>
                  <input className="input mono" type="number" min={0} step={0.01} style={{ width: 110, textAlign: "right", display: "inline-block" }}
                    value={val(model, "input_per_1m")} placeholder="—" onChange={(e) => edit(model, "input_per_1m", e.target.value)} />
                </td>
                <td style={{ textAlign: "right" }}>
                  <input className="input mono" type="number" min={0} step={0.01} style={{ width: 110, textAlign: "right", display: "inline-block" }}
                    value={val(model, "output_per_1m")} placeholder="—" onChange={(e) => edit(model, "output_per_1m", e.target.value)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function Card({ title, action, children }: { title: string; action?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: 18, marginBottom: 16 }}>
      <div className="row spread" style={{ marginBottom: 12 }}><div className="t-h2">{title}</div>{action}</div>
      {children}
    </div>
  );
}
