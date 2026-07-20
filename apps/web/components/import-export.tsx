"use client";
/* Reusable Export / Import controls for the four authorable entity types (tool, workflow,
   component, agent). Export opens a select-all picker and downloads a single-type JSON
   bundle; Import uploads such a bundle and re-creates its items IN THE CURRENT PROJECT
   (new ids, auto-renamed on collision - never overwrites). Dropped into each list screen's
   header; the same bundle format works across projects. */
import { useRef, useState } from "react";
import { Icon } from "./icons";
import { Modal } from "./primitives";
import { api, ImportReport, PortableType } from "@/lib/api";

export interface PortableItem {
  id: string;
  name: string;
  sub?: string; // optional secondary line (e.g. kind / model)
}

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function Check({ checked, indeterminate }: { checked: boolean; indeterminate?: boolean }) {
  return (
    <span
      aria-checked={indeterminate ? "mixed" : checked}
      role="checkbox"
      style={{
        width: 16, height: 16, flex: "none", borderRadius: 4, display: "inline-flex", alignItems: "center", justifyContent: "center",
        border: "1.5px solid " + (checked || indeterminate ? "var(--accent)" : "var(--line-strong)"),
        background: checked || indeterminate ? "var(--accent)" : "transparent", color: "var(--fg-on-accent)",
      }}
    >
      {indeterminate ? <Icon name="minus" size={11} /> : checked ? <Icon name="check" size={11} /> : null}
    </span>
  );
}

export function ImportExport({
  project, type, typeLabel, items, onImported, size = "sm",
}: {
  project: { id: string; name?: string; slug?: string } | null | undefined;
  type: PortableType;
  typeLabel: string; // singular, lowercase (e.g. "tool")
  items: PortableItem[];
  onImported: () => void;
  size?: "sm" | "md";
}) {
  const [exportOpen, setExportOpen] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const btnCls = "btn btn-secondary " + (size === "sm" ? "btn-sm" : "");
  const plural = `${typeLabel}s`;

  const allSelected = items.length > 0 && sel.size === items.length;
  const someSelected = sel.size > 0 && !allSelected;

  function openExport() {
    setSel(new Set(items.map((i) => i.id))); // default to "select all"
    setExportOpen(true);
  }
  function toggle(id: string) {
    setSel((prev) => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function toggleAll() {
    setSel(allSelected ? new Set() : new Set(items.map((i) => i.id)));
  }

  async function doDownload() {
    if (!project || sel.size === 0) return;
    setDownloading(true);
    try {
      const ids = items.filter((i) => sel.has(i.id)).map((i) => i.id); // preserve list order
      const bundle = await api.exportBundle(project.id, type, ids);
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      const base = (project.slug || project.name || "forge").toString().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "forge";
      downloadJson(`${base}-${plural}-${stamp}.json`, bundle);
      setExportOpen(false);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setDownloading(false);
    }
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file || !project) return;
    setImporting(true);
    setError(null);
    setReport(null);
    try {
      const text = await file.text();
      let bundle: unknown;
      try {
        bundle = JSON.parse(text);
      } catch {
        throw new Error("That file isn't valid JSON. Choose a bundle exported from Forge.");
      }
      const r = await api.importBundle(project.id, type, bundle);
      setReport(r);
      onImported();
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setImporting(false);
    }
  }

  return (
    <>
      <button className={btnCls} onClick={openExport} disabled={!project || items.length === 0} title={items.length === 0 ? `No ${plural} to export` : `Export ${plural} to a file`}>
        <Icon name="download" size={14} />Export
      </button>
      <button className={btnCls} onClick={() => fileRef.current?.click()} disabled={!project || importing} title={`Import ${plural} from a file`}>
        <Icon name={importing ? "refresh" : "upload"} size={14} style={importing ? { animation: "spin 1s linear infinite" } : undefined} />
        {importing ? "Importing…" : "Import"}
      </button>
      <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: "none" }} onChange={onFile} />

      {/* Export picker: choose which rows go into the bundle (defaults to all). */}
      <Modal
        open={exportOpen}
        onClose={() => setExportOpen(false)}
        title={`Export ${plural}`}
        width={520}
        footer={
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => setExportOpen(false)}>Cancel</button>
            <button className="btn btn-primary btn-sm" onClick={doDownload} disabled={sel.size === 0 || downloading}>
              <Icon name={downloading ? "refresh" : "download"} size={14} style={downloading ? { animation: "spin 1s linear infinite" } : undefined} />
              {downloading ? "Preparing…" : `Download ${sel.size} ${sel.size === 1 ? typeLabel : plural}`}
            </button>
          </>
        }
      >
        <div className="col gap2">
          <button className="row gap2" onClick={toggleAll} style={{ alignItems: "center", background: "none", border: "none", cursor: "pointer", padding: "4px 2px", width: "100%", textAlign: "left" }}>
            <Check checked={allSelected} indeterminate={someSelected} />
            <span className="t-body-sm" style={{ fontWeight: 600 }}>Select all</span>
            <span className="fg-2 t-caption" style={{ marginLeft: "auto" }}>{sel.size}/{items.length} selected</span>
          </button>
          <div className="divider" />
          <div className="col" style={{ gap: 1, maxHeight: 380, overflowY: "auto" }}>
            {items.map((it) => {
              const on = sel.has(it.id);
              return (
                <button key={it.id} className="row gap2" onClick={() => toggle(it.id)}
                  style={{ alignItems: "center", background: on ? "var(--bg-3)" : "none", border: "none", cursor: "pointer", padding: "8px 8px", borderRadius: 7, width: "100%", textAlign: "left" }}>
                  <Check checked={on} />
                  <div className="grow" style={{ minWidth: 0 }}>
                    <div className="mono-sm truncate" style={{ fontWeight: 600 }}>{it.name}</div>
                    {it.sub && <div className="fg-2 t-caption truncate">{it.sub}</div>}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </Modal>

      {/* Import result. */}
      <Modal
        open={!!report || !!error}
        onClose={() => { setReport(null); setError(null); }}
        title={error ? "Import failed" : "Import complete"}
        width={520}
        footer={<button className="btn btn-primary btn-sm" onClick={() => { setReport(null); setError(null); }}>Done</button>}
      >
        {error ? (
          <div className="t-body-sm" style={{ color: "var(--err)", overflowWrap: "anywhere" }}>{error}</div>
        ) : report ? (
          <div className="col gap3">
            <div className="t-body-sm">
              Imported <b>{report.imported}</b> {report.imported === 1 ? typeLabel : plural}
              {report.skipped > 0 && <> · skipped <b>{report.skipped}</b></>} into <b>{project?.name}</b>.
            </div>
            {report.items.some((i) => i.renamed) && (
              <div className="card" style={{ padding: 10 }}>
                <div className="t-micro" style={{ marginBottom: 6 }}>Renamed to avoid clashes</div>
                <div className="col gap1">
                  {report.items.filter((i) => i.renamed).map((i, n) => (
                    <div key={n} className="mono-sm fg-1 truncate">{i.original_name} → {i.name}</div>
                  ))}
                </div>
              </div>
            )}
            {report.warnings.length > 0 && (
              <div className="card" style={{ padding: 10, borderColor: "var(--warn)" }}>
                <div className="t-micro" style={{ marginBottom: 6, color: "var(--warn)" }}>Heads up</div>
                <div className="col gap1">
                  {report.warnings.map((w, n) => (
                    <div key={n} className="t-caption fg-1" style={{ overflowWrap: "anywhere" }}>{w}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </Modal>
    </>
  );
}
