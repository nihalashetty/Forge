"use client";
/* Connectors - the front door for every external system a project talks to.

   A connector is a manifest that installs into ORDINARY Forge rows: an auth provider, a tool
   set, and one tool per action. So everything on this screen ends up in Tools, on the canvas,
   and in agents automatically - there is no separate "connector runtime" for the rest of the
   app to know about.

   Two rules shape this screen:

   * ONE CLICK. A catalog connector never asks anyone for a client id, a secret, or a token. The
     deployment registers each vendor app once in its environment; from here you click Connect,
     sign in on the vendor's own page, approve, and you're done. When the deployment hasn't
     registered a vendor yet the card says so - naming the env key - instead of falling back to
     a form that asks an end user for a credential they have no business holding.
   * EVERY ACCOUNT IS YOURS. Catalog connectors are per-user: connecting signs YOU in, the tick
     means YOU are connected, and disconnecting only affects you. A colleague's Gmail never
     becomes yours by virtue of sharing a project.

   Pasted manifests (Add > Custom connector) keep the credential form and the shared/per-user
   choice - that is where a service account or an internal API with an API key belongs. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../icons";
import { EmptyState, Field, Modal, Toggle } from "../primitives";
import { api, ConnectorCatalogEntry, ConnectorExample, ConnectorInstallT } from "@/lib/api";
import { McpClientsScreen } from "./mcp";

type Filter = "all" | "connected" | "not";

/** A catalog entry merged with its install (if any) - one row in the table. */
type Row = ConnectorCatalogEntry & { install?: ConnectorInstallT };

function iconFor(r: { icon?: string | null; type?: string }) {
  return r.icon || (r.type === "MCP" ? "server" : "k_rest");
}

/** Connected means connected FOR YOU. On a per-user connector the install row goes green as soon
 *  as anyone signs in, which is exactly the wrong thing to show the next person. */
const isConnected = (r: Row) => !!r.install?.connected;

/** Can this be connected right now? Custom installs carry their own credentials; catalog entries
 *  depend on the deployment having registered the vendor. */
const isAvailable = (r: Row) => !!r.install || r.managed === false || r.configured !== false;

/** "Google Calendar" -> "Google". The vendor is what a sign-in button should say. */
const vendor = (name: string) => name.split(" ")[0];

