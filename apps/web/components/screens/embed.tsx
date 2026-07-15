"use client";
/* Console screen to manage the embeddable chat widget (Phase 3b/4): enable it, choose the
   workflow, allow-list embedding origins, and copy the iframe snippet + publishable key. */
import { useEffect, useState } from "react";
import { Icon } from "../icons";
import { api, Workflow } from "@/lib/api";

/* Reusable settings body (no page chrome) so it can live standalone OR as a section inside
   the Connect screen's secondary nav. The container is expected to supply the header. */
export function EmbedPanel({ project }: { project: any }) {
  const [enabled, setEnabled] = useState(false);
  const [origins, setOrigins] = useState("");
  const [workflowId, setWorkflowId] = useState("");
  const [pubKey, setPubKey] = useState<string | null>(null);
  const [wfs, setWfs] = useState<Workflow[]>([]);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [origin, setOrigin] = useState("");

  useEffect(() => { if (typeof window !== "undefined") setOrigin(window.location.origin); }, []);
  useEffect(() => {
    if (!project?.id) return;
    api.getEmbed(project.id).then((e) => {
      setEnabled(e.enabled);
      setOrigins((e.allowed_origins || []).join("\n"));
      setWorkflowId(e.workflow_id || "");
      setPubKey(e.publishable_key || null);
    }).catch(() => {});
    api.listWorkflows(project.id).then(setWfs).catch(() => {});
  }, [project?.id]);

  async function save() {
    setSaving(true);
    setStatus(null);
    try {
      const e = await api.setEmbed(project.id, {
        enabled,
        allowed_origins: origins.split(/\s+/).map((s) => s.trim()).filter(Boolean),
        workflow_id: workflowId || null,
      });
      setPubKey(e.publishable_key || null);
      setStatus("Saved.");
      setTimeout(() => setStatus(null), 1600);
    } catch (e: any) {
      setStatus(`Save failed: ${e.message || e}`);
    } finally {
      setSaving(false);
    }
  }

  const src = pubKey && enabled ? `${origin}/embed?key=${pubKey}` : null;
  const launcherSnippet = src
    ? `<script src="${origin}/launcher.js"\n  data-forge-key="${pubKey}"\n  data-forge-origin="${origin}"\n  data-forge-title="${(project?.name || "Chat").replace(/"/g, "&quot;")}"\n  defer></script>`
    : null;
  const iframeSnippet = src ? `<iframe src="${src}" style="border:0;width:400px;height:600px"></iframe>` : null;

  return (
    <div className="col gap4">
        <div className="card col gap3" style={{ padding: 18 }}>
          <label className="row gap2" style={{ alignItems: "center", cursor: "pointer" }}>
            <span className={"toggle" + (enabled ? " on" : "")} onClick={() => setEnabled((v) => !v)} role="switch" aria-checked={enabled} />
            <span className="t-body-sm">Enable the embeddable widget</span>
          </label>
          <div>
            <label className="field-label">Workflow</label>
            <select className="select" value={workflowId} onChange={(e) => setWorkflowId(e.target.value)}>
              <option value="">Active workflow</option>
              {wfs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
            <div className="field-help">Which workflow the widget runs.</div>
          </div>
          <div>
            <label className="field-label">Allowed origins</label>
            <textarea className="textarea mono" value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder={"https://yoursite.com\nhttps://app.yoursite.com"} style={{ minHeight: 70, fontSize: 12 }} spellCheck={false} />
            <div className="field-help">Sites permitted to embed the widget, one per line. Empty = only this Forge origin (external embedding blocked). Enforced via the page's frame-ancestors policy.</div>
          </div>
          <div className="row gap2" style={{ alignItems: "center" }}>
            <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
              <Icon name={saving ? "refresh" : "check"} size={14} style={saving ? { animation: "spin 1s linear infinite" } : {}} />
              {saving ? "Saving…" : "Save"}
            </button>
            {status && <span className="t-caption" style={{ color: status.includes("fail") ? "var(--err)" : "var(--ok)" }}>{status}</span>}
          </div>
        </div>

        {launcherSnippet ? (
          <div className="card col gap3" style={{ padding: 18 }}>
            <div>
              <div className="t-h3">Floating chat bubble (recommended)</div>
              <div className="field-help">Paste once before <span className="mono-sm">&lt;/body&gt;</span>. Adds a launcher button that opens the chat.</div>
            </div>
            <pre className="mono-sm" style={{ background: "var(--bg-3)", padding: 12, borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap" }}>{launcherSnippet}</pre>
            <div className="row gap2">
              <button className="btn btn-primary btn-sm" onClick={() => navigator.clipboard?.writeText(launcherSnippet)}>Copy bubble snippet</button>
              <a className="btn btn-ghost btn-sm" href={src!} target="_blank" rel="noreferrer">Open widget</a>
            </div>
            <div className="t-h3" style={{ marginTop: 6 }}>Inline iframe (advanced)</div>
            <pre className="mono-sm" style={{ background: "var(--bg-3)", padding: 12, borderRadius: 8, overflowX: "auto", whiteSpace: "pre-wrap" }}>{iframeSnippet}</pre>
            <button className="btn btn-secondary btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => navigator.clipboard?.writeText(iframeSnippet!)}>Copy iframe</button>
            <div className="field-help">
              Publishable key: <span className="mono-sm">{pubKey}</span>. For logged-in users, have your backend mint a session token (POST /v1/projects/{project?.id}/session-tokens) and add <span className="mono-sm">data-forge-token="…"</span> (bubble) or <span className="mono-sm">&amp;session_token=…</span> (iframe) so the agent knows who is chatting and can honor their entitlements.
            </div>
          </div>
        ) : (
          <div className="fg-2 t-body-sm">Enable and save to get a publishable key + embed snippet.</div>
        )}
    </div>
  );
}

/* Standalone screen (kept for direct/deep-link navigation): page chrome + the panel. */
export function EmbedScreen({ project }: { project: any }) {
  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div style={{ maxWidth: 960, margin: "0 auto" }} className="col gap4">
        <div className="row gap2">
          <div>
            <div className="t-display">Embed</div>
            <div className="fg-1" style={{ marginTop: 3 }}>Drop this project&apos;s chatbot into any website as a widget.</div>
          </div>
        </div>
        <EmbedPanel project={project} />
      </div>
    </div>
  );
}
