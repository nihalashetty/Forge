"use client";
/* Login / register screen + AuthGate.

AuthGate calls /v1/auth/me on mount: in the default (auth-not-required) mode the
backend returns the seeded owner, so the gate passes straight through and the console
works as before. When FORGE_AUTH_REQUIRED=true, an unauthenticated /me returns 401 and
the gate shows this screen. */
import { ReactNode, useCallback, useEffect, useState } from "react";
import { api, clearTokens, setTokens, UNAUTHORIZED_EVENT } from "@/lib/api";
import type { MeResult, Project } from "@/lib/api";

function AcceptInviteScreen({ token, onAuthed, onCancel }: { token: string; onAuthed: () => void; onCancel: () => void }) {
  const [info, setInfo] = useState<{ email: string; role: string } | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.inviteInfo(token)
      .then(setInfo)
      .catch(() => setLoadErr("This invite link is invalid or has expired. Ask an admin to send a new one."));
  }, [token]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) { setError("Passwords don't match."); return; }
    setBusy(true); setError(null);
    try {
      const res = await api.acceptInvite(token, password);
      setTokens(res.access_token, res.refresh_token);
      onAuthed();
    } catch {
      setError("Could not set your password. The link may have expired (minimum 8 characters).");
    } finally { setBusy(false); }
  }

  return (
    <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", background: "var(--bg-0)" }}>
      <form onSubmit={submit} className="card" style={{ width: 380, padding: 28, boxShadow: "var(--sh-pop)" }}>
        <div className="t-display" style={{ marginBottom: 4 }}>Forge</div>
        {loadErr ? (
          <>
            <div className="t-caption" style={{ color: "var(--danger, #d33)", margin: "12px 0 16px" }}>{loadErr}</div>
            <button type="button" className="btn btn-secondary" style={{ width: "100%", justifyContent: "center" }} onClick={onCancel}>Go to sign in</button>
          </>
        ) : (
          <>
            <div className="fg-1" style={{ marginBottom: 20 }}>
              {info ? <>Set a password for <b>{info.email}</b> to join as a <b>{info.role}</b>.</> : "Loading your invite…"}
            </div>
            <label className="col gap1" style={{ marginBottom: 12 }}>
              <span className="t-micro">New password</span>
              <input className="input" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="At least 8 characters" disabled={!info} />
            </label>
            <label className="col gap1" style={{ marginBottom: 16 }}>
              <span className="t-micro">Confirm password</span>
              <input className="input" type="password" required minLength={8} value={confirm} onChange={(e) => setConfirm(e.target.value)} disabled={!info} />
            </label>
            {error && <div className="t-caption" style={{ color: "var(--danger, #d33)", marginBottom: 12 }}>{error}</div>}
            <button className="btn btn-primary" type="submit" disabled={busy || !info} style={{ width: "100%", justifyContent: "center" }}>
              {busy ? "…" : "Set password & continue"}
            </button>
          </>
        )}
      </form>
    </div>
  );
}

