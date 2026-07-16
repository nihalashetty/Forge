"use client";
/* Declarative config forms - FieldSpec lists render as friendly widgets (toggle/number/
   select/model/csv/textarea) instead of raw JSON. Shared by the canvas node inspector
   (workflows.tsx) and the middleware stack (AgentConfig.tsx). A form merges only its own
   keys into the config, so unknown/advanced keys are preserved. */
import { Field, Toggle } from "../primitives";
import { useModels } from "@/lib/models";
import { useEffect, useState } from "react";

export type FieldSpec = {
  key: string;
  label: string;
  widget: "toggle" | "number" | "text" | "textarea" | "select" | "model" | "csv" | "multiselect";
  help?: string;
  placeholder?: string;
  /** Override the "model" widget's empty-value label (default "Project default"). Use when an
   *  empty model doesn't resolve to the project default - e.g. the classifier falls back to the
   *  cheapest model, so showing "Project default" there would misrepresent what actually runs. */
  emptyLabel?: string;
  options?: { value: string; label: string }[];
  /** Pull options at render time from a live source (e.g. the project's KB folders or
   *  Q&A kinds) passed via FieldsForm's `dynamic` prop. Keyed by source name. */
  dynamicOptions?: "kb_folders" | "qa_kinds";
  /** First option for a dynamic select (e.g. "any"/"all"). */
  emptyOption?: { value: string; label: string };
  min?: number;
  max?: number;
  step?: number;
  /** Convert the stored config value to the widget's display value. */
  format?: (v: any) => any;
  /** Convert the widget's raw value to the stored config value. */
  parse?: (raw: any) => any;
};

/** Compact multi-select rendered as toggle chips (used for folder selection). Stores an
 *  array; empty array means "all". When there's nothing to choose yet, shows a plain
 *  message instead of an empty input (any already-stored values stay as removable chips). */
export function MultiSelectChips({ value, options, placeholder, onChange }: { value: any; options: string[]; placeholder?: string; onChange: (items: string[]) => void }) {
  const selected: string[] = Array.isArray(value) ? value.map(String) : (typeof value === "string" && value ? value.split(",").map((s) => s.trim()).filter(Boolean) : []);
  const toggle = (opt: string) => {
    const next = selected.includes(opt) ? selected.filter((s) => s !== opt) : [...selected, opt];
    onChange(next);
  };
  // No live options: don't render an input - just a hint. (Keep any stored values visible
  // as removable chips so they aren't silently lost.)
  const chipBtn = (opt: string, on: boolean) => (
    <button key={opt} type="button" onClick={() => toggle(opt)} className="chip"
      style={{ cursor: "pointer", background: on ? "var(--accent)" : "var(--bg-3)", color: on ? "var(--fg-on-accent)" : "var(--fg-1)", borderColor: on ? "var(--accent)" : "var(--line)" }}>
      {on ? "✓ " : ""}{opt}
    </button>
  );
  if (!options.length) {
    if (!selected.length) {
      return <div className="field-help" style={{ marginTop: 0 }}>{placeholder || "Nothing to choose from yet - manage these on the Knowledge screen."}</div>;
    }
    return <div className="row gap2 wrap">{selected.map((opt) => chipBtn(opt, true))}</div>;
  }
  return (
    <div className="row gap2 wrap">
      {options.map((opt) => {
        const on = selected.includes(opt);
        return (
          <button
            key={opt}
            type="button"
            onClick={() => toggle(opt)}
            className="chip"
            style={{ cursor: "pointer", background: on ? "var(--accent)" : "var(--bg-3)", color: on ? "var(--fg-on-accent)" : "var(--fg-1)", borderColor: on ? "var(--accent)" : "var(--line)" }}
          >
            {on ? "✓ " : ""}{opt}
          </button>
        );
      })}
    </div>
  );
}

export function ModelSelect({ value, onChange, emptyLabel = "Project default" }: { value: string; onChange: (v: string) => void; emptyLabel?: string }) {
  const MODELS = useModels();
  return (
    <select className="select" value={value || ""} onChange={(e) => onChange(e.target.value)}>
      <option value="">{emptyLabel}</option>
      {MODELS.map((m) => <option key={m.id} value={m.id}>{m.name} · {m.provider}</option>)}
      {value && !MODELS.some((m) => m.id === value) && <option value={value}>{value}</option>}
    </select>
  );
}

function CsvInput({ value, placeholder, onChange }: { value: any; placeholder?: string; onChange: (items: string[]) => void }) {
  const display = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  const [draft, setDraft] = useState(display);

  useEffect(() => {
    setDraft(display);
  }, [display]);

  const commit = () => {
    onChange(draft.split(",").map((s) => s.trim()).filter(Boolean));
  };

  return (
    <input
      className="input mono"
      value={draft}
      placeholder={placeholder}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
      }}
    />
  );
}

