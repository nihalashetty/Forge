/* Forge shared UI primitives. Exported on window. */
const { useState, useRef, useEffect, useLayoutEffect, useMemo, useCallback } = React;

/* ---------------- Sparkline ---------------- */
function Sparkline({ data, w = 80, h = 24, color = 'var(--accent)', fill = true, strokeW = 1.5 }) {
  const max = Math.max(...data, 1), min = Math.min(...data, 0);
  const rng = max - min || 1;
  const pts = data.map((v, i) => [i / (data.length - 1) * w, h - 2 - ((v - min) / rng) * (h - 4)]);
  const d = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ');
  const area = d + ` L${w} ${h} L0 ${h} Z`;
  const gid = useMemo(() => 'sg' + Math.random().toString(36).slice(2, 7), []);
  return React.createElement('svg', { width: w, height: h, style: { display: 'block', overflow: 'visible' } },
    fill && React.createElement('defs', null,
      React.createElement('linearGradient', { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 },
        React.createElement('stop', { offset: 0, stopColor: color, stopOpacity: 0.22 }),
        React.createElement('stop', { offset: 1, stopColor: color, stopOpacity: 0 }))),
    fill && React.createElement('path', { d: area, fill: `url(#${gid})`, stroke: 'none' }),
    React.createElement('path', { d, fill: 'none', stroke: color, strokeWidth: strokeW, strokeLinejoin: 'round', strokeLinecap: 'round' }));
}

