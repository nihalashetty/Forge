"use client";
/* Forge shared UI primitives - ported from the design handoff (primitives.jsx). */
import { CSSProperties, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "./icons";

/* ---------------- Sparkline ---------------- */
export function Sparkline({
  data, w = 80, h = 24, color = "var(--accent)", fill = true, strokeW = 1.5,
}: { data: number[]; w?: number; h?: number; color?: string; fill?: boolean; strokeW?: number }) {
  const max = Math.max(...data, 1), min = Math.min(...data, 0);
  const rng = max - min || 1;
  const pts = data.map((v, i) => [(i / (data.length - 1)) * w, h - 2 - ((v - min) / rng) * (h - 4)]);
  const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const area = d + ` L${w} ${h} L0 ${h} Z`;
  const gid = useMemo(() => "sg" + Math.random().toString(36).slice(2, 7), []);
  return (
    <svg width={w} height={h} style={{ display: "block", overflow: "visible" }}>
      {fill && (
        <defs>
          <linearGradient id={gid} x1={0} y1={0} x2={0} y2={1}>
            <stop offset={0} stopColor={color} stopOpacity={0.22} />
            <stop offset={1} stopColor={color} stopOpacity={0} />
          </linearGradient>
        </defs>
      )}
      {fill && <path d={area} fill={`url(#${gid})`} stroke="none" />}
      <path d={d} fill="none" stroke={color} strokeWidth={strokeW} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/* ---------------- Donut ---------------- */
export function Donut({
  segments, size = 120, thickness = 16, center,
}: { segments: { value: number; color: string }[]; size?: number; thickness?: number; center?: ReactNode }) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const R = (size - thickness) / 2, C = 2 * Math.PI * R;
  let off = 0;
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg width={size} height={size} style={{ transform: "rotate(-90deg)" }}>
        <circle cx={size / 2} cy={size / 2} r={R} fill="none" stroke="var(--bg-3)" strokeWidth={thickness} />
        {segments.map((s, i) => {
          const len = (s.value / total) * C;
          const el = (
            <circle key={i} cx={size / 2} cy={size / 2} r={R} fill="none" stroke={s.color}
              strokeWidth={thickness} strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-off}
              style={{ transition: "stroke-dasharray .6s var(--ease)" }} />
          );
          off += len;
          return el;
        })}
      </svg>
      {center && (
        <div style={{ position: "absolute", inset: 0, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
          {center}
        </div>
      )}
    </div>
  );
}

/* ---------------- TokenMeter (signature) ---------------- */
export function TokenMeter({
  raw, projected, max, compact = false, animateKey,
}: { raw: number; projected: number; max?: number; compact?: boolean; animateKey?: any }) {
  const cap = max || Math.max(raw, projected, 1) * 1.1;
  const [shown, setShown] = useState<"raw" | "proj">("raw");
  useEffect(() => {
    setShown("raw");
    const t = setTimeout(() => setShown("proj"), 420);
    return () => clearTimeout(t);
  }, [animateKey]);
  const pctRaw = Math.min(100, (raw / cap) * 100);
  const pctProj = Math.min(100, (projected / cap) * 100);
  const saved = raw > 0 ? Math.round((1 - projected / raw) * 100) : 0;
  if (compact) {
    return (
      <div className="row gap2" style={{ fontSize: 11 }}>
        <div style={{ position: "relative", width: 64, height: 6, borderRadius: 999, background: "var(--bg-3)", overflow: "hidden" }}>
          <i style={{ position: "absolute", inset: 0, width: pctProj + "%", background: "var(--signal)", borderRadius: 999, transition: "width .5s var(--ease)" }} />
        </div>
        <span className="mono-sm" style={{ color: "var(--fg-1)" }}>{projected}</span>
        {saved > 0 && <span className="pill pill-ok" style={{ height: 16 }}>{"−" + saved + "%"}</span>}
      </div>
    );
  }
  return (
    <div className="col gap2">
      <div className="row spread" style={{ fontSize: 11 }}>
        <span className="t-micro">Context cost</span>
        <span className="mono-sm" style={{ color: shown === "proj" ? "var(--signal)" : "var(--fg-1)" }}>
          {(shown === "proj" ? projected : raw).toLocaleString()} tok
        </span>
      </div>
      <div style={{ position: "relative", height: 10, borderRadius: 999, background: "var(--bg-3)", overflow: "hidden" }}>
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: pctRaw + "%", background: "var(--line-strong)", borderRadius: 999 }} />
        <div style={{ position: "absolute", top: 0, bottom: 0, left: 0, width: (shown === "proj" ? pctProj : pctRaw) + "%", background: shown === "proj" ? "var(--signal)" : "var(--accent)", borderRadius: 999, transition: "width .55s var(--ease), background .3s", boxShadow: shown === "proj" ? "0 0 12px var(--signal-glow)" : "none" }} />
      </div>
      <div className="row spread" style={{ fontSize: 11, color: "var(--fg-2)" }}>
        <span>raw <b className="mono-sm" style={{ color: "var(--fg-1)" }}>{raw.toLocaleString()}</b></span>
        <span>→ projected <b className="mono-sm" style={{ color: "var(--signal)" }}>{projected.toLocaleString()}</b></span>
        {saved > 0 && <span className="pill pill-ok"><Icon name="bolt" size={11} />{saved + "% saved"}</span>}
      </div>
    </div>
  );
}

