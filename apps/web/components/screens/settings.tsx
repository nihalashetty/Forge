"use client";
/* Settings: project config (model, budgets, features) + secrets (write-only). */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, StatusPill, Tile, Toggle } from "../primitives";
import { api, AuditEntry, clearTokens, InviteResult, MeResult, Secret, TeamMember } from "@/lib/api";
import { MODELS } from "@/lib/data";

const ROLES = ["owner", "admin", "editor", "viewer"];

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
              They’ll get an email with a secure link to set their own password and join the workspace.
            </div>
          </div>
          {result && (
            <div className="card" style={{ padding: 10, marginBottom: 12, background: "var(--bg-2)" }}>
              {result.email_sent ? (
                <div className="t-body-sm"><Icon name="check" size={13} style={{ color: "var(--ok, #2a8)" }} /> Invitation emailed to <b>{result.email}</b>. They’ll set their own password from the link.</div>
              ) : (
                <>
                  <div className="t-body-sm" style={{ marginBottom: 6 }}>Invite created for <b>{result.email}</b>. Email isn’t configured on this server, so share this link - it lets them set their password and join:</div>
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
      {msg && <div className="t-caption" style={{ color: "var(--danger, #d33)", marginTop: 8 }}>{msg}</div>}
    </Card>
  );
}

export function SettingsScreen({ project }: { project: any }) {
  const [config, setConfig] = useState<Record<string, any>>({});
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [save, setSave] = useState<"idle" | "saving" | "saved">("idle");
  const [open, setOpen] = useState(false);
  const [secForm, setSecForm] = useState({ name: "", value: "", kind: "api_key" });
  const [pkeys, setPkeys] = useState<Record<string, string>>({});
  const [keySave, setKeySave] = useState<"idle" | "saving" | "saved">("idle");

  const reloadSecrets = useCallback(() => { if (project?.id) api.listSecrets(project.id).then(setSecrets).catch(() => {}); }, [project?.id]);
  useEffect(() => {
    if (!project?.id) return;
    api.getProject(project.id).then((p) => setConfig(p.config || {})).catch(() => {});
    reloadSecrets();
  }, [project?.id, reloadSecrets]);

  const setCfg = (patch: Record<string, any>) => setConfig((c) => ({ ...c, ...patch }));
  const features = config.features || {};
  const budgets = config.budgets || {};

  async function persist() {
    setSave("saving");
    await api.updateProject(project.id, { config });
    setSave("saved"); setTimeout(() => setSave("idle"), 1400);
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
    if (!window.confirm(`Delete secret “${name}”?${detail}`)) return;
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

  return (
    <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}>
      <div className="fade-up" style={{ maxWidth: 880, margin: "0 auto" }}>
        <div className="row spread" style={{ marginBottom: 18 }}>
          <div className="t-display">Settings</div>
          <button className="btn btn-primary btn-sm" onClick={persist} disabled={save === "saving"}><Icon name={save === "saved" ? "check" : "save"} size={14} />{save === "saving" ? "Saving…" : save === "saved" ? "Saved" : "Save"}</button>
        </div>

        <TeamCard />

        <Card title="Model & budgets">
          <Field label="Default model">
            <select className="select" value={config.default_model || ""} onChange={(e) => setCfg({ default_model: e.target.value })}>
              <option value="fake:echo">fake:echo (offline)</option>
              {MODELS.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.provider}</option>)}
            </select>
          </Field>
          <div className="row gap3">
            <Field label="Max $/run"><input className="input mono" type="number" value={budgets.max_usd_per_run ?? ""} onChange={(e) => setCfg({ budgets: { ...budgets, max_usd_per_run: parseFloat(e.target.value) || undefined } })} /></Field>
            <Field label="Monthly $ cap"><input className="input mono" type="number" value={budgets.monthly_usd_cap ?? ""} onChange={(e) => setCfg({ budgets: { ...budgets, monthly_usd_cap: parseFloat(e.target.value) || undefined } })} /></Field>
          </div>
        </Card>

        <Card title="Model providers" action={<button className="btn btn-primary btn-sm" onClick={saveKeys} disabled={keySave === "saving"}><Icon name={keySave === "saved" ? "check" : "save"} size={14} />{keySave === "saving" ? "Saving…" : keySave === "saved" ? "Saved" : "Save keys"}</button>}>
          <div className="field-help" style={{ marginTop: 0, marginBottom: 6 }}>Keys are encrypted (Fernet), bound to this project's models, and never returned. They fall back to the server's env var if unset.</div>
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

        <Card title="Feature flags">
          {[["code_nodes", "Code nodes", "Allow sandboxed code execution"], ["remote_sandbox", "Remote sandbox", "Use E2B/Modal/Daytona for code"], ["advanced_scripts", "Advanced scripts", "RestrictedPython custom auth scripts"]].map(([k, label, desc]) => (
            <label key={k} className="row spread" style={{ padding: "8px 0" }}>
              <div><div className="t-body-sm" style={{ fontWeight: 600 }}>{label}</div><div className="field-help" style={{ marginTop: 0 }}>{desc}</div></div>
              <Toggle on={!!features[k]} onChange={(v) => setCfg({ features: { ...features, [k]: v } })} />
            </label>
          ))}
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

        <AuditCard project={project} />
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

function AuditCard({ project }: { project: any }) {
  const [rows, setRows] = useState<AuditEntry[]>([]);
  const [denied, setDenied] = useState(false);
  useEffect(() => { if (project?.id) api.listAudit(project.id).then(setRows).catch(() => setDenied(true)); }, [project?.id]);
  if (denied) return null; // non-admins don't see the audit log
  return (
    <Card title="Audit log">
      <div className="field-help" style={{ marginTop: 0, marginBottom: 8 }}>Recent create/update/delete and auth events in this workspace (admin only).</div>
      {rows.slice(0, 40).map((a) => (
        <div key={a.id} className="row spread" style={{ padding: "5px 0", borderTop: "1px solid var(--line)" }}>
          <div className="row gap2"><span className="mono-sm">{a.action}</span>{a.status !== "ok" && <span className="pill pill-muted" style={{ height: 16 }}>{a.status}</span>}</div>
          <div className="row gap2 fg-2 t-caption"><span>{a.actor_email || "-"}</span><span>{a.at ? new Date(a.at).toLocaleString() : ""}</span></div>
        </div>
      ))}
      {rows.length === 0 && <div className="fg-2 t-caption">No audit events yet.</div>}
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