/* ---------------- Donut ---------------- */
function Donut({ segments, size = 120, thickness = 16, center }) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const R = (size - thickness) / 2, C = 2 * Math.PI * R;
  let off = 0;
  return React.createElement('div', { style: { position: 'relative', width: size, height: size } },
    React.createElement('svg', { width: size, height: size, style: { transform: 'rotate(-90deg)' } },
      React.createElement('circle', { cx: size / 2, cy: size / 2, r: R, fill: 'none', stroke: 'var(--bg-3)', strokeWidth: thickness }),
      segments.map((s, i) => {
        const len = (s.value / total) * C;
        const el = React.createElement('circle', { key: i, cx: size / 2, cy: size / 2, r: R, fill: 'none',
          stroke: s.color, strokeWidth: thickness, strokeDasharray: `${len} ${C - len}`, strokeDashoffset: -off,
          style: { transition: 'stroke-dasharray .6s var(--ease)' } });
        off += len; return el;
      })),
    center && React.createElement('div', { style: { position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' } }, center));
}

/* ---------------- TokenMeter (signature) ----------------
   Animated horizontal bar showing raw vs projected token cost.
   variant: 'compact' (inline) | 'full' (with labels) */
function TokenMeter({ raw, projected, max, compact = false, animateKey }) {
  const cap = max || Math.max(raw, projected, 1) * 1.1;
  const [shown, setShown] = useState('raw');
  useEffect(() => {
    setShown('raw');
    const t = setTimeout(() => setShown('proj'), 420);
    return () => clearTimeout(t);
  }, [animateKey]);
  const pctRaw = Math.min(100, raw / cap * 100);
  const pctProj = Math.min(100, projected / cap * 100);
  const saved = raw > 0 ? Math.round((1 - projected / raw) * 100) : 0;
  if (compact) {
    return React.createElement('div', { className: 'row gap2', style: { fontSize: 11 } },
      React.createElement('div', { style: { position: 'relative', width: 64, height: 6, borderRadius: 999, background: 'var(--bg-3)', overflow: 'hidden' } },
        React.createElement('i', { style: { position: 'absolute', inset: 0, width: pctProj + '%', background: 'var(--signal)', borderRadius: 999, transition: 'width .5s var(--ease)' } })),
      React.createElement('span', { className: 'mono-sm', style: { color: 'var(--fg-1)' } }, projected),
      saved > 0 && React.createElement('span', { className: 'pill pill-ok', style: { height: 16 } }, '−' + saved + '%'));
  }
  return React.createElement('div', { className: 'col gap2' },
    React.createElement('div', { className: 'row spread', style: { fontSize: 11 } },
      React.createElement('span', { className: 't-micro' }, 'Context cost'),
      React.createElement('span', { className: 'mono-sm', style: { color: shown === 'proj' ? 'var(--signal)' : 'var(--fg-1)' } },
        (shown === 'proj' ? projected : raw).toLocaleString() + ' tok')),
    React.createElement('div', { style: { position: 'relative', height: 10, borderRadius: 999, background: 'var(--bg-3)', overflow: 'hidden' } },
      React.createElement('div', { style: { position: 'absolute', top: 0, bottom: 0, left: 0, width: pctRaw + '%', background: 'var(--line-strong)', borderRadius: 999 } }),
      React.createElement('div', { style: { position: 'absolute', top: 0, bottom: 0, left: 0, width: (shown === 'proj' ? pctProj : pctRaw) + '%', background: shown === 'proj' ? 'linear-gradient(90deg,var(--signal),var(--signal))' : 'var(--accent)', borderRadius: 999, transition: 'width .55s var(--ease), background .3s', boxShadow: shown === 'proj' ? '0 0 12px var(--signal-glow)' : 'none' } })),
    React.createElement('div', { className: 'row spread', style: { fontSize: 11, color: 'var(--fg-2)' } },
      React.createElement('span', null, 'raw ', React.createElement('b', { className: 'mono-sm', style: { color: 'var(--fg-1)' } }, raw.toLocaleString())),
      React.createElement('span', null, '→ projected ', React.createElement('b', { className: 'mono-sm', style: { color: 'var(--signal)' } }, projected.toLocaleString())),
      saved > 0 && React.createElement('span', { className: 'pill pill-ok' }, React.createElement(Icon, { name: 'bolt', size: 11 }), saved + '% saved')));
}

/* ---------------- StatusPill ---------------- */
function StatusPill({ status, label }) {
  const map = {
    done: ['pill-ok', 'Done'], pass: ['pill-ok', 'Passing'], active: ['pill-ok', 'Active'], ready: ['pill-ok', 'Ready'],
    error: ['pill-err', 'Error'], fail: ['pill-err', 'Failing'],
    interrupted: ['pill-warn', 'Interrupted'], processing: ['pill-warn', 'Processing'], draft: ['pill-muted', 'Draft'],
    untested: ['pill-muted', 'Untested'], running: ['pill-info', 'Running'],
  };
  const [cls, def] = map[status] || ['pill-muted', status];
  return React.createElement('span', { className: 'pill ' + cls }, React.createElement('span', { className: 'dot' }), label || def);
}

/* ---------------- Avatar ---------------- */
function Avatar({ name, size = 26, color }) {
  const init = (name || '?').split(/\s|_|-/).filter(Boolean).slice(0, 2).map(s => s[0]).join('').toUpperCase();
  const hue = useMemo(() => { let h = 0; for (const c of (name || '')) h = (h * 31 + c.charCodeAt(0)) % 360; return h; }, [name]);
  return React.createElement('div', { style: { width: size, height: size, borderRadius: '50%', flex: 'none',
    background: color || `oklch(0.62 0.13 ${hue})`, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
    fontSize: size * 0.4, fontWeight: 700, fontFamily: 'var(--font-display)', letterSpacing: '-.02em' } }, init);
}

/* ---------------- Toggle ---------------- */
function Toggle({ on, onChange, signal }) {
  return React.createElement('button', { className: 'toggle' + (on ? ' on' : '') + (signal ? ' signal' : ''),
    onClick: (e) => { e.stopPropagation(); onChange && onChange(!on); }, 'aria-pressed': on });
}

/* ---------------- Segmented ---------------- */
function Segmented({ options, value, onChange }) {
  return React.createElement('div', { className: 'segmented' },
    options.map(o => {
      const val = typeof o === 'string' ? o : o.value;
      const lab = typeof o === 'string' ? o : o.label;
      return React.createElement('button', { key: val, className: value === val ? 'active' : '', onClick: () => onChange(val) },
        typeof o === 'object' && o.icon && React.createElement(Icon, { name: o.icon, size: 13, style: { marginRight: 5, verticalAlign: '-2px' } }), lab);
    }));
}

/* ---------------- Modal ---------------- */
function Modal({ open, onClose, children, width = 520, title, footer }) {
  useEffect(() => {
    if (!open) return;
    const h = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h); return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  return React.createElement('div', { className: 'fade-in', style: { position: 'fixed', inset: 0, zIndex: 8000, background: 'rgba(8,10,14,.5)', backdropFilter: 'blur(3px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }, onMouseDown: onClose },
    React.createElement('div', { className: 'card fade-up', style: { width, maxWidth: '94vw', maxHeight: '88vh', boxShadow: 'var(--sh-pop)', display: 'flex', flexDirection: 'column' }, onMouseDown: e => e.stopPropagation() },
      title && React.createElement('div', { className: 'row spread', style: { padding: '14px 18px', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', { className: 't-h1' }, title),
        React.createElement('button', { className: 'iconbtn', onClick: onClose }, React.createElement(Icon, { name: 'x', size: 17 }))),
      React.createElement('div', { className: 'scroll-y', style: { padding: 18, flex: 1 } }, children),
      footer && React.createElement('div', { className: 'row gap2', style: { padding: '12px 18px', borderTop: '1px solid var(--line)', justifyContent: 'flex-end' } }, footer)));
}

/* ---------------- Drawer (right slide-in) ---------------- */
function Drawer({ open, onClose, children, width = 440, title, sub }) {
  return React.createElement('div', { style: { position: 'fixed', inset: 0, zIndex: 7000, pointerEvents: open ? 'auto' : 'none' } },
    React.createElement('div', { onClick: onClose, style: { position: 'absolute', inset: 0, background: 'rgba(8,10,14,.4)', opacity: open ? 1 : 0, transition: 'opacity var(--dur)' } }),
    React.createElement('div', { style: { position: 'absolute', top: 0, right: 0, bottom: 0, width, maxWidth: '94vw', background: 'var(--bg-1)', borderLeft: '1px solid var(--line)', boxShadow: 'var(--sh-pop)', transform: open ? 'none' : 'translateX(100%)', transition: 'transform var(--dur-slow) var(--ease)', display: 'flex', flexDirection: 'column' } },
      title && React.createElement('div', { className: 'row spread', style: { padding: '14px 18px', borderBottom: '1px solid var(--line)' } },
        React.createElement('div', null, React.createElement('div', { className: 't-h1' }, title), sub && React.createElement('div', { className: 'fg-2 t-caption', style: { marginTop: 2 } }, sub)),
        React.createElement('button', { className: 'iconbtn', onClick: onClose }, React.createElement(Icon, { name: 'x', size: 17 }))),
      React.createElement('div', { className: 'scroll-y', style: { flex: 1 } }, children)));
}

/* ---------------- EmptyState ---------------- */
function EmptyState({ icon, title, sub, action }) {
  return React.createElement('div', { className: 'col center', style: { padding: '48px 24px', textAlign: 'center', gap: 10 } },
    React.createElement('div', { style: { width: 48, height: 48, borderRadius: 12, background: 'var(--bg-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-2)' } }, React.createElement(Icon, { name: icon, size: 24 })),
    React.createElement('div', { className: 't-h1' }, title),
    sub && React.createElement('div', { className: 'fg-2', style: { maxWidth: 320 } }, sub),
    action);
}

/* ---------------- Field ---------------- */
function Field({ label, help, children, required }) {
  return React.createElement('div', { className: 'col', style: { marginBottom: 14 } },
    label && React.createElement('label', { className: 'field-label' }, label, required && React.createElement('span', { style: { color: 'var(--accent)', marginLeft: 3 } }, '*')),
    children,
    help && React.createElement('div', { className: 'field-help' }, help));
}

/* ---------------- Tabs ---------------- */
function Tabs({ tabs, value, onChange }) {
  return React.createElement('div', { className: 'row', style: { gap: 2, borderBottom: '1px solid var(--line)' } },
    tabs.map(t => {
      const val = typeof t === 'string' ? t : t.value;
      const lab = typeof t === 'string' ? t : t.label;
      const active = value === val;
      return React.createElement('button', { key: val, onClick: () => onChange(val),
        style: { background: 'none', border: 'none', cursor: 'pointer', padding: '9px 12px', fontSize: 13, fontWeight: 600, fontFamily: 'var(--font-ui)',
          color: active ? 'var(--fg-0)' : 'var(--fg-2)', borderBottom: '2px solid ' + (active ? 'var(--accent)' : 'transparent'), marginBottom: -1, transition: 'color var(--dur-fast)' } },
        lab, typeof t === 'object' && t.count != null && React.createElement('span', { className: 'badge', style: { marginLeft: 6 } }, t.count));
    }));
}

/* ---------------- CodeBlock ---------------- */
function CodeBlock({ code, lang = 'json', copyable = true, maxHeight }) {
  const [copied, setCopied] = useState(false);
  return React.createElement('div', { style: { position: 'relative', background: 'var(--bg-0)', border: '1px solid var(--line)', borderRadius: 'var(--r-md)', overflow: 'hidden' } },
    copyable && React.createElement('button', { className: 'iconbtn', style: { position: 'absolute', top: 6, right: 6, zIndex: 2, background: 'var(--bg-1)' },
      onClick: () => { setCopied(true); setTimeout(() => setCopied(false), 1200); } },
      React.createElement(Icon, { name: copied ? 'check' : 'copy', size: 14 })),
    React.createElement('pre', { className: 'mono no-scrollbar', style: { margin: 0, padding: '12px 14px', overflow: 'auto', maxHeight, color: 'var(--fg-1)', fontSize: 12 } },
      React.createElement('code', null, code)));
}

/* ---------------- Tile (icon container) ---------------- */
function Tile({ icon, color = 'var(--accent)', size = 36, glow }) {
  return React.createElement('div', { style: { width: size, height: size, flex: 'none', borderRadius: size * 0.28, display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: `color-mix(in srgb, ${color} 14%, var(--bg-1))`, color, border: `1px solid color-mix(in srgb, ${color} 26%, transparent)`, boxShadow: glow ? `0 0 16px color-mix(in srgb, ${color} 22%, transparent)` : 'none' } },
    React.createElement(Icon, { name: icon, size: size * 0.5 }));
}

/* ---------------- Menu (dropdown) ---------------- */
function Menu({ trigger, items, align = 'right' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const h = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    window.addEventListener('mousedown', h); return () => window.removeEventListener('mousedown', h);
  }, [open]);
  return React.createElement('div', { ref, style: { position: 'relative' } },
    React.createElement('div', { onClick: () => setOpen(o => !o) }, trigger),
    open && React.createElement('div', { className: 'card fade-in', style: { position: 'absolute', top: '100%', marginTop: 4, [align]: 0, zIndex: 6000, minWidth: 168, padding: 4, boxShadow: 'var(--sh-pop)' } },
      items.map((it, i) => it.divider
        ? React.createElement('div', { key: i, className: 'divider', style: { margin: '4px 0' } })
        : React.createElement('button', { key: i, onClick: () => { setOpen(false); it.onClick && it.onClick(); },
            style: { display: 'flex', alignItems: 'center', gap: 9, width: '100%', textAlign: 'left', padding: '7px 9px', border: 'none', background: 'none', cursor: 'pointer', borderRadius: 5, fontSize: 13, fontFamily: 'var(--font-ui)', color: it.danger ? 'var(--err)' : 'var(--fg-1)' },
            onMouseEnter: e => e.currentTarget.style.background = 'var(--bg-3)', onMouseLeave: e => e.currentTarget.style.background = 'none' },
          it.icon && React.createElement(Icon, { name: it.icon, size: 15 }), it.label))));
}

Object.assign(window, { Sparkline, Donut, TokenMeter, StatusPill, Avatar, Toggle, Segmented, Modal, Drawer, EmptyState, Field, Tabs, CodeBlock, Tile, Menu,
  useState, useRef, useEffect, useLayoutEffect, useMemo, useCallback });
