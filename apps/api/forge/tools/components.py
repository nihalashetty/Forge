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
import uuid
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from forge.tools.rest import build_args_schema_from_jsonschema


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
        # (e.g. tool tests) — then we just ack.
        sw = getattr(runtime, "stream_writer", None)
        if sw:
            try:
                sw({
                    "channel": "component",
                    "payload": {
                        "component_id": cid,
                        "name": name,
                        "version": version,
                        "instance_id": uuid.uuid4().hex,
                        "props": props,
                        "actions": actions,
                    },
                })
            except Exception:  # noqa: BLE001 - no active stream writer; degrade to ack-only
                pass
        return (
            f"The '{name}' UI component is now displayed to the user with those props. "
            "Reply with at most a brief lead-in; do not restate the component's contents."
        )

    return StructuredTool.from_function(
        coroutine=_call, name=name, description=description, args_schema=args_schema
    )
