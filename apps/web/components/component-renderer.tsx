"use client";
/* Renders a user-authored UI component (Feature 2 — generative UI) inside a SANDBOXED
   iframe. Mustache interpolates the agent-supplied props into the saved HTML (values are
   HTML-escaped by Mustache), the CSS is scoped to the iframe document, and a tiny injected
   bridge reports content height (for auto-sizing) and posts button/form actions back to the
   host. sandbox="allow-scripts" (NO allow-same-origin) means the component can never reach
   the parent DOM, cookies, or storage — only postMessage. */
import { useEffect, useMemo, useRef, useState } from "react";
import Mustache from "mustache";

export interface ComponentDef {
  id?: string;
  name?: string;
  html: string;
  css: string;
  actions?: Record<string, any>[];
}

// Runs INSIDE the sandboxed iframe. No Date/Math.random (kept deterministic). Reports size
// and forwards clicks on [data-forge-action] elements (with any named field values).
const BRIDGE =
  "(function(){function post(m){try{parent.postMessage(Object.assign({__forge:true},m),(window.__FO||'*'))}catch(e){}}" +
  "function size(){post({type:'size',height:(document.documentElement.scrollHeight||document.body.scrollHeight)})}" +
  "window.addEventListener('load',function(){size();setTimeout(size,60)});" +
  "try{new ResizeObserver(size).observe(document.documentElement)}catch(e){}" +
  "document.addEventListener('click',function(e){var el=e.target&&e.target.closest?e.target.closest('[data-forge-action]'):null;if(!el)return;e.preventDefault();" +
  "var scope=el.closest('form')||document;var fields={};scope.querySelectorAll('[name]').forEach(function(i){var t=(i.type||'').toLowerCase();if((t==='checkbox'||t==='radio')&&!i.checked)return;if(Object.prototype.hasOwnProperty.call(fields,i.name)){fields[i.name]=[].concat(fields[i.name],i.value)}else{fields[i.name]=i.value}});" +
  "post({type:'action',action:el.getAttribute('data-forge-action'),fields:fields})});})();";

export function ComponentRenderer({
  def,
  props,
  onAction,
}: {
  def: ComponentDef;
  props: Record<string, any>;
  onAction?: (action: string, fields: Record<string, string>, def: ComponentDef) => void;
}) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(60);
  // Keep latest def/onAction in refs so the message listener registers ONCE — new def/onAction
  // object identities on each parent render would otherwise re-bind it every render (F19/F33).
  const defRef = useRef(def);
  defRef.current = def;
  const actionRef = useRef(onAction);
  actionRef.current = onAction;

  // Rebuild the iframe document only when the template or props actually change — not on every
  // parent re-render (e.g. while a sibling message streams), which would reload the iframe (F19).
  const srcDoc = useMemo(() => {
    let body = "";
    try {
      body = Mustache.render(def.html || "", props || {});
    } catch (e: any) {
      body = `<pre style="color:#b00020;font:12px monospace;white-space:pre-wrap">template error: ${String(e?.message || e)}</pre>`;
    }
    // Bake our origin in so the sandboxed (opaque-origin) frame can postMessage back with a
    // concrete targetOrigin instead of "*" (review hardening). The parent is same-origin as us.
    const fo = typeof window !== "undefined" ? window.location.origin : "*";
    return (
      `<!DOCTYPE html><html><head><meta charset="utf-8">` +
      `<style>html,body{margin:0;padding:0;background:transparent}${def.css || ""}</style></head>` +
      `<body>${body}<script>window.__FO=${JSON.stringify(fo)};${BRIDGE}</script></body></html>`
    );
  }, [def.html, def.css, props]);

  useEffect(() => {
    function onMsg(e: MessageEvent) {
      if (!ref.current || e.source !== ref.current.contentWindow) return;
      // Sandboxed (allow-scripts, no same-origin) iframes post from the opaque "null" origin;
      // accept that or our own origin, reject anything else (audit F27).
      if (e.origin !== "null" && e.origin !== window.location.origin) return;
      const d: any = e.data || {};
      if (!d.__forge) return;
      if (d.type === "size" && typeof d.height === "number") {
        setHeight(Math.min(2000, Math.max(40, Math.ceil(d.height))));
      } else if (d.type === "action") {
        actionRef.current?.(String(d.action || ""), d.fields || {}, defRef.current);
      }
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const actions = def.actions || [];
  return (
    <div>
      <iframe
        ref={ref}
        sandbox="allow-scripts"
        srcDoc={srcDoc}
        title={def.name || "component"}
        style={{ width: "100%", height, border: "none", display: "block", background: "transparent" }}
      />
      {actions.length > 0 && (
        <div className="row gap2 wrap" style={{ marginTop: 8 }}>
          {actions.map((a: any, i: number) => (
            <button
              key={a.id || i}
              className="btn btn-secondary btn-sm"
              onClick={() => onAction?.(String(a.id || a.label || ""), {}, def)}
            >
              {a.label || a.id || "action"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
