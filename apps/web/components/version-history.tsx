"use client";
/* Reusable version-history drawer: lists recent versions of an entity (workflow, agent,
   tool, …) with author + timestamp + label and a Restore action. Wired to the
   /v1/versions/{entity_type}/{entity_id} endpoints. Drop <VersionHistory .../> into any
   editor toolbar. */
import { useCallback, useEffect, useState } from "react";
import { Icon } from "./icons";
import { Drawer } from "./primitives";
import { api, EntityType, EntityVersion } from "@/lib/api";

/** Compact "3m ago" / "2d ago" relative time, falling back to a locale date. */
function relTime(iso?: string | null): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return String(iso);
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}

export function VersionHistory({
  entityType,
  entityId,
  entityLabel,
  onRestored,
  buttonClassName = "btn btn-secondary btn-sm",
  buttonLabel = "History",
  allowRestore = true,
}: {
  entityType: EntityType;
  entityId?: string | null;
  entityLabel?: string;
  onRestored?: () => void;
  buttonClassName?: string;
  buttonLabel?: string;
  // Some entities (e.g. knowledge sources) version only their config metadata, not the
  // embedded content, so a "restore" would be misleading - show read-only history instead.
  allowRestore?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<EntityVersion[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [snapshots, setSnapshots] = useState<Record<number, string>>({});

  const load = useCallback(() => {
    if (!entityId) return;
    setRows(null);
    setErr(null);
    api
      .listVersions(entityType, entityId)
      .then((v) => setRows(v))
      .catch((e) => setErr(String(e?.message || e)));
  }, [entityType, entityId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  async function peek(versionNo: number) {
    if (expanded === versionNo) {
      setExpanded(null);
      return;
    }
    setExpanded(versionNo);
    if (snapshots[versionNo] || !entityId) return;
    try {
      const v = await api.getVersion(entityType, entityId, versionNo);
      setSnapshots((s) => ({ ...s, [versionNo]: JSON.stringify(v.snapshot ?? {}, null, 2) }));
    } catch {
      setSnapshots((s) => ({ ...s, [versionNo]: "(could not load snapshot)" }));
    }
  }

  async function restore(versionNo: number) {
    if (!entityId) return;
    if (!window.confirm(`Restore version ${versionNo}? The current state is saved as a new version first, so this is reversible.`)) return;
    setRestoring(versionNo);
    try {
      await api.restoreVersion(entityType, entityId, versionNo);
      setOpen(false);
      onRestored?.();
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setRestoring(null);
    }
  }

  const latest = rows && rows.length ? Math.max(...rows.map((r) => r.version_no)) : null;

  return (
    <>
      <button className={buttonClassName} onClick={() => setOpen(true)} disabled={!entityId} title="Version history">
        <Icon name="clock" size={14} />
        {buttonLabel}
      </button>
      <Drawer open={open} onClose={() => setOpen(false)} title="Version history" sub={entityLabel} width={420}>
        <div className="col" style={{ padding: 14, gap: 8 }}>
          {err && (
            <div className="card" style={{ padding: 12, color: "var(--err)" }}>{err}</div>
          )}
          {!err && rows === null && (
            <div className="fg-2 t-caption" style={{ padding: "8px 2px" }}>Loading versions…</div>
          )}
          {!err && rows !== null && rows.length === 0 && (
            <div className="col center" style={{ padding: "40px 16px", textAlign: "center", gap: 8, color: "var(--fg-2)" }}>
              <Icon name="clock" size={22} />
              <div className="t-body-sm">No saved versions yet.</div>
              <div className="t-caption">Versions are captured each time you save or publish.</div>
            </div>
          )}
          {rows?.map((v) => {
            const isLatest = v.version_no === latest;
            const isOpen = expanded === v.version_no;
            return (
              <div key={v.id || v.version_no} className="card" style={{ padding: "10px 12px" }}>
                <div className="row spread" style={{ alignItems: "flex-start", gap: 8 }}>
                  <div className="col" style={{ gap: 3, minWidth: 0 }}>
                    <div className="row gap2" style={{ alignItems: "center" }}>
                      <span className="mono-sm" style={{ fontWeight: 600 }}>v{v.version_no}</span>
                      {isLatest && <span className="pill pill-muted" style={{ height: 18 }}>current</span>}
                      {v.label && <span className="t-body-sm truncate">{v.label}</span>}
                    </div>
                    <div className="t-caption fg-2 truncate">
                      {v.author_email || "unknown"}
                      {v.created_at ? ` · ${relTime(v.created_at)}` : ""}
                    </div>
                  </div>
                  <div className="row gap1" style={{ flex: "none" }}>
                    <button className="iconbtn" title="Inspect snapshot" onClick={() => peek(v.version_no)}>
                      <Icon name={isOpen ? "eyeoff" : "eye"} size={14} />
                    </button>
                    {allowRestore && (
                      <button
                        className="btn btn-secondary btn-sm"
                        disabled={isLatest || restoring != null}
                        title={isLatest ? "This is the current version" : `Restore v${v.version_no}`}
                        onClick={() => restore(v.version_no)}
                      >
                        <Icon name="rotate" size={13} />
                        {restoring === v.version_no ? "…" : "Restore"}
                      </button>
                    )}
                  </div>
                </div>
                {isOpen && (
                  <pre
                    className="mono no-scrollbar"
                    style={{ margin: "8px 0 0", padding: 10, background: "var(--bg-0)", border: "1px solid var(--line)", borderRadius: "var(--r-sm)", fontSize: 11, lineHeight: "16px", maxHeight: 220, overflow: "auto", color: "var(--fg-1)", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}
                  >
                    {snapshots[v.version_no] ?? "Loading…"}
                  </pre>
                )}
              </div>
            );
          })}
        </div>
      </Drawer>
    </>
  );
}