export function FieldsForm({ specs, config, onPatch, dynamic }: { specs: FieldSpec[]; config: Record<string, any>; onPatch: (patch: Record<string, any>) => void; dynamic?: Record<string, string[]> }) {
  const read = (f: FieldSpec) => {
    const v = config[f.key];
    return f.format ? f.format(v) : v;
  };
  const write = (f: FieldSpec, raw: any) => {
    onPatch({ [f.key]: f.parse ? f.parse(raw) : raw });
  };
  const dynOptions = (f: FieldSpec): string[] => (f.dynamicOptions && dynamic?.[f.dynamicOptions]) || [];

  return (
    <div className="col gap3">
      {specs.map((f) => {
        const v = read(f);
        if (f.widget === "toggle") {
          return (
            <div key={f.key} className="col gap1">
              <label className="row gap2" style={{ cursor: "pointer" }}>
                <Toggle on={!!v} onChange={(on) => write(f, on)} />
                <span className="t-body-sm">{f.label}</span>
              </label>
              {f.help && <div className="field-help">{f.help}</div>}
            </div>
          );
        }
        return (
          <Field key={f.key} label={f.label} help={f.help}>
            {f.widget === "number" ? (
              <input className="input mono" type="number" min={f.min} max={f.max} step={f.step ?? 1}
                value={v ?? ""} placeholder={f.placeholder}
                onChange={(e) => write(f, e.target.value === "" ? undefined : Number(e.target.value))} />
            ) : f.widget === "select" ? (
              (() => {
                // Options come from the static list OR a live source (e.g. Q&A kinds).
                const opts = f.dynamicOptions
                  ? [...(f.emptyOption ? [f.emptyOption] : []), ...dynOptions(f).map((o) => ({ value: o, label: o }))]
                  : (f.options || []);
                // Keep a current value that isn't in the live list selectable (don't lose it).
                const hasV = v == null || v === "" || opts.some((o) => o.value === v);
                return (
                  <select className="select" value={v ?? (opts[0]?.value || "")} onChange={(e) => write(f, e.target.value)}>
                    {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    {!hasV && <option value={v}>{v}</option>}
                  </select>
                );
              })()
            ) : f.widget === "multiselect" ? (
              <MultiSelectChips value={v} options={dynOptions(f)} placeholder={f.placeholder} onChange={(items) => write(f, items)} />
            ) : f.widget === "model" ? (
              <ModelSelect value={v || ""} emptyLabel={f.emptyLabel} onChange={(val) => write(f, val || undefined)} />
            ) : f.widget === "csv" ? (
              <CsvInput value={v} placeholder={f.placeholder} onChange={(items) => write(f, items)} />
            ) : f.widget === "textarea" ? (
              <textarea className="textarea mono" rows={3} value={v ?? ""} placeholder={f.placeholder} onChange={(e) => write(f, e.target.value)} />
            ) : (
              <input className="input mono" value={v ?? ""} placeholder={f.placeholder} onChange={(e) => write(f, e.target.value || undefined)} />
            )}
          </Field>
        );
      })}
    </div>
  );
}

/* ---- Middleware config field specs (mirror forge/engine/middleware_compiler.py) ---- */

const ctxSize = {
  // ContextSize is ["messages", N] in config; surface just N in the UI.
  format: (v: any) => (Array.isArray(v) ? v[1] : v),
  parse: (n: any) => (n == null ? undefined : ["messages", n]),
};

export const MW_FIELDS: Record<string, FieldSpec[]> = {
  summarization: [
    { key: "model", label: "Summarizer model", widget: "model", help: "Model used to write the summary." },
    { key: "trigger", label: "Summarize after N messages", widget: "number", min: 4, step: 1, ...ctxSize },
    { key: "keep", label: "Keep last N messages", widget: "number", min: 1, step: 1, ...ctxSize },
    { key: "summary_prompt", label: "Summary prompt (optional)", widget: "textarea" },
  ],
  human_in_the_loop: [
    {
      key: "interrupt_on", label: "Tools requiring approval", widget: "csv", placeholder: "send_email, delete_record",
      help: "Comma-separated tool names - the run pauses for approval before these execute.",
      format: (v: any) => (v && typeof v === "object" ? Object.keys(v) : []),
      parse: (arr: string[]) => Object.fromEntries((arr || []).map((n) => [n, true])),
    },
  ],
  model_call_limit: [
    { key: "run_limit", label: "Max model calls per run", widget: "number", min: 1 },
    { key: "thread_limit", label: "Max model calls per thread", widget: "number", min: 1 },
    { key: "exit_behavior", label: "When the limit hits", widget: "select", options: [{ value: "end", label: "End gracefully" }, { value: "error", label: "Raise an error" }] },
  ],
  tool_call_limit: [
    { key: "tool_name", label: "Tool (empty = all tools)", widget: "text", placeholder: "web_fetch" },
    { key: "run_limit", label: "Max calls per run", widget: "number", min: 1 },
    { key: "thread_limit", label: "Max calls per thread", widget: "number", min: 1 },
    { key: "exit_behavior", label: "When the limit hits", widget: "select", options: [{ value: "continue", label: "Continue without the tool" }, { value: "end", label: "End gracefully" }, { value: "error", label: "Raise an error" }] },
  ],
  model_fallback: [
    { key: "models", label: "Fallback models (in order)", widget: "csv", placeholder: "openai:gpt-4o-mini, anthropic:claude-sonnet-4-6", help: "Tried left to right when the primary model errors." },
  ],
  pii: [
    { key: "pii_type", label: "PII type", widget: "select", options: ["email", "credit_card", "ip", "mac_address", "url"].map((v) => ({ value: v, label: v })) },
    { key: "strategy", label: "Strategy", widget: "select", options: ["redact", "mask", "hash", "block"].map((v) => ({ value: v, label: v })) },
    { key: "apply_to_input", label: "Apply to user input", widget: "toggle" },
    { key: "apply_to_output", label: "Apply to model output", widget: "toggle" },
    { key: "apply_to_tool_results", label: "Apply to tool results", widget: "toggle" },
  ],
  todo: [
    { key: "system_prompt", label: "Planning prompt (optional)", widget: "textarea" },
    { key: "tool_description", label: "write_todos tool description (optional)", widget: "textarea" },
  ],
  llm_tool_selector: [
    { key: "model", label: "Selector model", widget: "model" },
    { key: "max_tools", label: "Max tools exposed per call", widget: "number", min: 1 },
    { key: "always_include", label: "Always include tools", widget: "csv", placeholder: "lookup_kb" },
  ],
  tool_retry: [
    { key: "max_retries", label: "Max retries", widget: "number", min: 1 },
    { key: "tools", label: "Only these tools (empty = all)", widget: "csv" },
    { key: "backoff_factor", label: "Backoff factor", widget: "number", step: 0.1 },
    { key: "initial_delay", label: "Initial delay (s)", widget: "number", step: 0.1 },
    { key: "max_delay", label: "Max delay (s)", widget: "number", step: 0.5 },
    { key: "jitter", label: "Add jitter", widget: "toggle" },
  ],
  model_retry: [
    { key: "max_retries", label: "Max retries", widget: "number", min: 1 },
    { key: "backoff_factor", label: "Backoff factor", widget: "number", step: 0.1 },
    { key: "initial_delay", label: "Initial delay (s)", widget: "number", step: 0.1 },
    { key: "max_delay", label: "Max delay (s)", widget: "number", step: 0.5 },
    { key: "jitter", label: "Add jitter", widget: "toggle" },
  ],
  tool_emulator: [
    { key: "model", label: "Emulator model", widget: "model" },
    { key: "tools", label: "Tools to emulate (empty = all)", widget: "csv", help: "Emulated tools return LLM-invented results - for testing flows without live APIs." },
  ],
  context_editing: [
    {
      key: "edits", label: "Clear old tool results after N tokens", widget: "number", min: 1000, step: 1000,
      help: "When the context exceeds this, older tool outputs are cleared.",
      format: (v: any) => (Array.isArray(v) && v[0] ? v[0].trigger : undefined),
      parse: (n: any) => (n == null ? undefined : [{ trigger: n }]),
    },
  ],
  anthropic_prompt_caching: [
    { key: "ttl", label: "Cache TTL", widget: "select", options: [{ value: "5m", label: "5 minutes" }, { value: "1h", label: "1 hour" }] },
  ],
  openai_moderation: [
    { key: "apply_to_input", label: "Moderate user input", widget: "toggle" },
    { key: "apply_to_output", label: "Moderate model output", widget: "toggle" },
  ],
  guardrail_regex: [
    { key: "patterns", label: "Blocked patterns (regex)", widget: "csv", placeholder: "(?i)password, secret_key" },
    { key: "on_match", label: "On match", widget: "select", options: [{ value: "block", label: "Block the reply" }, { value: "flag", label: "Flag only" }] },
  ],
  tenant_budget: [
    { key: "max_tokens_per_run", label: "Max tokens per run", widget: "number", min: 100, step: 100 },
    { key: "on_exceed", label: "When exceeded", widget: "select", options: [{ value: "end", label: "End gracefully" }, { value: "error", label: "Raise an error" }] },
  ],
};
