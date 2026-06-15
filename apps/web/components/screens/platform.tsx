"use client";
/* Screens for the platform features: Channels, Triggers, Datasets (eval),
   and the live-agent Handoff inbox. */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal } from "../primitives";
import { api, Channel, Dataset, EvalReport, Handoff, Trigger, Workflow } from "@/lib/api";

function Header({ title, subtitle, action }: { title: string; subtitle?: string; action?: React.ReactNode }) {
  return (
    <div className="row spread" style={{ marginBottom: 18 }}>
      <div>
        <div className="t-display">{title}</div>
        {subtitle && <div className="fg-1" style={{ marginTop: 2 }}>{subtitle}</div>}
      </div>
      {action}
    </div>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div className="scroll-y" style={{ flex: 1, padding: "24px 28px" }}><div className="fade-up" style={{ maxWidth: 980, margin: "0 auto" }}>{children}</div></div>;
}

function useWorkflows(pid?: string) {
  const [wfs, setWfs] = useState<Workflow[]>([]);
  useEffect(() => { if (pid) api.listWorkflows(pid).then(setWfs).catch(() => setWfs([])); }, [pid]);
  return wfs;
}

/* ============ CHANNELS ============ */
type ChannelForm = { id?: string; type: string; name: string; workflow_id: string; config: any };
const BLANK_CHANNEL: ChannelForm = { type: "email", name: "", workflow_id: "", config: {} };

export function ChannelsScreen({ project }: { project: any }) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const wfs = useWorkflows(project?.id);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ChannelForm>(BLANK_CHANNEL);
  const reload = useCallback(() => { if (project?.id) api.listChannels(project.id).then(setChannels).catch(() => setChannels([])); }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  const setCfg = (patch: any) => setForm((f) => ({ ...f, config: { ...f.config, ...patch } }));
  const setSmtp = (patch: any) => setForm((f) => ({ ...f, config: { ...f.config, smtp: { ...(f.config.smtp || {}), ...patch } } }));
  const c = form.config || {};
  const smtp = c.smtp || {};

  async function save() {
    if (!form.name.trim()) return;
    if (form.id) await api.updateChannel(project.id, form.id, { name: form.name, workflow_id: form.workflow_id || undefined, config: form.config });
    else await api.createChannel(project.id, { type: form.type, name: form.name, workflow_id: form.workflow_id || undefined, config: form.config });
    setOpen(false); setForm(BLANK_CHANNEL); reload();
  }
  function edit(ch: Channel) { setForm({ id: ch.id, type: ch.type, name: ch.name, workflow_id: ch.workflow_id || "", config: ch.config || {} }); setOpen(true); }
  async function remove(id: string) { if (window.confirm("Delete this channel?")) { await api.deleteChannel(project.id, id); reload(); } }
  const urlOf = (ch: Channel) => ch.inbound_url || ch.messaging_endpoint;

  return (
    <Shell>
      <Header title="Channels" subtitle="Deploy a workflow to a surface: email or Microsoft Teams."
        action={<button className="btn btn-primary btn-sm" onClick={() => { setForm(BLANK_CHANNEL); setOpen(true); }}><Icon name="plus" size={14} />New channel</button>} />
      <div className="col gap2">
        {channels.map((ch) => (
          <div key={ch.id} className="card" style={{ padding: 14 }}>
            <div className="row spread">
              <div className="row gap2"><Icon name="msg" size={16} /><span className="t-h3">{ch.name}</span><span className="typechip">{ch.type}</span>{!ch.enabled && <span className="pill pill-muted">disabled</span>}</div>
              <div className="row gap2"><button className="btn btn-secondary btn-sm" onClick={() => edit(ch)}><Icon name="edit" size={13} />Configure</button><button className="iconbtn" title="Delete" onClick={() => remove(ch.id)}><Icon name="trash" size={14} /></button></div>
            </div>
            {urlOf(ch) && <div className="mono-sm fg-2" style={{ marginTop: 8, wordBreak: "break-all" }}>{urlOf(ch)}</div>}
          </div>
        ))}
        {channels.length === 0 && <div className="fg-2 t-caption">No channels yet. Create one to deploy this project's workflow.</div>}
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={form.id ? "Configure channel" : "New channel"} width={500}
        footer={<><button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={save}>{form.id ? "Save" : "Create"}</button></>}>
        {!form.id && <Field label="Type"><select className="select" value={form.type} onChange={(e) => setForm((f) => ({ ...f, type: e.target.value }))}><option value="email">Email</option><option value="teams">Microsoft Teams</option></select></Field>}
        <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Support channel" /></Field>
        <Field label="Workflow" help="Which workflow handles messages on this channel."><select className="select" value={form.workflow_id} onChange={(e) => setForm((f) => ({ ...f, workflow_id: e.target.value }))}><option value="">First active workflow</option>{wfs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>

        {form.type === "email" && (
          <>
            <div className="field-help" style={{ marginTop: 0 }}>Outbound SMTP for replies. Inbound mail is posted to the channel&apos;s inbound URL by your provider (Mailgun/SendGrid/Postmark) or an IMAP relay.</div>
            <div className="row gap3">
              <Field label="SMTP host"><input className="input mono" value={smtp.host || ""} onChange={(e) => setSmtp({ host: e.target.value })} placeholder="smtp.sendgrid.net" /></Field>
              <Field label="Port"><input className="input mono" type="number" value={smtp.port ?? 587} onChange={(e) => setSmtp({ port: Number(e.target.value) })} /></Field>
            </div>
            <div className="row gap3">
              <Field label="Username"><input className="input mono" value={smtp.username || ""} onChange={(e) => setSmtp({ username: e.target.value })} /></Field>
              <Field label="From address"><input className="input mono" value={smtp.from || ""} onChange={(e) => setSmtp({ from: e.target.value })} placeholder="support@yourco.com" /></Field>
            </div>
            <Field label="Password secret ref" help="A secret holding the SMTP password (Settings → Secrets)."><input className="input mono" value={smtp.password_ref || ""} onChange={(e) => setSmtp({ password_ref: e.target.value })} placeholder="secret://proj/smtp_password" /></Field>
          </>
        )}

        {form.type === "teams" && (
          <>
            <div className="field-help" style={{ marginTop: 0 }}>From your Azure Bot registration. Point the bot&apos;s messaging endpoint at the channel&apos;s endpoint URL (shown after save).</div>
            <Field label="Azure app id"><input className="input mono" value={c.app_id || ""} onChange={(e) => setCfg({ app_id: e.target.value })} /></Field>
            <Field label="App password secret ref" help="Secret holding the bot app password."><input className="input mono" value={c.app_password_ref || ""} onChange={(e) => setCfg({ app_password_ref: e.target.value })} placeholder="secret://proj/teams_app_password" /></Field>
          </>
        )}
      </Modal>
    </Shell>
  );
}

/* ============ TRIGGERS ============ */
export function TriggersScreen({ project }: { project: any }) {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  useEffect(() => { if (project?.id) api.listTriggers(project.id).then(setTriggers).catch(() => setTriggers([])); }, [project?.id]);
  return (
    <Shell>
      <Header title="Triggers" subtitle="Event entry points, synced from your workflows' trigger nodes (Webhook / Schedule / Email / Chat / App Event)." />
      <div className="col gap2">
        {triggers.map((t) => (
          <div key={t.id} className="card" style={{ padding: 14 }}>
            <div className="row spread">
              <div className="row gap2"><Icon name="bolt" size={15} /><span className="t-h3" style={{ textTransform: "capitalize" }}>{t.kind.replace("_", " ")}</span><span className="typechip">{t.node_id}</span>{!t.enabled && <span className="pill pill-muted">disabled</span>}</div>
              {t.last_fired_at && <span className="fg-2 t-caption">last fired {new Date(t.last_fired_at).toLocaleString()}</span>}
            </div>
            {t.webhook_url && <div className="mono-sm fg-2" style={{ marginTop: 8, wordBreak: "break-all" }}>POST {t.webhook_url}</div>}
            {t.config?.cron && <div className="mono-sm fg-2" style={{ marginTop: 8 }}>cron: {t.config.cron}</div>}
            {t.config?.every_minutes && <div className="mono-sm fg-2" style={{ marginTop: 8 }}>every {t.config.every_minutes} min</div>}
            {t.config?.poll_url && <div className="mono-sm fg-2" style={{ marginTop: 8, wordBreak: "break-all" }}>polls {t.config.poll_url}</div>}
          </div>
        ))}
        {triggers.length === 0 && <div className="fg-2 t-caption">No triggers. Add a trigger node (Webhook / Schedule / …) to a workflow and publish it.</div>}
      </div>
    </Shell>
  );
}

/* ============ DATASETS / EVAL ============ */
export function DatasetsScreen({ project }: { project: any }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const wfs = useWorkflows(project?.id);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: "", workflow_id: "", score_mode: "contains", items: '[\n  {"input": "what are your hours?", "expected": "9am"}\n]' });
  const [report, setReport] = useState<EvalReport | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const reload = useCallback(() => { if (project?.id) api.listDatasets(project.id).then(setDatasets).catch(() => setDatasets([])); }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  async function create() {
    let items: any[] = [];
    try { items = JSON.parse(form.items); } catch { /* ignore */ }
    await api.createDataset(project.id, { name: form.name, workflow_id: form.workflow_id || undefined, score_mode: form.score_mode, items });
    setOpen(false); reload();
  }
  async function run(d: Dataset) { setRunning(d.id); setReport(null); try { setReport(await api.runDataset(project.id, d.id)); } finally { setRunning(null); reload(); } }

  return (
    <Shell>
      <Header title="Evaluations" subtitle="Run input → expected-output datasets against a workflow to score quality and catch regressions."
        action={<button className="btn btn-primary btn-sm" onClick={() => setOpen(true)}><Icon name="plus" size={14} />New dataset</button>} />
      <div className="col gap2">
        {datasets.map((d) => (
          <div key={d.id} className="card" style={{ padding: 14 }}>
            <div className="row spread">
              <div className="row gap2"><Icon name="validate" size={15} /><span className="t-h3">{d.name}</span><span className="typechip">{d.score_mode}</span><span className="fg-2 t-caption">{d.n_items} cases</span></div>
              <div className="row gap2">
                {d.last_pass_rate != null && <span className="pill" style={{ background: d.last_pass_rate >= 0.8 ? "var(--ok-bg, #e6f6ec)" : "var(--bg-3)" }}>{Math.round(d.last_pass_rate * 100)}% pass</span>}
                <button className="btn btn-secondary btn-sm" disabled={running === d.id} onClick={() => run(d)}><Icon name="play" size={13} />{running === d.id ? "Running…" : "Run"}</button>
              </div>
            </div>
          </div>
        ))}
        {datasets.length === 0 && <div className="fg-2 t-caption">No datasets yet.</div>}
      </div>
      {report && (
        <div className="card" style={{ padding: 16, marginTop: 16 }}>
          <div className="t-h3" style={{ marginBottom: 8 }}>Last run · {report.summary.passed}/{report.summary.total} passed ({Math.round(report.summary.pass_rate * 100)}%)</div>
          {report.results.map((r, i) => (
            <div key={i} className="row spread" style={{ padding: "6px 0", borderTop: "1px solid var(--line)" }}>
              <span className="t-caption truncate" style={{ flex: 1 }}>{r.input}</span>
              <span className="pill" style={{ background: r.passed ? "var(--ok-bg, #e6f6ec)" : "var(--danger-bg, #fde8e8)" }}>{r.passed ? "pass" : "fail"}</span>
            </div>
          ))}
        </div>
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="New dataset" width={520}
        footer={<><button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={create}>Create</button></>}>
        <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Smoke tests" /></Field>
        <Field label="Workflow"><select className="select" value={form.workflow_id} onChange={(e) => setForm((f) => ({ ...f, workflow_id: e.target.value }))}><option value="">Select…</option>{wfs.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></Field>
        <Field label="Scoring"><select className="select" value={form.score_mode} onChange={(e) => setForm((f) => ({ ...f, score_mode: e.target.value }))}><option value="contains">contains</option><option value="exact">exact</option><option value="regex">regex</option><option value="judge">LLM judge</option></select></Field>
        <Field label="Cases (JSON)" help='[{"input": "...", "expected": "..."}]'><textarea className="textarea mono" rows={6} style={{ fontSize: 12 }} value={form.items} onChange={(e) => setForm((f) => ({ ...f, items: e.target.value }))} /></Field>
      </Modal>
    </Shell>
  );
}

/* ============ HANDOFF INBOX ============ */
export function HandoffScreen({ project }: { project: any }) {
  const [items, setItems] = useState<Handoff[]>([]);
  const [reply, setReply] = useState<Record<string, string>>({});
  const reload = useCallback(() => { if (project?.id) api.listHandoffs(project.id, "open").then(setItems).catch(() => setItems([])); }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  async function send(h: Handoff) {
    const msg = (reply[h.id] || "").trim();
    if (!msg) return;
    await api.replyHandoff(project.id, h.id, msg);
    setReply((r) => ({ ...r, [h.id]: "" })); reload();
  }

  return (
    <Shell>
      <Header title="Agent inbox" subtitle="Conversations escalated to a human. Replying resumes the paused run and delivers your message over its channel." />
      <div className="col gap2">
        {items.map((h) => (
          <div key={h.id} className="card" style={{ padding: 14 }}>
            <div className="row spread" style={{ marginBottom: 8 }}>
              <div className="row gap2"><Icon name="user" size={15} /><span className="t-h3">{h.customer || "Customer"}</span></div>
              <span className="fg-2 t-caption">{h.reason}</span>
            </div>
            {h.customer_message && <div style={{ background: "var(--bg-3)", padding: "8px 11px", borderRadius: 10, fontSize: 13, marginBottom: 8 }}>{h.customer_message}</div>}
            <div className="row gap2">
              <input className="input" placeholder="Type your reply…" value={reply[h.id] || ""} onChange={(e) => setReply((r) => ({ ...r, [h.id]: e.target.value }))} onKeyDown={(e) => e.key === "Enter" && send(h)} style={{ flex: 1 }} />
              <button className="btn btn-primary btn-sm" onClick={() => send(h)}><Icon name="bolt" size={13} />Reply &amp; resume</button>
            </div>
          </div>
        ))}
        {items.length === 0 && <div className="fg-2 t-caption">No open handoffs. Add a Human Handoff node to a workflow to route conversations here.</div>}
      </div>
    </Shell>
  );
}