export function ConnectorsScreen({ project, onOpenToolSet }: { project: any; onOpenToolSet?: (setId: string) => void }) {
  const [catalog, setCatalog] = useState<ConnectorCatalogEntry[]>([]);
  const [installs, setInstalls] = useState<ConnectorInstallT[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [role, setRole] = useState<string>("");
  const [q, setQ] = useState("");
  const [searching, setSearching] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [sel, setSel] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [customOpen, setCustomOpen] = useState(false);
  const [view, setView] = useState<"list" | "mcp">("list");
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const searchRef = useRef<HTMLInputElement | null>(null);

  const reload = useCallback(async () => {
    if (!project?.id) return;
    try {
      const [cat, inst] = await Promise.all([api.connectorCatalog(project.id), api.listConnectors(project.id)]);
      setCatalog(cat.connectors);
      setRoles(cat.roles);
      setInstalls(inst);
      setRole((r) => r || cat.roles[0] || "");
    } catch (e: any) {
      setErr(e?.message || "Could not load connectors");
    }
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { if (searching) searchRef.current?.focus(); }, [searching]);

  /* The whole flow, from anywhere on the screen: one call, one browser round trip, connected.
     The server adds the connector to the project if it isn't there yet, so "install" never
     surfaces as a step someone has to understand. */
  const connect = useCallback(async (slug: string) => {
    setBusy(slug); setErr(null); setNote(null);
    try {
      const { authorize_url } = await api.connectConnector(project.id, slug);
      // Consent happens on the vendor's site and the callback lands on the API origin, which
      // stores the token. Refresh when the user comes back rather than guessing a delay - they
      // decide how long a sign-in takes.
      window.open(authorize_url, "_blank", "width=720,height=820");
      setNote("Finish signing in in the new window — this page updates when you come back.");
      const onFocus = async () => {
        window.removeEventListener("focus", onFocus);
        try { await api.syncConnector(project.id, slug); } catch { /* not connected yet, or not an editor */ }
        reload();
      };
      window.addEventListener("focus", onFocus);
    } catch (e: any) {
      setErr(e?.message || "Could not start sign-in");
    } finally {
      setBusy(null);
    }
  }, [project.id, reload]);

  // Custom (non-catalog) installs have no catalog entry, so they are synthesised into rows -
  // otherwise a connector someone installed from their own manifest would be invisible here.
  const rows: Row[] = useMemo(() => {
    const byslug = new Map(installs.map((i) => [i.slug, i]));
    const fromCatalog: Row[] = catalog.map((c) => ({ ...c, install: byslug.get(c.slug) }));
    const known = new Set(catalog.map((c) => c.slug));
    const custom: Row[] = installs.filter((i) => !known.has(i.slug)).map((i) => ({
      slug: i.slug, name: i.name, version: i.version, publisher: "custom" as const,
      summary: i.summary, categories: [], roles: [], icon: i.icon, docs_url: i.docs_url,
      type: i.type, auth: { kind: i.auth_kind, per_user: "optional" as const, scopes: [], setup: [] },
      installed: true, status: i.status, managed: false, configured: true, install: i,
    }));
    return [...fromCatalog, ...custom];
  }, [catalog, installs]);

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter((r) => {
      if (filter === "connected" && !isConnected(r)) return false;
      if (filter === "not" && isConnected(r)) return false;
      if (!needle) return true;
      return (r.name + " " + r.summary + " " + r.categories.join(" ")).toLowerCase().includes(needle);
    });
  }, [rows, q, filter]);

  // Three suggestions for the chosen role, connected ones last (you don't need suggesting
  // toward something you already use).
  const popular = useMemo(() => {
    if (!role) return [];
    return rows
      .filter((r) => r.roles.some((x) => x.toLowerCase() === role.toLowerCase()))
      .sort((a, b) => Number(isConnected(a)) - Number(isConnected(b)))
      .slice(0, 3);
  }, [rows, role]);

  const selected = sel ? rows.find((r) => r.slug === sel) || null : null;
  const unavailable = rows.filter((r) => !isAvailable(r)).length;

  if (view === "mcp") {
    return (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div className="row" style={{ padding: "10px 16px", borderBottom: "1px solid var(--line)", gap: 8 }}>
          <button className="btn btn-ghost btn-sm" onClick={() => setView("list")}><Icon name="arrow-left" size={14} /> Connectors</button>
          <span className="fg-2 t-caption">MCP servers · advanced</span>
        </div>
        <div style={{ flex: 1, display: "flex", minHeight: 0 }}><McpClientsScreen project={project} /></div>
      </div>
    );
  }

  return (
    <div className="grow scroll-y" style={{ minWidth: 0 }}>
      <div style={{ maxWidth: 1080, margin: "0 auto", padding: "22px 24px 48px" }}>

        {/* header */}
        <div className="row spread" style={{ marginBottom: 6 }}>
          <div className="t-display" style={{ fontSize: 22 }}>Connectors</div>
          <div className="row gap2">
            {searching || q ? (
              <input ref={searchRef} className="input" style={{ width: 240 }} value={q} placeholder="Search connectors…"
                onChange={(e) => setQ(e.target.value)}
                onBlur={() => { if (!q) setSearching(false); }}
                onKeyDown={(e) => { if (e.key === "Escape") { setQ(""); setSearching(false); } }} />
            ) : (
              <button className="btn btn-ghost btn-sm" title="Search" onClick={() => setSearching(true)}><Icon name="search" size={16} /></button>
            )}
            <div style={{ position: "relative" }}>
              <button className="btn btn-secondary btn-sm" onClick={() => setAddOpen((o) => !o)}>
                Add <Icon name="chevron" size={13} />
              </button>
              {addOpen && (
                <>
                  <div style={{ position: "fixed", inset: 0, zIndex: 40 }} onClick={() => setAddOpen(false)} />
                  <div className="card" style={{ position: "absolute", right: 0, top: 32, width: 270, zIndex: 41, padding: 6, boxShadow: "var(--sh-2)" }}>
                    <MenuItem icon="upload" title="Custom connector"
                      sub="Your own manifest, or one of the bundled examples."
                      onClick={() => { setAddOpen(false); setCustomOpen(true); }} />
                    <MenuItem icon="server" title="MCP server"
                      sub="Register a raw MCP server by URL."
                      onClick={() => { setAddOpen(false); setView("mcp"); }} />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
        <div className="fg-2 t-body-sm" style={{ marginBottom: 18 }}>
          Connect your own accounts. Each sign-in is personal to you — your colleagues connect theirs.
        </div>

        {err && <div className="pill pill-err" style={{ marginBottom: 12 }}><span className="dot" />{err}</div>}
        {note && <div className="pill pill-info" style={{ marginBottom: 12 }}><span className="dot" />{note}</div>}

        {/* popular for <role> */}
        {popular.length > 0 && (
          <div style={{ marginBottom: 22 }}>
            <div className="row gap2" style={{ marginBottom: 10 }}>
              <span className="fg-2 t-body-sm">Popular for</span>
              <select className="select" style={{ height: 30, width: "auto", minWidth: 170 }} value={role} onChange={(e) => setRole(e.target.value)}>
                {roles.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
              {popular.map((r) => (
                <div key={r.slug} className="card row spread" style={{ padding: "12px 14px", gap: 10 }}>
                  <button className="row gap2" style={{ background: "none", border: "none", padding: 0, cursor: "pointer", minWidth: 0, color: "inherit" }} onClick={() => setSel(r.slug)}>
                    <Icon name={iconFor(r)} size={20} />
                    <span className="truncate" style={{ fontWeight: 600, color: "var(--fg-0)" }}>{r.name}</span>
                  </button>
                  <StatusAction row={r} busy={busy === r.slug} onConnect={() => connect(r.slug)} onOpen={() => setSel(r.slug)} />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* filter */}
        <div className="segmented" style={{ marginBottom: 12 }}>
          {([["all", "All"], ["connected", "Connected"], ["not", "Not connected"]] as [Filter, string][]).map(([k, label]) => (
            <button key={k} className={filter === k ? "active" : ""} onClick={() => setFilter(k)}>{label}</button>
          ))}
        </div>

        {/* table */}
        <div className="card" style={{ overflow: "hidden" }}>
          <div className="row" style={{ padding: "9px 14px", borderBottom: "1px solid var(--line)", color: "var(--fg-2)", fontSize: 12, fontWeight: 600 }}>
            <div style={{ flex: 1 }}>Connector</div>
            <div style={{ width: 110 }}>Type</div>
            <div style={{ width: 150, textAlign: "right" }}>Status</div>
          </div>
          {visible.length === 0 ? (
            <EmptyState icon="plug" title={q ? "No matches" : "No connectors"} sub={q ? "Try a different search." : "The bundled catalog is empty."} />
          ) : visible.map((r) => (
            <div key={r.slug} className="row" style={{ padding: "10px 14px", borderTop: "1px solid var(--line)", gap: 10 }}>
              <button className="row gap2 grow" style={{ background: "none", border: "none", padding: 0, cursor: "pointer", minWidth: 0, textAlign: "left", color: "inherit" }} onClick={() => setSel(r.slug)}>
                <Icon name={iconFor(r)} size={18} />
                <span className="truncate" style={{ fontWeight: 600, color: "var(--fg-0)" }}>{r.name}</span>
                {r.publisher === "custom" && <span className="typechip">Custom</span>}
                {r.install?.auth_mode === "shared" && <span className="typechip" title="One credential for the whole project">Shared</span>}
              </button>
              <div style={{ width: 110, color: "var(--fg-2)", fontSize: 12.5 }}>{r.type === "MCP" ? "MCP server" : "REST API"}</div>
              <div style={{ width: 150, display: "flex", justifyContent: "flex-end" }}>
                <StatusAction row={r} busy={busy === r.slug} onConnect={() => connect(r.slug)} onOpen={() => setSel(r.slug)} />
              </div>
            </div>
          ))}
        </div>

        <div className="fg-2 t-caption" style={{ marginTop: 14, lineHeight: 1.6 }}>
          Connecting creates an auth provider, a tool set, and one tool per action — so its actions show up in{" "}
          <strong>Tools</strong> and can be dropped straight onto a workflow canvas or granted to an agent. The
          tools are the project&apos;s; the account behind them is yours.
          {unavailable > 0 && (
            <> {unavailable} connector{unavailable === 1 ? " isn't" : "s aren't"} set up on this deployment yet —
            open one to see which app to register.</>
          )}
        </div>
      </div>

      {selected && (
        <ConnectorDetail
          project={project} slug={selected.slug} row={selected}
          busy={busy === selected.slug}
          onConnect={() => connect(selected.slug)}
          onClose={() => setSel(null)} onChanged={reload}
          onOpenToolSet={onOpenToolSet}
        />
      )}
      {customOpen && <CustomConnectorModal project={project} onClose={() => setCustomOpen(false)} onInstalled={() => { setCustomOpen(false); reload(); }} />}
    </div>
  );
}

function MenuItem({ icon, title, sub, onClick }: { icon: string; title: string; sub: string; onClick: () => void }) {
  return (
    <button className="row gap2" style={{ width: "100%", textAlign: "left", padding: "9px 10px", borderRadius: 8, border: "none", background: "transparent", cursor: "pointer", color: "inherit" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-hover)")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
      onClick={onClick}>
      <Icon name={icon} size={16} />
      <span className="col" style={{ gap: 1, minWidth: 0 }}>
        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--fg-0)" }}>{title}</span>
        <span className="fg-2" style={{ fontSize: 11.5 }}>{sub}</span>
      </span>
    </button>
  );
}

/** The right-hand cell. For anything the deployment can actually sign you into, this IS the
 *  flow - one click straight to the vendor, no intermediate screen. */
function StatusAction({ row, busy, onConnect, onOpen }: {
  row: Row; busy: boolean; onConnect: () => void; onOpen: () => void;
}) {
  if (isConnected(row)) {
    return <span className="row gap2" style={{ color: "var(--ok)" }} title="You're connected"><Icon name="check" size={16} /></span>;
  }
  if (!isAvailable(row)) {
    return <button className="btn btn-ghost btn-sm fg-2" onClick={onOpen} title="Not set up on this deployment">Unavailable</button>;
  }
  // A pasted connector with a key/token credential has nothing to sign in to - its detail panel
  // is where you manage it.
  if (row.install && row.auth?.kind !== "oauth2_authorization_code") {
    return <button className="btn btn-ghost btn-sm" onClick={onOpen}>Manage</button>;
  }
  return (
    <button className="btn btn-secondary btn-sm" disabled={busy} onClick={onConnect}>
      {busy ? "Opening…" : "Connect"}
    </button>
  );
}

/* ---------------------------------------------------------------------------------------- */

function ConnectorDetail({ project, slug, row, busy, onConnect, onClose, onChanged, onOpenToolSet }: {
  project: any; slug: string; row: Row; busy: boolean; onConnect: () => void;
  onClose: () => void; onChanged: () => void; onOpenToolSet?: (setId: string) => void;
}) {
  const [detail, setDetail] = useState<ConnectorCatalogEntry | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [work, setWork] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const install = row.install;

  useEffect(() => {
    if (row.publisher === "custom") { setDetail(row); return; }
    api.connectorDetail(project.id, slug).then(setDetail).catch(() => setDetail(row));
  }, [project.id, slug, row]);

  const spec = detail || row;
  const managed = spec.managed !== false;
  const available = isAvailable({ ...spec, install });
  const connected = isConnected(row);
  const oauth = spec.auth?.kind === "oauth2_authorization_code";

  const run = async (label: string, fn: () => Promise<void>) => {
    setWork(label); setErr(null); setNote(null);
    try { await fn(); } catch (e: any) { setErr(e?.message || "Something went wrong"); } finally { setWork(null); }
  };

  return (
    <Modal open onClose={onClose} width={620} title={spec.name}>
      <div className="col" style={{ gap: 14 }}>
        <div className="row gap2">
          <Icon name={iconFor(spec)} size={22} />
          <div className="col" style={{ gap: 2, minWidth: 0 }}>
            <div className="fg-2 t-body-sm">{spec.summary}</div>
            <div className="row gap2" style={{ marginTop: 2 }}>
              <span className="typechip">{spec.type === "MCP" ? "MCP server" : "REST API"}</span>
              {connected
                ? <span className="pill pill-ok"><span className="dot" />Connected as you</span>
                : install && <span className="pill pill-warn"><span className="dot" />Not connected</span>}
            </div>
          </div>
        </div>

        {install?.status_detail && <div className="pill pill-err"><span className="dot" />{install.status_detail}</div>}
        {err && <div className="pill pill-err"><span className="dot" />{err}</div>}
        {note && <div className="pill pill-info"><span className="dot" />{note}</div>}

        {/* what it can do */}
        {spec.actions && spec.actions.length > 0 && (
          <details>
            <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>{spec.actions.length} actions</summary>
            <div className="col" style={{ gap: 6, marginTop: 8 }}>
              {spec.actions.map((a) => (
                <div key={a.name} className="row gap2" style={{ alignItems: "flex-start" }}>
                  <span className="typechip">{a.method}</span>
                  <span className="col" style={{ gap: 1, minWidth: 0 }}>
                    <span className="mono-sm" style={{ color: "var(--fg-0)" }}>{a.name}</span>
                    <span className="fg-2" style={{ fontSize: 11.5 }}>{a.description}</span>
                  </span>
                </div>
              ))}
            </div>
          </details>
        )}
        {spec.type === "MCP" && !install && (
          <div className="fg-2 t-caption">
            Actions are discovered from the vendor&apos;s MCP server the first time you connect, so the
            list stays current as they ship new ones.
          </div>
        )}
        {spec.auth?.scopes?.length > 0 && available && (
          <Field label="You'll be asked to approve">
            <div className="row gap2 wrap">{spec.auth.scopes.map((s) => <span key={s} className="chip chip-mono">{s.replace(/^https:\/\/www\.googleapis\.com\/auth\//, "")}</span>)}</div>
          </Field>
        )}

        {!available ? (
          <NotConfigured spec={spec} />
        ) : !install || (oauth && !connected) ? (
          <>
            {managed && (
              <div className="fg-2 t-caption" style={{ lineHeight: 1.55 }}>
                You&apos;ll sign in to {vendor(spec.name)} in a new window and approve access. The connection is
                yours alone — nobody else in this project can act as you, and you can disconnect at any time.
              </div>
            )}
            <div className="row gap2">
              <button className="btn btn-primary" disabled={busy} onClick={onConnect}>
                {busy ? "Opening…" : `Continue with ${vendor(spec.name)}`}
              </button>
              {spec.docs_url && <a className="btn btn-ghost btn-sm" href={spec.docs_url} target="_blank" rel="noreferrer">API docs <Icon name="external" size={13} /></a>}
            </div>
          </>
        ) : null}

        {install && (
          <>
            <div className="row gap2 wrap">
              <span className="chip">{install.tool_count} action{install.tool_count === 1 ? "" : "s"}</span>
              <span className="chip">{install.auth_mode === "per_user" ? "Personal connection" : "Shared credential"}</span>
              {install.tool_set_id && onOpenToolSet && (
                <button className="btn btn-ghost btn-sm" onClick={() => { onOpenToolSet(install.tool_set_id!); onClose(); }}>Open in Tools</button>
              )}
            </div>
            <div className="row gap2 wrap">
              {connected && oauth && (
                <button className="btn btn-secondary btn-sm" disabled={busy} onClick={onConnect}>Reconnect</button>
              )}
              {spec.type === "MCP" && (
                <button className="btn btn-secondary btn-sm" disabled={work === "sync"}
                  onClick={() => run("sync", async () => {
                    const res = await api.syncConnector(project.id, slug);
                    if (!res.ok) throw new Error(res.error || "Could not list actions");
                    setNote(`${res.tool_count} action(s) available.`);
                    onChanged();
                  })}>
                  <Icon name="refresh" size={13} /> Refresh actions
                </button>
              )}
              {connected && install.auth_kind !== "none" && (
                <button className="btn btn-ghost btn-sm" disabled={work === "disc"}
                  onClick={() => run("disc", async () => {
                    await api.disconnectConnector(project.id, slug);
                    onChanged();
                    setNote("Signed out. The actions stay wired up but can't act as you until you reconnect.");
                  })}>
                  Disconnect
                </button>
              )}
              <button className="btn btn-danger btn-sm" disabled={work === "rm"}
                onClick={() => run("rm", async () => {
                  if (!window.confirm(`Remove ${spec.name} from this project? Its ${install.tool_count} action(s) and tool set are deleted for everyone, and every person who connected is disconnected. Workflows and agents using them keep running, minus those capabilities.`)) return;
                  await api.uninstallConnector(project.id, slug);
                  onChanged(); onClose();
                })}>
                <Icon name="trash" size={13} /> Remove from project
              </button>
            </div>
            {/* Rotating a credential only applies to a pasted connector - a catalog one's app
                lives in the deployment's environment, where its owner rotates it. */}
            {install.source !== "catalog" && (spec.auth?.setup || []).some((f) => f.secret) && (
              <details>
                <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>Update credentials</summary>
                <div style={{ marginTop: 8 }}>
                  {(spec.auth?.setup || []).filter((f) => f.secret).map((f) => (
                    <Field key={f.key} label={f.label}>
                      <input className="input" type="password" autoComplete="off" placeholder="unchanged"
                        value={values[f.key] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
                    </Field>
                  ))}
                  <button className="btn btn-secondary btn-sm" disabled={work === "creds"}
                    onClick={() => run("creds", async () => {
                      await api.setConnectorCredentials(project.id, slug, values);
                      setValues({}); onChanged(); setNote("Credentials updated.");
                    })}>Save credentials</button>
                </div>
              </details>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

/** The deployment hasn't registered this vendor. That is an operator's job, in the environment -
 *  so say precisely what to register and where it goes, rather than showing an end user a form
 *  for a client secret that shouldn't be theirs to hold. */
function NotConfigured({ spec }: { spec: ConnectorCatalogEntry }) {
  const keys = spec.missing_keys || [];
  const envLine = `${spec.config_env_key || "FORGE_CONNECTOR_OAUTH_APPS"}={"${spec.credential_group}":{${keys.map((k) => `"${k}":"…"`).join(",")}}}`;
  return (
    <div className="col" style={{ gap: 10 }}>
      <div className="pill pill-warn"><span className="dot" />Not set up on this Forge deployment yet</div>
      <div className="fg-2 t-caption" style={{ lineHeight: 1.6 }}>
        Whoever runs Forge registers <strong>one</strong> {vendor(spec.name)} OAuth app — with the redirect URI
        below — and adds it to the environment. After a restart, everyone here connects their own
        {" "}{vendor(spec.name)} account with a single click; nobody types a secret into Forge.
      </div>
      <Field label="Add to your .env, then restart the API">
        <input className="input mono" readOnly value={envLine} onFocus={(e) => e.currentTarget.select()} />
      </Field>
      <RedirectHint url={spec.redirect_uri} />
      <div className="row gap2">
        {spec.setup_url && <a className="btn btn-secondary btn-sm" href={spec.setup_url} target="_blank" rel="noreferrer">Register an app <Icon name="external" size={13} /></a>}
        {spec.docs_url && <a className="btn btn-ghost btn-sm" href={spec.docs_url} target="_blank" rel="noreferrer">API docs <Icon name="external" size={13} /></a>}
      </div>
      {spec.auth?.setup_help && <div className="fg-2 t-caption" style={{ lineHeight: 1.55 }}>{spec.auth.setup_help}</div>}
    </div>
  );
}

/** OAuth apps need the exact callback whitelisted; showing it removes the usual first failure.
 *  The value comes from the SERVER, because the redirect_uri the flow actually sends is built
 *  from the API's public base URL - not the console's origin the browser happens to be on. A
 *  guessed URL looks plausible and then fails with redirect_uri_mismatch. */
function RedirectHint({ url }: { url?: string }) {
  if (!url) return null;
  return (
    <Field label="Redirect URI" help="Add this exact URL to the OAuth app you register with the vendor.">
      <div className="row gap2">
        <input className="input mono" readOnly value={url} />
        <button className="btn btn-ghost btn-sm" onClick={() => navigator.clipboard?.writeText(url)}>Copy</button>
      </div>
    </Field>
  );
}

/* ---------------------------------------------------------------------------------------- */

function CustomConnectorModal({ project, onClose, onInstalled }: { project: any; onClose: () => void; onInstalled: () => void }) {
  const [text, setText] = useState("");
  const [checked, setChecked] = useState<{ ok: boolean; error?: string; connector?: ConnectorCatalogEntry; hosts?: string[] } | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [perUser, setPerUser] = useState(false);
  const [examples, setExamples] = useState<ConnectorExample[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Bundled starting points for the services that can't be one-click (an API key or a bot token
  // has to come from a person). Best-effort: a viewer without editor rights just doesn't see them.
  useEffect(() => { api.connectorExamples(project.id).then(setExamples).catch(() => setExamples([])); }, [project.id]);

  const parsed = useMemo(() => { try { return JSON.parse(text); } catch { return null; } }, [text]);

  const validate = async () => {
    setErr(null);
    if (!parsed) { setChecked({ ok: false, error: "That isn't valid JSON." }); return; }
    try { setChecked(await api.validateConnectorManifest(project.id, parsed)); }
    catch (e: any) { setErr(e?.message || "Could not validate"); }
  };

  const install = async () => {
    setBusy(true); setErr(null);
    try {
      await api.installCustomConnector(project.id, { manifest: parsed, values, auth_mode: perUser ? "per_user" : "shared" });
      onInstalled();
    } catch (e: any) { setErr(e?.message || "Install failed"); } finally { setBusy(false); }
  };

  const setup = checked?.connector?.auth?.setup || [];

  return (
    <Modal open onClose={onClose} width={640} title="Install a custom connector">
      <div className="col" style={{ gap: 12 }}>
        <div className="fg-2 t-caption" style={{ lineHeight: 1.55 }}>
          Paste a <span className="mono-sm">forge.connector/1</span> manifest. It installs exactly like a catalog
          entry — same validation, same rows — so a private connector pack can live in your own repo instead of a
          fork of Forge. This is also where a credential you type yourself belongs: an API key, a bot token, or one
          shared service account for the whole project.
        </div>
        {examples.length > 0 && (
          <Field label="Start from an example" help="Bundled manifests for services that need a key rather than a sign-in. Load one, then fill in its credential below.">
            <select className="select" value="" onChange={(e) => {
              const ex = examples.find((x) => x.slug === e.target.value);
              if (ex) { setText(JSON.stringify(ex.manifest, null, 2)); setChecked(null); }
            }}>
              <option value="">Choose an example…</option>
              {examples.map((ex) => (
                <option key={ex.slug} value={ex.slug}>{ex.name} — needs {ex.needs.join(", ").toLowerCase()}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Manifest (JSON)">
          <textarea className="textarea mono" rows={10} style={{ fontSize: 12 }} value={text}
            placeholder={'{\n  "format": "forge.connector/1",\n  "slug": "my-api",\n  ...\n}'}
            onChange={(e) => { setText(e.target.value); setChecked(null); }} />
        </Field>
        {err && <div className="pill pill-err"><span className="dot" />{err}</div>}
        {checked && !checked.ok && <div className="pill pill-err"><span className="dot" />{checked.error}</div>}
        {checked?.ok && checked.connector && (
          <>
            <div className="pill pill-ok"><span className="dot" />
              {checked.connector.name} · {checked.connector.type === "MCP" ? "MCP server" : `${checked.connector.action_count} action(s)`}
            </div>
            {checked.hosts && checked.hosts.length > 0 && (
              <Field label="Will be allowed to call" help="These hosts are added to this project's egress allow-list on install.">
                <div className="row gap2 wrap">{checked.hosts.map((h) => <span key={h} className="chip chip-mono">{h}</span>)}</div>
              </Field>
            )}
            {checked.connector.auth.kind?.startsWith("oauth2") && <RedirectHint url={checked.connector.redirect_uri} />}
            {checked.connector.auth.per_user !== "never" && (
              <div className="row gap2">
                <Toggle on={perUser} onChange={checked.connector.auth.per_user === "required" ? undefined : setPerUser} />
                <span className="t-body-sm">{perUser ? "Each user connects their own account" : "One shared account for this project"}</span>
              </div>
            )}
            {setup.map((f) => (
              <Field key={f.key} label={f.label} help={f.help || undefined} required={f.required}>
                <input className="input" type={f.secret ? "password" : "text"} autoComplete="off" placeholder={f.placeholder || ""}
                  value={values[f.key] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))} />
              </Field>
            ))}
          </>
        )}
        <div className="row gap2">
          {!checked?.ok
            ? <button className="btn btn-secondary" disabled={!text.trim()} onClick={validate}>Validate</button>
            : <button className="btn btn-primary" disabled={busy} onClick={install}>{busy ? "Installing…" : "Install"}</button>}
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </Modal>
  );
}