function LoginScreen({ onAuthed }: { onAuthed: () => void }) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [workspace, setWorkspace] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = mode === "login"
        ? await api.login(email.trim(), password)
        : await api.register(email.trim(), password, workspace.trim() || undefined);
      setTokens(res.access_token, res.refresh_token);
      onAuthed();
    } catch (err: any) {
      setError(mode === "login" ? "Invalid email or password." : "Could not create the account (email may already exist, or password is too short).");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", background: "var(--bg-0)" }}>
      <form onSubmit={submit} className="card" style={{ width: 380, padding: 28, boxShadow: "var(--sh-pop)" }}>
        <div className="t-display" style={{ marginBottom: 4 }}>Forge</div>
        <div className="fg-1" style={{ marginBottom: 20 }}>
          {mode === "login" ? "Sign in to your workspace" : "Create your workspace"}
        </div>
        {mode === "register" && (
          <label className="col gap1" style={{ marginBottom: 12 }}>
            <span className="t-micro">Workspace name</span>
            <input className="input" value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="Acme Inc" />
          </label>
        )}
        <label className="col gap1" style={{ marginBottom: 12 }}>
          <span className="t-micro">Email</span>
          <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" />
        </label>
        <label className="col gap1" style={{ marginBottom: 16 }}>
          <span className="t-micro">Password</span>
          <input className="input" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} placeholder={mode === "register" ? "At least 8 characters" : ""} />
        </label>
        {error && <div className="t-caption" style={{ color: "var(--danger, #d33)", marginBottom: 12 }}>{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy} style={{ width: "100%", justifyContent: "center", marginBottom: 12 }}>
          {busy ? "…" : mode === "login" ? "Sign in" : "Create workspace"}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" style={{ width: "100%", justifyContent: "center" }}
          onClick={() => { setError(null); setMode(mode === "login" ? "register" : "login"); }}>
          {mode === "login" ? "Need an account? Create a workspace" : "Already have an account? Sign in"}
        </button>
      </form>
    </div>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"loading" | "authed" | "login">("loading");
  const [me, setMe] = useState<MeResult | null>(null);
  const [invite, setInvite] = useState<string | null>(null);
  // An invite link (?invite=<token>) takes over the gate so a new teammate can set their
  // password even if there's a stale session in this browser.
  const clearInviteParam = useCallback(() => {
    if (typeof window !== "undefined") window.history.replaceState({}, "", window.location.pathname);
    setInvite(null);
  }, []);
  useEffect(() => {
    if (typeof window === "undefined") return;
    const t = new URLSearchParams(window.location.search).get("invite");
    if (t) setInvite(t);
  }, []);
  const check = useCallback(() => {
    api.me()
      .then((m) => { setMe(m); setState("authed"); })
      .catch((e: any) => {
        // Fail OPEN: only an explicit 401 means auth is enforced and we must log in.
        // A 404/network error (e.g. an older backend without /auth/me, or auth disabled)
        // must NOT lock the user out of the console.
        const is401 = typeof e?.message === "string" && e.message.startsWith("401");
        setState(is401 ? "login" : "authed");
      });
  }, []);
  useEffect(() => { check(); }, [check]);
  useEffect(() => {
    const h = () => setState("login");
    window.addEventListener(UNAUTHORIZED_EVENT, h);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, h);
  }, []);

  if (invite)
    return <AcceptInviteScreen token={invite} onAuthed={() => { clearInviteParam(); check(); }} onCancel={() => { clearInviteParam(); setState("login"); }} />;
  if (state === "loading")
    return <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center" }} className="fg-2">Loading…</div>;
  if (state === "login") return <LoginScreen onAuthed={() => check()} />;
  // MCP-only users (connector role) get just their token page, not the full console.
  if (me?.role === "connector") return <ConnectorHome me={me} />;
  return <>{children}</>;
}


/* Minimal console for an MCP-only (connector) user: pick a project, generate a personal access
   token, and copy the endpoint - no projects/tools/settings management. */
function ConnectorHome({ me }: { me: MeResult }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tokens, setTokens] = useState<Record<string, string>>({});
  useEffect(() => { api.listProjects().then(setProjects).catch(() => {}); }, []);
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const box: React.CSSProperties = { background: "var(--bg-2)", padding: 8, borderRadius: 6, overflowX: "auto", margin: "4px 0" };

  async function gen(pid: string) {
    const t = await api.createMcpToken(pid, {});
    setTokens((prev) => ({ ...prev, [pid]: t.token || "" }));
  }

  return (
    <div style={{ maxWidth: 640, margin: "48px auto", padding: "0 20px", fontFamily: "var(--font-ui)" }}>
      <div className="row spread" style={{ marginBottom: 14, alignItems: "center" }}>
        <div className="t-display" style={{ fontSize: 20 }}>Your MCP access</div>
        <button className="btn btn-secondary btn-sm" onClick={() => { clearTokens(); window.location.reload(); }}>Sign out</button>
      </div>
      <div className="fg-1" style={{ marginBottom: 20 }}>
        Signed in as <b>{me.email}</b>. Generate a personal token for a project and paste it into your MCP
        client (Claude, Cursor, …) as <span className="mono-sm">Authorization: Bearer &lt;token&gt;</span>.
      </div>
      {projects.length === 0 && <div className="fg-2 t-caption">No projects available yet.</div>}
      <div className="col gap3">
        {projects.map((p) => (
          <div key={p.id} className="card" style={{ padding: 16 }}>
            <div className="t-h3" style={{ marginBottom: 6 }}>{p.name}</div>
            <div className="t-caption fg-2">MCP endpoint</div>
            <pre className="mono-sm" style={box}>{`${origin}/api/forge/v1/mcp/${p.id}`}</pre>
            {tokens[p.id] ? (
              <>
                <div className="t-caption fg-2" style={{ marginTop: 6 }}>Access token — copy now, shown once:</div>
                <pre className="mono-sm" style={box}>{tokens[p.id]}</pre>
              </>
            ) : (
              <button className="btn btn-primary btn-sm" style={{ marginTop: 8 }} onClick={() => gen(p.id)}>Generate access token</button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
