"""UI component widget-tool (Feature 2 — generative UI).

Materializes a saved Component into a StructuredTool the agent calls like any other
tool. When invoked it does NOT return data to the model — it pushes a `component`
frame onto the run's custom stream (the client renders the saved HTML/CSS template
with these props) and returns a short ack. So the markup never enters the token
stream; only the widget id + props (the tool args, validated against props_schema) do.
This is the MCP-Apps structuredContent/_meta split realized with LangGraph's stream.
"""

# NO `from __future__ import annotations` here — on purpose. LangChain detects the
# injectable `runtime: ToolRuntime` parameter via inspect.signature(fn), which does NOT
# evaluate string annotations; postponed annotations make the runtime arg invisible and
# the stream_writer is lost. (See tools/rest.py for the full rationale.)
import re
import uuid
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from forge.tools.rest import build_args_schema_from_jsonschema

# Inline placeholder the agent copies into its reply to position a rendered component (the
# props/markup travel out-of-band on the custom stream; only this ~10-token marker is in the
# token stream). The web renderer splits the reply on these markers — keep the format in sync
# with apps/web/lib/chat-parts.ts.
COMPONENT_MARKER_RE = re.compile(r"\[\[forge:component:[A-Za-z0-9_-]+\]\]")


def component_marker(instance_id: str) -> str:
    return f"[[forge:component:{instance_id}]]"


def strip_component_markers(text: str) -> str:
    """Remove component markers from a reply for text-only surfaces (email/Teams/webhook/SMS),
    which can't render a widget and would otherwise show the literal `[[forge:component:…]]`.
    Tidies the whitespace/blank lines the removal leaves behind."""
    if not text:
        return text
    out = COMPONENT_MARKER_RE.sub("", text)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def build_component_tool(cfg: dict, ctx) -> Any:
    """cfg: {id, name, description, props_schema, actions, version}."""
    cid = cfg.get("id")
    name = cfg.get("name", "component")
    version = cfg.get("version", 1)
    actions = cfg.get("actions") or []
    description = cfg.get("description") or f"Render the '{name}' UI component for the user."
    props_schema = cfg.get("props_schema") or {}
    args_schema = build_args_schema_from_jsonschema(props_schema, name=f"{name}_props")
    # Real JSON-Schema validation of nested/enum/typed props (the generated Pydantic schema
    # only covers top-level scalars) — built once per materialization (audit F22).
    validator = None
    if isinstance(props_schema, dict) and (props_schema.get("properties") or props_schema.get("required")):
        try:
            from jsonschema import Draft202012Validator

            validator = Draft202012Validator(props_schema)
        except Exception:  # noqa: BLE001 - a bad schema must not block rendering
            validator = None

    async def _call(runtime: ToolRuntime = None, **kwargs):  # type: ignore[assignment]
        # Drop None props so optional fields fall back to the template's own defaults instead
        # of rendering as null (audit L1).
        props = {k: v for k, v in kwargs.items() if v is not None}
        # Validate against props_schema; on failure DON'T render — return a corrective ack so
        # the model retries with fixed props rather than silently showing a wrong widget (F22).
        if validator is not None:
            errs = sorted(validator.iter_errors(props), key=lambda e: list(e.path))
            if errs:
                e = errs[0]
                where = "/".join(str(p) for p in e.path) or "(root)"
                return f"Did not render '{name}': prop '{where}' is invalid — {e.message}. Fix the props and call the tool again."
        # Bound the payload so a huge props object can't bloat the frame / iframe (audit F26).
        import json as _json

        try:
            if len(_json.dumps(props, default=str)) > 200_000:
                return f"Did not render '{name}': the props payload is too large. Send a smaller one."
        except Exception:  # noqa: BLE001
            pass
        # `runtime.stream_writer` pushes onto the LangGraph custom stream; the runs service
        # forwards it to the client as a `component` frame. None when invoked without streaming
        # (e.g. tool tests) — then we just ack without a marker (nothing would render it).
        instance_id = uuid.uuid4().hex
        sw = getattr(runtime, "stream_writer", None)
        emitted = False
        if sw:
            try:
                sw({
                    "channel": "component",
                    "payload": {
                        "component_id": cid,
                        "name": name,
                        "version": version,
                        "instance_id": instance_id,
                        "props": props,
                        "actions": actions,
                    },
                })
                emitted = True
            except Exception:  # noqa: BLE001 - no active stream writer; degrade to ack-only
                emitted = False
        if not emitted:
            return f"The '{name}' component was prepared (no live display surface to render it)."
        # The marker is how the agent CONTROLS ordering: it copies this token into its reply text
        # at the exact spot the widget should appear (the client splices the rendered component
        # there). So the agent can put the widget mid-answer, after a heading, or at the end —
        # wherever it reads naturally — instead of it always landing at the top.
        return (
            f"The '{name}' component is ready. Place the marker {component_marker(instance_id)} in "
            "your reply text at the exact position where it should appear — write your prose before "
            "and after it as needed so the component lands in its natural place. Insert the marker "
            "exactly once and do not restate the component's contents as text."
        )

    return StructuredTool.from_function(
        coroutine=_call, name=name, description=description, args_schema=args_schema
    )