/* ---------------- StatusPill ---------------- */
export function StatusPill({ status, label }: { status: string; label?: string }) {
  const map: Record<string, [string, string]> = {
    done: ["pill-ok", "Done"], pass: ["pill-ok", "Passing"], active: ["pill-ok", "Active"], ready: ["pill-ok", "Ready"],
    error: ["pill-err", "Error"], fail: ["pill-err", "Failing"],
    interrupted: ["pill-warn", "Interrupted"], processing: ["pill-warn", "Processing"], draft: ["pill-muted", "Draft"],
    untested: ["pill-muted", "Untested"], running: ["pill-info", "Running"],
  };
  const [cls, def] = map[status] || ["pill-muted", status];
  return <span className={"pill " + cls}><span className="dot" />{label || def}</span>;
}

/* ---------------- Avatar ---------------- */
export function Avatar({ name, size = 26, color }: { name: string; size?: number; color?: string }) {
  const init = (name || "?").split(/\s|_|-/).filter(Boolean).slice(0, 2).map((s) => s[0]).join("").toUpperCase();
  const hue = useMemo(() => { let h = 0; for (const c of name || "") h = (h * 31 + c.charCodeAt(0)) % 360; return h; }, [name]);
  return (
    <div style={{ width: size, height: size, borderRadius: "50%", flex: "none", background: color || `oklch(0.62 0.13 ${hue})`, color: "#fff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: size * 0.4, fontWeight: 700, fontFamily: "var(--font-display)", letterSpacing: "-.02em" }}>
      {init}
    </div>
  );
}

/* ---------------- Toggle ---------------- */
export function Toggle({ on, onChange, signal }: { on: boolean; onChange?: (v: boolean) => void; signal?: boolean }) {
  return (
    <button className={"toggle" + (on ? " on" : "") + (signal ? " signal" : "")}
      onClick={(e) => { e.stopPropagation(); onChange && onChange(!on); }} aria-pressed={on} />
  );
}

/* ---------------- Segmented ---------------- */
export function Segmented({ options, value, onChange }: { options: (string | { value: string; label: string; icon?: string })[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="segmented">
      {options.map((o) => {
        const val = typeof o === "string" ? o : o.value;
        const lab = typeof o === "string" ? o : o.label;
        return (
          <button key={val} className={value === val ? "active" : ""} onClick={() => onChange(val)}>
            {typeof o === "object" && o.icon && <Icon name={o.icon} size={13} style={{ marginRight: 5, verticalAlign: "-2px" }} />}
            {lab}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- Modal ---------------- */
export function Modal({ open, onClose, children, width = 520, title, footer }: { open: boolean; onClose: () => void; children: ReactNode; width?: number; title?: string; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fade-in" style={{ position: "fixed", inset: 0, zIndex: 8000, background: "rgba(8,10,14,.5)", backdropFilter: "blur(3px)", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }} onMouseDown={onClose}>
      <div className="card fade-up" style={{ width, maxWidth: "94vw", maxHeight: "88vh", boxShadow: "var(--sh-pop)", display: "flex", flexDirection: "column" }} onMouseDown={(e) => e.stopPropagation()}>
        {title && (
          <div className="row spread" style={{ padding: "14px 18px", borderBottom: "1px solid var(--line)" }}>
            <div className="t-h1">{title}</div>
            <button className="iconbtn" onClick={onClose}><Icon name="x" size={17} /></button>
          </div>
        )}
        <div className="scroll-y" style={{ padding: 18, flex: 1 }}>{children}</div>
        {footer && <div className="row gap2" style={{ padding: "12px 18px", borderTop: "1px solid var(--line)", justifyContent: "flex-end" }}>{footer}</div>}
      </div>
    </div>
  );
}

/* ---------------- Drawer ---------------- */
export function Drawer({ open, onClose, children, width = 440, title, sub }: { open: boolean; onClose: () => void; children: ReactNode; width?: number; title?: string; sub?: string }) {
  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 7000, pointerEvents: open ? "auto" : "none" }}>
      <div onClick={onClose} style={{ position: "absolute", inset: 0, background: "rgba(8,10,14,.4)", opacity: open ? 1 : 0, transition: "opacity var(--dur)" }} />
      <div style={{ position: "absolute", top: 0, right: 0, bottom: 0, width, maxWidth: "94vw", background: "var(--bg-1)", borderLeft: "1px solid var(--line)", boxShadow: "var(--sh-pop)", transform: open ? "none" : "translateX(100%)", transition: "transform var(--dur-slow) var(--ease)", display: "flex", flexDirection: "column" }}>
        {title && (
          <div className="row spread" style={{ padding: "14px 18px", borderBottom: "1px solid var(--line)" }}>
            <div>
              <div className="t-h1">{title}</div>
              {sub && <div className="fg-2 t-caption" style={{ marginTop: 2 }}>{sub}</div>}
            </div>
            <button className="iconbtn" onClick={onClose}><Icon name="x" size={17} /></button>
          </div>
        )}
        <div className="scroll-y" style={{ flex: 1 }}>{children}</div>
      </div>
    </div>
  );
}

/* ---------------- EmptyState ---------------- */
export function EmptyState({ icon, title, sub, action }: { icon: string; title: string; sub?: string; action?: ReactNode }) {
  return (
    <div className="col center" style={{ padding: "48px 24px", textAlign: "center", gap: 10 }}>
      <div style={{ width: 48, height: 48, borderRadius: 12, background: "var(--bg-3)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--fg-2)" }}>
        <Icon name={icon} size={24} />
      </div>
      <div className="t-h1">{title}</div>
      {sub && <div className="fg-2" style={{ maxWidth: 320 }}>{sub}</div>}
      {action}
    </div>
  );
}

/* ---------------- Field ---------------- */
export function Field({ label, help, children, required }: { label?: string; help?: string; children: ReactNode; required?: boolean }) {
  return (
    <div className="col" style={{ marginBottom: 14 }}>
      {label && <label className="field-label">{label}{required && <span style={{ color: "var(--accent)", marginLeft: 3 }}>*</span>}</label>}
      {children}
      {help && <div className="field-help">{help}</div>}
    </div>
  );
}

/* ---------------- Tabs ---------------- */
export function Tabs({ tabs, value, onChange }: { tabs: (string | { value: string; label: string; count?: number })[]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="row" style={{ gap: 2, borderBottom: "1px solid var(--line)" }}>
      {tabs.map((t) => {
        const val = typeof t === "string" ? t : t.value;
        const lab = typeof t === "string" ? t : t.label;
        const active = value === val;
        return (
          <button key={val} onClick={() => onChange(val)}
            style={{ background: "none", border: "none", cursor: "pointer", padding: "9px 12px", fontSize: 13, fontWeight: 600, fontFamily: "var(--font-ui)", color: active ? "var(--fg-0)" : "var(--fg-2)", borderBottom: "2px solid " + (active ? "var(--accent)" : "transparent"), marginBottom: -1, transition: "color var(--dur-fast)" }}>
            {lab}
            {typeof t === "object" && t.count != null && <span className="badge" style={{ marginLeft: 6 }}>{t.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

/* ---------------- CodeBlock ---------------- */
export function CodeBlock({ code, copyable = true, maxHeight }: { code: string; lang?: string; copyable?: boolean; maxHeight?: number }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ position: "relative", background: "var(--bg-0)", border: "1px solid var(--line)", borderRadius: "var(--r-md)", overflow: "hidden" }}>
      {copyable && (
        <button className="iconbtn" style={{ position: "absolute", top: 6, right: 6, zIndex: 2, background: "var(--bg-1)" }}
          onClick={() => { navigator.clipboard?.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1200); }}>
          <Icon name={copied ? "check" : "copy"} size={14} />
        </button>
      )}
      <pre className="mono no-scrollbar" style={{ margin: 0, padding: "12px 14px", overflow: "auto", maxHeight, color: "var(--fg-1)", fontSize: 12 }}>
        <code>{code}</code>
      </pre>
    </div>
  );
}

/* ---------------- Tile ---------------- */
export function Tile({ icon, color = "var(--accent)", size = 36, glow }: { icon: string; color?: string; size?: number; glow?: boolean }) {
  return (
    <div style={{ width: size, height: size, flex: "none", borderRadius: size * 0.28, display: "flex", alignItems: "center", justifyContent: "center", background: `color-mix(in srgb, ${color} 14%, var(--bg-1))`, color, border: `1px solid color-mix(in srgb, ${color} 26%, transparent)`, boxShadow: glow ? `0 0 16px color-mix(in srgb, ${color} 22%, transparent)` : "none" }}>
      <Icon name={icon} size={size * 0.5} />
    </div>
  );
}

/* ---------------- Menu ---------------- */
export function Menu({ trigger, items, align = "right" }: { trigger: ReactNode; items: { label?: string; icon?: string; onClick?: () => void; danger?: boolean; divider?: boolean }[]; align?: "left" | "right" }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", h);
    return () => window.removeEventListener("mousedown", h);
  }, [open]);
  const alignStyle: CSSProperties = align === "right" ? { right: 0 } : { left: 0 };
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <div onClick={() => setOpen((o) => !o)}>{trigger}</div>
      {open && (
        <div className="card fade-in" style={{ position: "absolute", top: "100%", marginTop: 4, ...alignStyle, zIndex: 6000, minWidth: 168, padding: 4, boxShadow: "var(--sh-pop)" }}>
          {items.map((it, i) =>
            it.divider ? (
              <div key={i} className="divider" style={{ margin: "4px 0" }} />
            ) : (
              <button key={i} onClick={() => { setOpen(false); it.onClick && it.onClick(); }}
                style={{ display: "flex", alignItems: "center", gap: 9, width: "100%", textAlign: "left", padding: "7px 9px", border: "none", background: "none", cursor: "pointer", borderRadius: 5, fontSize: 13, fontFamily: "var(--font-ui)", color: it.danger ? "var(--err)" : "var(--fg-1)" }}>
                {it.icon && <Icon name={it.icon} size={15} />}
                {it.label}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
