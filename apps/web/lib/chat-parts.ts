/* Assembles an assistant reply into ordered parts (markdown text + inline UI components).

   The problem this solves: a component is rendered as a side-effect of a tool call, which the
   agent runs BEFORE it writes its prose (the prose is generated in the final turn, after every
   tool returns). So if we placed components in frame-arrival order, every widget would jump to
   the TOP of the reply, ahead of all text - never in the middle, never at the end.

   The fix (how ChatGPT/Claude interleave widgets): the component tool returns a tiny placeholder
   marker - [[forge:component:<instance_id>]] - and the model copies it into its reply text at the
   exact spot the widget belongs. Here we split the text on those markers and splice each
   already-received component instance into its place. The heavy props/markup never enter the
   token stream (only the ~10-token marker does); the model gets full control over ordering. */

export interface ComponentInstance {
  component_id: string;
  instance_id?: string;
  name?: string;
  version?: number;
  props?: Record<string, any>;
  actions?: Record<string, any>[];
}

export type Part =
  | { kind: "text"; text: string }
  | { kind: "component"; inst: ComponentInstance };

// The literal a component tool's ack tells the model to place. Keep in sync with the backend
// (tools/components.py COMPONENT_MARKER). Instance ids are uuid4().hex, but we accept any
// url-safe token so a slightly-mangled id still matches.
const MARKER_PREFIX = "[[forge:component:";
const MARKER_RE = /\[\[forge:component:([A-Za-z0-9_-]+)\]\]/g;

/* While streaming, the closing `]]` of a marker may not have arrived yet (e.g. the buffer ends
   with "…[[forge:comp"). Return the index where such a half-typed marker begins so the caller can
   hide it until it completes - otherwise it flashes as raw text. -1 when there's no partial tail. */
function trailingPartialMarkerStart(text: string): number {
  // 1) the "[[forge:component:" prefix itself is still being typed - longest suffix of `text`
  //    that equals a non-empty prefix of MARKER_PREFIX.
  for (let n = Math.min(MARKER_PREFIX.length, text.length); n > 0; n--) {
    if (text.slice(text.length - n) === MARKER_PREFIX.slice(0, n)) return text.length - n;
  }
  // 2) the prefix is complete and the id is streaming, but the closing `]]` hasn't arrived.
  const m = /\[\[forge:component:[A-Za-z0-9_-]*$/.exec(text);
  return m ? m.index : -1;
}

/* Split `raw` on component markers and produce ordered parts, resolving each marker to its
   component instance. Unknown markers (no matching instance) are dropped from the text. When
   not streaming, any rendered component the model never referenced with a marker is appended at
   the end (in arrival order) so a widget is never lost - and never jumps ahead of the prose. */
function assembleParts(
  raw: string,
  instances: Record<string, ComponentInstance>,
  order: string[],
  streaming: boolean,
): Part[] {
  let text = raw || "";
  if (streaming) {
    const cut = trailingPartialMarkerStart(text);
    if (cut >= 0) text = text.slice(0, cut);
  }
  const parts: Part[] = [];
  const used = new Set<string>();
  const push = (s: string) => {
    if (!s) return;
    const last = parts[parts.length - 1];
    if (last && last.kind === "text") last.text += s;
    else parts.push({ kind: "text", text: s });
  };
  const re = new RegExp(MARKER_RE.source, "g");
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const inst = instances[m[1]];
    // Render each instance at most once (a duplicated marker is ignored); an unknown id is
    // simply dropped so a stray/hallucinated marker never shows as literal text.
    if (inst && !used.has(m[1])) {
      parts.push({ kind: "component", inst });
      used.add(m[1]);
    }
    last = m.index + m[0].length;
  }
  push(text.slice(last));
  if (!streaming) {
    for (const id of order) {
      if (!used.has(id) && instances[id]) parts.push({ kind: "component", inst: instances[id] });
    }
  }
  // Drop whitespace-only text parts (e.g. the blank line between two adjacent components) - each
  // text part renders as its own markdown block, so leading/trailing whitespace is meaningless.
  return parts.filter((p) => p.kind === "component" || p.text.trim().length > 0);
}

/* Accumulates a streaming assistant reply: token text plus the component instances the agent
   rendered (keyed by instance_id). `parts()` produces the ordered render list at any point.

   Shared by every chat surface (Playground, embed widget, workflow test panel) so they stay
   consistent. */
export class ReplyAccumulator {
  text = "";
  private instances: Record<string, ComponentInstance> = {};
  private order: string[] = [];

  addText(s: string) {
    this.text += s;
  }

  addComponent(inst: ComponentInstance) {
    const id = String(inst?.instance_id || `c${this.order.length}`);
    if (!(id in this.instances)) this.order.push(id);
    this.instances[id] = inst;
  }

  hasComponents() {
    return this.order.length > 0;
  }

  /** Ordered parts for rendering. `streaming` hides a half-arrived marker and withholds any
      component whose marker hasn't streamed in yet (it appears the moment the marker does, in
      its proper place). Pass `finalText` to render the reconciled final text instead of the
      live token buffer. */
  parts(opts?: { streaming?: boolean; finalText?: string }): Part[] {
    const text = opts?.finalText ?? this.text;
    return assembleParts(text, this.instances, this.order, !!opts?.streaming);
  }

  /** Reconcile the streamed token buffer with the run's authoritative final answer. Prefer the
      streamed text (it spans every turn, in order, and carries the markers); fall back to - or
      append - the final answer only when it adds something the stream didn't (a non-LLM node's
      output, or an error message). */
  resolveText(finalAnswer?: string): string {
    const buf = this.text;
    const fa = (finalAnswer || "").trim();
    if (!fa) return buf;
    if (!buf.trim()) return finalAnswer || "";
    if (buf.includes(fa)) return buf;
    return `${buf}\n\n${finalAnswer}`;
  }
}
