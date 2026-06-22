"use client";
/* Auth Providers - master/detail: left list, right Strategy + Credentials forms + masked test. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, StatusPill, Tile, Toggle } from "../primitives";
import { api, AuthProviderT, Tool } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  csrf_session: "CSRF + session", oauth2_client_credentials: "OAuth2 client-creds", oauth2_authorization_code: "OAuth2 (user login)", bearer: "Bearer token", basic: "Basic auth", api_key: "API key", custom_script: "Custom script",
};

// One-line "use this when…" per strategy - shown in the create picker so the choice is legible.
const KIND_DESC: Record<string, string> = {
  bearer: "A static token sent in a header. The simplest option.",
  api_key: "A key sent as a header or query param.",
  basic: "Username + password (HTTP Basic).",
  oauth2_client_credentials: "Machine-to-machine - Forge trades a client id/secret for a short-lived token.",
  oauth2_authorization_code: "A user signs in on the provider's consent page; tokens auto-refresh.",
  csrf_session: "Log in to a web app, capture its CSRF token + session cookie, and replay them. For targets with no real API auth.",
};

const TEMPLATES: Record<string, any> = {
  bearer: { kind: "bearer", token_ref: "secret://proj/token", header_name: "Authorization", prefix: "Bearer " },
  api_key: { kind: "api_key", in: "header", name: "X-API-Key", value_ref: "secret://proj/api_key" },
  basic: { kind: "basic", username_ref: "secret://proj/user", password_ref: "secret://proj/pass" },
  oauth2_client_credentials: { kind: "oauth2_client_credentials", token_url: "https://idp.example.com/oauth/token", scope: "read", client_id_ref: "secret://proj/client_id", client_secret_ref: "secret://proj/client_secret" },
  oauth2_authorization_code: { kind: "oauth2_authorization_code", authorize_url: "https://accounts.example.com/o/oauth2/v2/auth", token_url: "https://oauth2.example.com/token", scope: "openid email", client_id_ref: "secret://proj/client_id", client_secret_ref: "secret://proj/client_secret" },
  csrf_session: {
    kind: "csrf_session", credentials_ref: "secret://proj/creds",
    token_fetch: { method: "POST", url: "https://app.example.com/login", headers: { "Content-Type": "application/json" }, body: { username: "{{cred.username}}", password: "{{cred.password}}" } },
    extract: [{ name: "csrf", from: "header", header: "X-CSRF-Token" }, { name: "session", from: "cookie", cookie: "SESSIONID" }],
    inject: [{ to: "header", name: "X-CSRF-Token", value: "{{extracted.csrf}}" }, { to: "cookie", name: "SESSIONID", value: "{{extracted.session}}" }],
    cache_ttl_seconds: 1800, refresh_on: [401, 403],
  },
};

export function AuthProvidersScreen({ project }: { project: any }) {
  const [rows, setRows] = useState<AuthProviderT[]>([]);
  const [tools, setTools] = useState<Tool[]>([]);
  const [selId, setSelId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const reload = useCallback(() => {
    if (!project?.id) return;
    api.listAuthProviders(project.id).then((r) => { setRows(r); setSelId((s) => s && r.some((x) => x.id === s) ? s : (r[0]?.id ?? null)); }).catch(() => {});
    api.listTools(project.id).then(setTools).catch(() => {});
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  const toolCount = useMemo(() => {
    const m: Record<string, number> = {};
    tools.forEach((t) => { if (t.auth_provider_id) m[t.auth_provider_id] = (m[t.auth_provider_id] || 0) + 1; });
    return m;
  }, [tools]);

  const sel = rows.find((r) => r.id === selId) || null;

  return (
    <div style={{ flex: 1, display: "flex", minHeight: 0 }}>
      {/* LEFT list */}
      <div style={{ width: 280, flex: "none", borderRight: "1px solid var(--line)", display: "flex", flexDirection: "column", background: "var(--bg-1)" }}>
        <div className="row spread" style={{ padding: "14px 16px", borderBottom: "1px solid var(--line)" }}>
          <div className="t-h1">Auth Providers</div>
          <button className="btn btn-primary btn-sm" onClick={() => setCreateOpen(true)}><Icon name="plus" size={14} /></button>
        </div>
        <div className="scroll-y" style={{ flex: 1, padding: 8 }}>
          {rows.length === 0 && <div className="fg-2 t-caption" style={{ padding: 12 }}>No providers yet. Click + to add one.</div>}
          {rows.map((p) => {
            const on = selId === p.id;
            return (
              <button key={p.id} onClick={() => setSelId(p.id)} className="col" style={{ width: "100%", textAlign: "left", padding: "11px 12px", borderRadius: 9, marginBottom: 4, border: "1px solid " + (on ? "var(--accent)" : "transparent"), background: on ? "var(--accent-glow)" : "transparent", cursor: "pointer", gap: 4 }}>
                <div className="row spread"><span className="mono-sm" style={{ fontWeight: 700, color: "var(--fg-0)" }}>{p.name}</span><StatusPill status="untested" /></div>
                <div className="row spread">
                  <div className="row gap2" style={{ fontSize: 11, color: "var(--fg-2)" }}><span>{KIND_LABEL[p.kind] || p.kind}</span><span>· {toolCount[p.id] || 0} tools</span></div>
                  <span
                    className="iconbtn" role="button" title="Delete provider"
                    onClick={async (e) => {
                      e.stopPropagation();
                      const used = toolCount[p.id] || 0;
                      const warn = used ? ` ${used} tool(s) reference it and will lose auth.` : "";
                      if (!window.confirm(`Delete auth provider “${p.name}”?${warn}`)) return;
                      await api.deleteAuthProvider(project.id, p.id);
                      reload();
                    }}
                  ><Icon name="trash" size={13} /></span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* RIGHT detail */}
      <div className="grow scroll-y" style={{ padding: 24, minWidth: 0 }}>
        {sel ? <ProviderDetail key={sel.id} project={project} provider={sel} onSaved={reload} /> : (
          <div className="col center" style={{ height: "100%", gap: 8, color: "var(--fg-2)" }}><Tile icon="auth" color="var(--accent)" size={48} glow /><div className="t-h2">Select or add a provider</div></div>
        )}
      </div>

      <CreateModal open={createOpen} onClose={() => setCreateOpen(false)} onCreate={async (name, kind) => {
        const cfg = TEMPLATES[kind] || { kind };
        const ap = await api.createAuthProvider(project.id, { name: name || kind, kind, config: cfg, credentials_ref: cfg.credentials_ref });
        setCreateOpen(false); reload(); setSelId(ap.id);
      }} />
    </div>
  );
}

function CreateModal({ open, onClose, onCreate }: { open: boolean; onClose: () => void; onCreate: (name: string, kind: string) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("bearer");
  return (
    <Modal open={open} onClose={onClose} title="New auth provider" width={520}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" onClick={() => onCreate(name.trim().replace(/\s+/g, "_"), kind)}>Create</button></>}>
      <Field label="Strategy" help="How the target API expects to be authenticated - pick whichever scheme it requires.">
        <div className="col gap2">
          {Object.keys(TEMPLATES).map((k) => {
            const on = kind === k;
            return (
              <button key={k} type="button" onClick={() => setKind(k)} className="col"
                style={{ width: "100%", textAlign: "left", padding: "10px 12px", borderRadius: 9, gap: 3, cursor: "pointer", border: "1px solid " + (on ? "var(--accent)" : "var(--line)"), background: on ? "var(--accent-glow)" : "var(--bg-1)" }}>
                <span style={{ fontWeight: 700, color: "var(--fg-0)" }}>{KIND_LABEL[k]}</span>
                <span className="t-caption fg-2">{KIND_DESC[k]}</span>
              </button>
            );
          })}
        </div>
      </Field>
      <Field label="Name"><input className="input mono" value={name} onChange={(e) => setName(e.target.value)} placeholder="orders_api" /></Field>
    </Modal>
  );
}

function ProviderDetail({ project, provider, onSaved }: { project: any; provider: AuthProviderT; onSaved: () => void }) {
  const [cfg, setCfg] = useState<any>(() => ({ ...(provider.config || {}), kind: provider.kind }));
  const [name, setName] = useState(provider.name);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [test, setTest] = useState<any>(null);
  const [reveal, setReveal] = useState(false);

  const kind = cfg.kind;
  function setPath(path: string[], value: any) {
    setCfg((c: any) => {
      const next = structuredClone(c); let o = next;
      for (let i = 0; i < path.length - 1; i++) { o[path[i]] = o[path[i]] ?? {}; o = o[path[i]]; }
      o[path[path.length - 1]] = value; return next;
    });
    setSaved(false);
  }
  const get = (path: string[], dflt: any = "") => path.reduce((o, k) => (o == null ? o : o[k]), cfg) ?? dflt;

  async function save() {
    setSaving(true);
    try {
      const updated = await api.updateAuthProvider(project.id, provider.id, { name, kind, config: cfg, credentials_ref: cfg.credentials_ref });
      setSaved(true); onSaved();
    } catch { /* */ } finally { setSaving(false); }
  }
  async function runTest() {
    const r = await api.testAuthProvider(project.id, provider.id, {});
    setTest(r);
  }

  // csrf_session uses extract/inject arrays; surface the first CSRF rule for editing.
  const extract = cfg.extract || [];
  const csrfRule = extract.find((e: any) => e.from === "header") || extract[0] || {};
  const jsonRule = extract.find((e: any) => e.from === "json") || {};
  function setExtractField(field: string, value: string) {
    const ext = [...(cfg.extract || [])];
    const idx = ext.findIndex((e: any) => e === csrfRule);
    if (idx >= 0) ext[idx] = { ...ext[idx], [field]: value };
    setPath(["extract"], ext);
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="row spread" style={{ marginBottom: 18 }}>
        <div className="row gap3">
          <Tile icon="auth" color="var(--accent)" size={40} glow />
          <div><div className="t-display mono" style={{ fontSize: 18 }}>{provider.name}</div><div className="fg-2 t-caption">{KIND_LABEL[kind] || kind} · ttl {cfg.cache_ttl_seconds || 1800}s</div></div>
        </div>
        <div className="row gap2">
          <button className="btn btn-secondary" onClick={runTest}><Icon name="validate" size={15} />Test connection</button>
          <button className="btn btn-primary" onClick={save} disabled={saving}><Icon name="save" size={15} />{saving ? "Saving…" : saved ? "Saved ✓" : "Save"}</button>
        </div>
      </div>

      {test && (
        <div className="card" style={{ padding: 12, marginBottom: 16, background: "var(--bg-3)" }}>
          {test.ok
            ? <div className="col gap1"><div className="t-caption fg-2">Would inject (masked):</div><pre className="mono-sm" style={{ margin: 0 }}>{JSON.stringify({ headers: test.headers, cookies: test.cookies, params: test.params }, null, 2)}</pre></div>
            : <div className="t-caption" style={{ color: "var(--err)" }}>{test.error}</div>}
        </div>
      )}

      {/* Strategy */}
      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <div className="t-h2" style={{ marginBottom: 14 }}>Strategy</div>
        <Field label="Name"><input className="input mono" value={name} onChange={(e) => { setName(e.target.value); setSaved(false); }} /></Field>
        <Field label="Type">
          <div style={{ position: "relative" }}>
            <select className="select" value={kind} onChange={(e) => { const k = e.target.value; setCfg({ ...(TEMPLATES[k] || { kind: k }) }); setSaved(false); }}>
              {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <Icon name="chevdown" size={13} style={{ position: "absolute", right: 9, top: 9, pointerEvents: "none", color: "var(--fg-2)" }} />
          </div>
        </Field>

        {kind === "csrf_session" && (
          <>
            <div className="row gap4">
              <Field label="Login URL"><input className="input mono" value={get(["token_fetch", "url"])} onChange={(e) => setPath(["token_fetch", "url"], e.target.value)} /></Field>
              <Field label="Method"><input className="input mono" value={get(["token_fetch", "method"], "POST")} onChange={(e) => setPath(["token_fetch", "method"], e.target.value)} /></Field>
            </div>
            <div className="row gap4">
              <Field label="CSRF header"><input className="input mono" value={csrfRule.header || ""} onChange={(e) => setExtractField("header", e.target.value)} /></Field>
              <Field label="CSRF JSON path"><input className="input mono" value={jsonRule.json_path || ""} onChange={(e) => setExtractField("json_path", e.target.value)} placeholder="data.csrfToken" /></Field>
            </div>
            <Field label="Session TTL" help="Auto re-login on expiry or 401."><input className="input mono" value={get(["cache_ttl_seconds"], 1800)} onChange={(e) => setPath(["cache_ttl_seconds"], Number(e.target.value) || 0)} /></Field>
          </>
        )}
        {kind === "oauth2_client_credentials" && (
          <>
            <div className="row gap4">
              <Field label="Token URL"><input className="input mono" value={get(["token_url"])} onChange={(e) => setPath(["token_url"], e.target.value)} /></Field>
              <Field label="Scope"><input className="input mono" value={get(["scope"])} onChange={(e) => setPath(["scope"], e.target.value)} /></Field>
            </div>
          </>
        )}
        {kind === "oauth2_authorization_code" && (
          <>
            <Field label="Authorize URL" help="The provider's consent page the user is redirected to."><input className="input mono" value={get(["authorize_url"])} onChange={(e) => setPath(["authorize_url"], e.target.value)} /></Field>
            <div className="row gap4">
              <Field label="Token URL"><input className="input mono" value={get(["token_url"])} onChange={(e) => setPath(["token_url"], e.target.value)} /></Field>
              <Field label="Scope"><input className="input mono" value={get(["scope"])} onChange={(e) => setPath(["scope"], e.target.value)} /></Field>
            </div>
            <OAuthConnect project={project} provider={provider} />
          </>
        )}
        {kind === "api_key" && (
          <div className="row gap4">
            <Field label="In"><div style={{ position: "relative" }}><select className="select" value={get(["in"], "header")} onChange={(e) => setPath(["in"], e.target.value)}><option value="header">header</option><option value="query">query</option></select></div></Field>
            <Field label="Param name"><input className="input mono" value={get(["name"])} onChange={(e) => setPath(["name"], e.target.value)} /></Field>
          </div>
        )}
        {kind === "bearer" && (
          <div className="row gap4">
            <Field label="Header name"><input className="input mono" value={get(["header_name"], "Authorization")} onChange={(e) => setPath(["header_name"], e.target.value)} /></Field>
            <Field label="Prefix"><input className="input mono" value={get(["prefix"], "Bearer ")} onChange={(e) => setPath(["prefix"], e.target.value)} /></Field>
          </div>
        )}
      </div>

      {/* Credentials */}
      <div className="card" style={{ padding: 18 }}>
        <div className="row spread" style={{ marginBottom: 12 }}><div className="t-h2">Credentials</div><span className="chip" style={{ color: "var(--fg-2)" }}><Icon name="secret" size={12} />from secret store</span></div>
        {credentialFields(kind).map((cf) => (
          <Field key={cf.path} label={cf.label} help={cf.help}>
            <div className="row gap2">
              <input className="input mono" type={reveal ? "text" : "password"} value={get([cf.path])} onChange={(e) => setPath([cf.path], e.target.value)} style={{ flex: 1 }} placeholder="secret://proj/…" />
              <button className="iconbtn" style={{ border: "1px solid var(--line-strong)" }} onClick={() => setReveal((r) => !r)}><Icon name={reveal ? "eye" : "eye"} size={15} /></button>
            </div>
          </Field>
        ))}
        <div className="fg-2 t-caption" style={{ marginTop: 4 }}>Secret values live in Settings → Secrets. Reference them as <span className="mono-sm">secret://proj/&lt;name&gt;</span>.</div>
      </div>
    </div>
  );
}

function OAuthConnect({ project, provider }: { project: any; provider: AuthProviderT }) {
  const [status, setStatus] = useState<{ connected: boolean; scope?: string | null } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const refresh = useCallback(() => { api.oauthStatus(project.id, provider.id).then(setStatus).catch(() => setStatus(null)); }, [project.id, provider.id]);
  useEffect(() => { refresh(); }, [refresh]);

  async function connect() {
    setErr(null);
    try {
      const { authorize_url } = await api.oauthStart(project.id, provider.id);
      const w = window.open(authorize_url, "_blank", "width=620,height=760");
      // Poll status a few times after the popup so the badge flips to "connected".
      const t = setInterval(() => refresh(), 2500);
      setTimeout(() => { clearInterval(t); try { w?.close(); } catch { /* ignore */ } }, 60000);
    } catch {
      setErr("Could not start OAuth - save the provider first and set the client_id secret in Settings → Secrets.");
    }
  }

  return (
    <div className="card" style={{ padding: 14, marginTop: 8, background: "var(--bg-3)" }}>
      <div className="row spread">
        <div className="row gap2">
          <Icon name="link" size={15} />
          <span className="t-body-sm" style={{ fontWeight: 600 }}>User authorization</span>
          {status?.connected ? <span className="pill pill-ok"><span className="dot" />connected</span> : <span className="pill pill-muted">not connected</span>}
        </div>
        <button className="btn btn-primary btn-sm" onClick={connect}><Icon name="external" size={13} />{status?.connected ? "Reconnect" : "Connect"}</button>
      </div>
      {status?.scope && <div className="fg-2 t-caption" style={{ marginTop: 6 }}>scope: {status.scope}</div>}
      {err && <div className="t-caption" style={{ color: "var(--danger, #d33)", marginTop: 6 }}>{err}</div>}
      <div className="fg-2 t-caption" style={{ marginTop: 6 }}>Save the provider, set the client_id/secret secrets, then Connect. A popup completes the grant; tokens auto-refresh.</div>
    </div>
  );
}

function credentialFields(kind: string): { path: string; label: string; help?: string }[] {
  switch (kind) {
    case "csrf_session": return [{ path: "credentials_ref", label: "Credentials secret ref", help: "Holds { username, password } for the login call." }];
    case "bearer": return [{ path: "token_ref", label: "Token secret ref" }];
    case "api_key": return [{ path: "value_ref", label: "API key secret ref" }];
    case "basic": return [{ path: "username_ref", label: "Username secret ref" }, { path: "password_ref", label: "Password secret ref" }];
    case "oauth2_client_credentials":
    case "oauth2_authorization_code": return [{ path: "client_id_ref", label: "Client ID secret ref" }, { path: "client_secret_ref", label: "Client secret ref" }];
    default: return [{ path: "credentials_ref", label: "Credentials secret ref" }];
  }
}
