"use client";
/* Knowledge: vertical-tab layout — Files (sources organized in folders), Q&A pairs
   (free-form kinds/categories + tags), and the search debugger. */
import { useCallback, useEffect, useMemo, useState } from "react";
import { Icon } from "../icons";
import { Field, Modal, Segmented, StatusPill } from "../primitives";
import { api, KbSource, QaPair, SearchHit } from "@/lib/api";

const VTABS = [
  { value: "files", label: "Files", icon: "knowledge" },
  { value: "qa", label: "Q&A pairs", icon: "n_qa" },
  { value: "search", label: "Search debugger", icon: "search" },
] as const;

export function KnowledgeScreen({ project }: { project: any }) {
  const [tab, setTab] = useState<string>("files");
  return (
    <div className="col" style={{ flex: 1, minHeight: 0 }}>
      <div style={{ padding: "20px 28px 14px" }}>
        <div className="t-display">Knowledge</div>
        <div className="fg-1" style={{ marginTop: 3 }}>Ground agents in your docs (Chroma vectors, organized in folders) and deflect FAQs with categorized Q&A pairs.</div>
      </div>
      <div className="row" style={{ flex: 1, minHeight: 0, alignItems: "stretch" }}>
        {/* vertical tab rail */}
        <nav className="col" style={{ width: 184, flex: "none", padding: "4px 0 16px 20px", gap: 2 }}>
          {VTABS.map((t) => (
            <button
              key={t.value}
              onClick={() => setTab(t.value)}
              className="row gap2"
              style={{
                alignItems: "center", textAlign: "left", padding: "8px 12px", borderRadius: 8,
                border: "none", cursor: "pointer", fontSize: 13, fontWeight: tab === t.value ? 650 : 450,
                background: tab === t.value ? "var(--bg-3)" : "transparent",
                color: tab === t.value ? "var(--fg-0)" : "var(--fg-1)",
              }}
            >
              <Icon name={t.icon as any} size={15} />
              {t.label}
            </button>
          ))}
        </nav>
        <div className="scroll-y" style={{ flex: 1, minWidth: 0, padding: "4px 28px 24px 16px" }}>
          <div style={{ maxWidth: 960 }}>
            {tab === "files" && <Files project={project} />}
            {tab === "qa" && <QA project={project} />}
            {tab === "search" && <SearchDebugger project={project} />}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Files (sources in folders) ---------------- */

const UNFILED = "";

function Files({ project }: { project: any }) {
  const [rows, setRows] = useState<KbSource[]>([]);
  const [folder, setFolder] = useState<string | null>(null); // null = All files
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);
  const [newFolder, setNewFolder] = useState<string | null>(null); // non-null = naming a new folder
  // The modal never asks for a folder — sources land in the folder open at the time
  // ("" = Unfiled, e.g. from the All files view).
  const [targetFolder, setTargetFolder] = useState<string>("");
  const [form, setForm] = useState<{ kind: string; name: string; text: string; uri: string; file: globalThis.File | null }>({ kind: "text", name: "", text: "", uri: "", file: null });

  const [health, setHealth] = useState<{ needs_reembed: boolean; current_model: string; mismatched: { id: string; name: string }[] } | null>(null);
  const reload = useCallback(() => {
    if (!project?.id) return;
    api.listSources(project.id).then(setRows).catch(() => {});
    api.embeddingHealth(project.id).then(setHealth).catch(() => setHealth(null));
  }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  async function reingest(id: string) { await api.reingestSource(project.id, id).catch(() => {}); reload(); }

  const folders = useMemo(() => {
    const set = new Set<string>();
    rows.forEach((s) => { if (s.folder) set.add(s.folder); });
    return [...set].sort();
  }, [rows]);

  const visible = folder === null ? rows : rows.filter((s) => (s.folder || UNFILED) === folder);
  const hasUnfiled = rows.some((s) => !s.folder);
  // Folder column is redundant when already viewing one named folder — show it only in
  // the All-files and Unfiled views (where moving files between folders is useful).
  const showFolderCol = folder === null || folder === UNFILED;
  const inNamedFolder = folder !== null && folder !== UNFILED;

  function openAdd(forFolder?: string) {
    setTargetFolder(forFolder ?? (inNamedFolder ? folder! : ""));
    setAddErr(null);
    setOpen(true);
  }

  async function add() {
    setBusy(true);
    setAddErr(null);
    try {
      if (form.kind === "file") {
        if (!form.file) { setAddErr("Choose a file to upload."); return; }
        await api.uploadSource(project.id, form.file, targetFolder || undefined);
      } else {
        await api.addSource(project.id, {
          kind: form.kind, name: form.name || "Untitled", folder: targetFolder || undefined,
          text: form.kind === "text" ? form.text : undefined, uri: (form.kind === "url" || form.kind === "crawl") ? form.uri : undefined,
        });
      }
      setOpen(false); setForm({ kind: "text", name: "", text: "", uri: "", file: null }); reload();
    } catch (e: any) {
      setAddErr(String(e?.message || e));
    } finally { setBusy(false); }
  }

  function FolderRow({ value, label, icon, count }: { value: string | null; label: string; icon: string; count: number }) {
    const active = folder === value;
    return (
      <button onClick={() => setFolder(value)} className="row spread" style={{
        width: "100%", alignItems: "center", padding: "7px 10px", borderRadius: 7, border: "none", cursor: "pointer",
        background: active ? "var(--bg-3)" : "transparent", color: active ? "var(--fg-0)" : "var(--fg-1)", fontSize: 13,
      }}>
        <span className="row gap2" style={{ alignItems: "center", minWidth: 0 }}><Icon name={icon as any} size={14} /><span className="truncate">{label}</span></span>
        <span className="t-caption fg-2 mono">{count}</span>
      </button>
    );
  }

  return (
    <div className="col" style={{ gap: 12 }}>
      {health?.needs_reembed && (
        <div className="card row spread" style={{ padding: "10px 14px", background: "var(--signal-glow)", borderColor: "transparent" }}>
          <div className="row gap2" style={{ minWidth: 0 }}><Icon name="bolt" size={15} style={{ color: "var(--signal)" }} />
            <span className="t-body-sm">{health.mismatched.length} source(s) were embedded with a different model than the current one ({health.current_model}) — they won&apos;t appear in search until re-embedded.</span>
          </div>
          <button className="btn btn-secondary btn-sm" style={{ flex: "none" }} onClick={async () => { for (const m of health.mismatched) await reingest(m.id); }}><Icon name="refresh" size={13} />Re-embed all</button>
        </div>
      )}
    <div className="row" style={{ gap: 18, alignItems: "flex-start" }}>
      {/* folder list */}
      <div className="card col" style={{ width: 218, flex: "none", padding: 10, gap: 2 }}>
        <FolderRow value={null} label="All files" icon="list" count={rows.length} />
        {hasUnfiled && <FolderRow value={UNFILED} label="Unfiled" icon="file" count={rows.filter((s) => !s.folder).length} />}
        {folders.map((f) => (
          <FolderRow key={f} value={f} label={f} icon="layers" count={rows.filter((s) => s.folder === f).length} />
        ))}
        {newFolder === null ? (
          <button className="btn btn-ghost btn-sm" style={{ justifyContent: "flex-start", marginTop: 4 }} onClick={() => setNewFolder("")}>
            <Icon name="plus" size={13} />New folder
          </button>
        ) : (
          <input
            autoFocus className="input" style={{ marginTop: 4, fontSize: 13 }} placeholder="Folder name…" value={newFolder}
            onChange={(e) => setNewFolder(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newFolder.trim()) {
                // Folders exist through their files: open the add modal locked to the new folder.
                const nf = newFolder.trim();
                setNewFolder(null);
                openAdd(nf);
              }
              if (e.key === "Escape") setNewFolder(null);
            }}
            onBlur={() => setNewFolder(null)}
          />
        )}
        <div className="t-caption fg-2" style={{ padding: "6px 10px 2px" }}>
          Retrieval nodes and knowledge_search tools can filter by folder.
        </div>
      </div>

      {/* sources table */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="row spread" style={{ marginBottom: 12 }}>
          <div className="t-h2">{folder === null ? "All files" : folder === UNFILED ? "Unfiled" : folder}</div>
          <button className="btn btn-primary btn-sm" onClick={() => openAdd()}>
            <Icon name="plus" size={14} />Add source
          </button>
        </div>
        <div className="card" style={{ overflow: "hidden" }}>
          <table className="tbl">
            <thead><tr><th>Name</th><th>Kind</th>{showFolderCol && <th>Folder</th>}<th>Status</th><th>Chunks</th><th /></tr></thead>
            <tbody>
              {visible.map((s) => (
                <tr key={s.id}>
                  <td style={{ fontWeight: 600 }}>{s.name}</td>
                  <td><span className="typechip">{s.kind}</span></td>
                  {showFolderCol && (
                    <td>
                      <select
                        className="select" style={{ fontSize: 12, padding: "3px 6px", maxWidth: 140 }}
                        value={s.folder || UNFILED}
                        onChange={async (e) => { await api.moveSource(project.id, s.id, e.target.value); reload(); }}
                      >
                        <option value={UNFILED}>Unfiled</option>
                        {folders.map((f) => <option key={f} value={f}>{f}</option>)}
                        {s.folder && !folders.includes(s.folder) && <option value={s.folder}>{s.folder}</option>}
                      </select>
                    </td>
                  )}
                  <td><StatusPill status={s.status} /></td>
                  <td className="mono-sm">{s.chunks}</td>
                  <td style={{ textAlign: "right" }}>
                    <div className="row gap1" style={{ justifyContent: "flex-end" }}>
                      {(s.kind === "url" || s.kind === "crawl" || s.kind === "text") && <button className="iconbtn" title="Re-fetch & re-embed" onClick={() => reingest(s.id)}><Icon name="refresh" size={14} /></button>}
                      <button className="iconbtn" title="Delete" onClick={async () => { await api.deleteSource(project.id, s.id); reload(); }}><Icon name="trash" size={15} /></button>
                    </div>
                  </td>
                </tr>
              ))}
              {visible.length === 0 && <tr><td colSpan={showFolderCol ? 6 : 5}><div className="fg-2" style={{ padding: 22, textAlign: "center" }}>{rows.length === 0 ? "No sources yet. Add text or a URL to feed your agents." : "No files in this folder yet."}</div></td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <Modal open={open} onClose={() => setOpen(false)} title={`Add source to “${targetFolder || "Unfiled"}”`} width={560}
        footer={<><button className="btn btn-ghost" onClick={() => setOpen(false)}>Cancel</button><button className="btn btn-primary" onClick={add} disabled={busy}>{busy ? "Ingesting…" : "Add & ingest"}</button></>}>
        <Field label="Kind"><Segmented options={[{ value: "text", label: "Paste text" }, { value: "url", label: "URL" }, { value: "crawl", label: "Crawl site" }, { value: "file", label: "Upload file" }]} value={form.kind} onChange={(v) => setForm((f) => ({ ...f, kind: v }))} /></Field>
        {form.kind !== "file" && (
          <Field label="Name"><input className="input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Help Center FAQ" /></Field>
        )}
        {form.kind === "text" && (
          <Field label="Text" help="Split into ~1000-char chunks and embedded into Chroma."><textarea className="textarea" rows={7} value={form.text} onChange={(e) => setForm((f) => ({ ...f, text: e.target.value }))} placeholder="Paste documentation, policies, FAQs…" /></Field>
        )}
        {form.kind === "crawl" && (
          <Field label="Start URL" help="Crawls same-domain pages from here (up to ~10), strips HTML, chunks + embeds. Re-crawl anytime with the ↻ button."><input className="input mono" value={form.uri} onChange={(e) => setForm((f) => ({ ...f, uri: e.target.value }))} placeholder="https://docs.example.com" /></Field>
        )}
        {form.kind === "url" && (
          <Field label="URL" help="Fetched, stripped of HTML, chunked, embedded."><input className="input mono" value={form.uri} onChange={(e) => setForm((f) => ({ ...f, uri: e.target.value }))} placeholder="https://docs.example.com/help" /></Field>
        )}
        {form.kind === "file" && (
          <Field label="File" help="Text formats (.txt, .md, .csv, .json…) and PDF. Named after the file; extracted, chunked, embedded.">
            <input className="input" type="file" accept=".txt,.md,.markdown,.csv,.json,.html,.pdf,text/*,application/pdf"
              onChange={(e) => setForm((f) => ({ ...f, file: e.target.files?.[0] || null }))} />
            {form.file && <div className="t-caption fg-2" style={{ marginTop: 6 }}>{form.file.name} · {(form.file.size / 1024).toFixed(1)} KB</div>}
          </Field>
        )}
        {addErr && <div className="t-caption" style={{ color: "var(--danger, #c00)", marginTop: 4 }}>⚠ {addErr}</div>}
      </Modal>
    </div>
    </div>
  );
}

/* ---------------- Q&A pairs (custom kinds + tags) ---------------- */

const BUILTIN_KINDS = ["faq", "error_workaround"];

function QA({ project }: { project: any }) {
  const [rows, setRows] = useState<QaPair[]>([]);
  const [kind, setKind] = useState<string | null>(null); // null = All pairs
  const [newKind, setNewKind] = useState<string | null>(null); // non-null = naming a new kind
  const [form, setForm] = useState({ question: "", answer: "", kind: "faq", tags: "" });
  const reload = useCallback(() => { if (project?.id) api.listQa(project.id).then(setRows).catch(() => {}); }, [project?.id]);
  useEffect(() => { reload(); }, [reload]);

  const kinds = useMemo(() => {
    const set = new Set<string>(BUILTIN_KINDS);
    rows.forEach((q) => { if (q.kind) set.add(q.kind); });
    return [...set].sort();
  }, [rows]);

  // Selecting a kind in the rail locks the add-form kind to it (mirrors Files/folders).
  const lockedKind = kind;
  const effectiveKind = lockedKind ?? (form.kind.trim() || "faq");
  const visible = kind === null ? rows : rows.filter((q) => q.kind === kind);
  // Hide the Kind column when viewing one kind — the rail already says which.
  const showKindCol = kind === null;

  async function add() {
    if (!form.question.trim()) return;
    const tags = form.tags.split(",").map((t) => t.trim()).filter(Boolean);
    await api.addQa(project.id, { question: form.question, answer: form.answer, kind: effectiveKind, tags });
    setForm({ question: "", answer: "", kind: form.kind, tags: "" }); reload();
  }

  function KindRow({ value, label, count }: { value: string | null; label: string; count: number }) {
    const active = kind === value;
    return (
      <button onClick={() => setKind(value)} className="row spread" style={{
        width: "100%", alignItems: "center", padding: "7px 10px", borderRadius: 7, border: "none", cursor: "pointer",
        background: active ? "var(--bg-3)" : "transparent", color: active ? "var(--fg-0)" : "var(--fg-1)", fontSize: 13,
      }}>
        <span className="row gap2" style={{ alignItems: "center", minWidth: 0 }}><Icon name={value === null ? "list" : "n_qa"} size={14} /><span className="truncate">{label}</span></span>
        <span className="t-caption fg-2 mono">{count}</span>
      </button>
    );
  }

  return (
    <div className="row" style={{ gap: 18, alignItems: "flex-start" }}>
      {/* kind list */}
      <div className="card col" style={{ width: 218, flex: "none", padding: 10, gap: 2 }}>
        <KindRow value={null} label="All pairs" count={rows.length} />
        {kinds.map((k) => {
          const count = rows.filter((q) => q.kind === k).length;
          if (!count && kind !== k) return null;
          return <KindRow key={k} value={k} label={k} count={count} />;
        })}
        {newKind === null ? (
          <button className="btn btn-ghost btn-sm" style={{ justifyContent: "flex-start", marginTop: 4 }} onClick={() => setNewKind("")}>
            <Icon name="plus" size={13} />New kind
          </button>
        ) : (
          <input
            autoFocus className="input" style={{ marginTop: 4, fontSize: 13 }} placeholder="Kind name…" value={newKind}
            onChange={(e) => setNewKind(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newKind.trim()) {
                // Kinds exist through their pairs: select it + prime the add form.
                const nk = newKind.trim();
                setForm((f) => ({ ...f, kind: nk })); setKind(nk); setNewKind(null);
              }
              if (e.key === "Escape") setNewKind(null);
            }}
            onBlur={() => setNewKind(null)}
          />
        )}
        <div className="t-caption fg-2" style={{ padding: "6px 10px 2px" }}>
          Kinds are free-form categories. qa_lookup nodes can filter by kind.
        </div>
      </div>

      {/* add form + table */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="card" style={{ padding: 14, marginBottom: 16 }}>
          <div className="row spread" style={{ marginBottom: 10 }}>
            <div className="t-h3">Add Q&A pair</div>
            {lockedKind ? (
              <span className="row gap2 t-caption fg-1" style={{ alignItems: "center" }}><Icon name="n_qa" size={13} />kind: <b>{lockedKind}</b></span>
            ) : (
              <div style={{ width: 200 }}>
                <input className="input" list="qa-kinds" value={form.kind} placeholder="Kind (e.g. faq, billing)" onChange={(e) => setForm((f) => ({ ...f, kind: e.target.value }))} title="Category. Pick an existing kind or type a new one." />
                <datalist id="qa-kinds">{kinds.map((k) => <option key={k} value={k} />)}</datalist>
              </div>
            )}
          </div>
          <div className="row gap2" style={{ marginBottom: 8 }}>
            <input className="input" style={{ flex: 1 }} placeholder="Question" value={form.question} onChange={(e) => setForm((f) => ({ ...f, question: e.target.value }))} />
          </div>
          <div className="row gap2" style={{ marginBottom: 8 }}>
            <textarea className="textarea" style={{ flex: 1, minHeight: 44 }} rows={2} placeholder="Answer" value={form.answer} onChange={(e) => setForm((f) => ({ ...f, answer: e.target.value }))} />
          </div>
          <div className="row gap2">
            <input className="input" style={{ flex: 1 }} placeholder="Tags (comma separated, optional)" value={form.tags} onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))} />
            <button className="btn btn-primary" onClick={add}><Icon name="plus" size={14} />Add</button>
          </div>
        </div>

        <div className="card" style={{ overflow: "hidden" }}>
          <table className="tbl"><thead><tr><th>Question</th><th>Answer</th>{showKindCol && <th>Kind</th>}<th>Tags</th><th /></tr></thead>
            <tbody>
              {visible.map((q) => (
                <tr key={q.id}>
                  <td style={{ fontWeight: 600, maxWidth: 260 }} className="truncate">{q.question}</td>
                  <td className="fg-1 truncate" style={{ maxWidth: 280 }}>{q.answer}</td>
                  {showKindCol && <td><span className="typechip">{q.kind}</span></td>}
                  <td className="fg-2 t-caption truncate" style={{ maxWidth: 140 }}>{(q.tags || []).join(", ") || "—"}</td>
                  <td style={{ textAlign: "right" }}><button className="iconbtn" onClick={async () => { await api.deleteQa(project.id, q.id); reload(); }}><Icon name="trash" size={15} /></button></td>
                </tr>
              ))}
              {visible.length === 0 && <tr><td colSpan={showKindCol ? 5 : 4}><div className="fg-2" style={{ padding: 22, textAlign: "center" }}>{rows.length === 0 ? "No Q&A pairs yet." : "No pairs of this kind yet."}</div></td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Search debugger ---------------- */

function SearchDebugger({ project }: { project: any }) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searched, setSearched] = useState(false);
  const [folders, setFolders] = useState<string[]>([]);
  const [folder, setFolder] = useState("");
  useEffect(() => { if (project?.id) api.listFolders(project.id).then(setFolders).catch(() => {}); }, [project?.id]);
  async function run() {
    const h = await api.searchKnowledge(project.id, q, 8, folder ? [folder] : undefined);
    setHits(h); setSearched(true);
  }
  return (
    <>
      <div className="row gap2" style={{ marginBottom: 14 }}>
        <input className="input" style={{ flex: 1 }} placeholder="Query the knowledge base…" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && run()} />
        {folders.length > 0 && (
          <select className="select" style={{ width: 160 }} value={folder} onChange={(e) => setFolder(e.target.value)}>
            <option value="">All folders</option>
            {folders.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        )}
        <button className="btn btn-primary" onClick={run}><Icon name="search" size={14} />Search</button>
      </div>
      <div className="col gap2">
        {hits.map((h, i) => (
          <div key={i} className="card" style={{ padding: 12 }}>
            <div className="row spread" style={{ marginBottom: 4 }}>
              <span className="t-caption fg-2 mono">{h.source_id?.slice(0, 12) || "—"}</span>
              <span className="chip chip-mono" style={{ color: "var(--signal)" }}>score {h.score.toFixed(3)}</span>
            </div>
            <div className="t-body-sm">{h.text}</div>
          </div>
        ))}
        {searched && hits.length === 0 && <div className="fg-2" style={{ padding: 22, textAlign: "center" }}>No matches. Add sources first.</div>}
      </div>
    </>
  );
}
