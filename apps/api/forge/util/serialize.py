"""JSON-safe serialization of LangGraph stream chunks and state (messages, etc.)."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from langchain_core.messages import BaseMessage

# LLM tool name -> human-readable label for the run currently being streamed. Set by the
# run stream from CompileContext.tool_display_names so `jsonable` can relabel tool_calls
# for end-user surfaces (the model still calls the tool by its underscore identifier).
# Empty by default => every tool_call's display_name falls back to its own name.
_TOOL_DISPLAY_NAMES: ContextVar[dict[str, str]] = ContextVar("forge_tool_display_names", default={})


def set_tool_display_names(mapping: dict[str, str] | None) -> Token:
    """Bind the current run's name->label map; returns a token to reset() when the run ends."""
    return _TOOL_DISPLAY_NAMES.set(mapping or {})


def reset_tool_display_names(token: Token) -> None:
    _TOOL_DISPLAY_NAMES.reset(token)


def content_to_text(content: Any) -> str:
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", b.get("content", "")))
            else:
                parts.append(str(b))
        return "".join(str(p) for p in parts)
    return content if isinstance(content, str) else ("" if content is None else str(content))


def _interrupt_to_json(obj: Any) -> dict[str, Any] | None:
    """LangGraph Interrupts carry the human-facing payload on `.value`.

    If they fall through to `str(obj)`, the Playground cannot read the custom
    prompt/allowed decisions and has to show its default approval text.
    """
    if obj.__class__.__name__ != "Interrupt" or not hasattr(obj, "value"):
        return None
    out: dict[str, Any] = {"value": jsonable(obj.value)}
    for attr in ("id", "ns", "resumable", "when"):
        if hasattr(obj, attr):
            value = getattr(obj, attr)
            if value is not None:
                out[attr] = jsonable(value)
    return out


def jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseMessage):
        tool_calls = getattr(obj, "tool_calls", None) or None
        names = _TOOL_DISPLAY_NAMES.get()
        return {
            "type": obj.type,
            "content": content_to_text(obj.content),
            "name": getattr(obj, "name", None),
            "tool_calls": [
                # `name` stays the model-facing identifier; `display_name` is the human label
                # (relabeled per the run's map, else the identifier itself).
                {
                    "name": tc.get("name"),
                    "display_name": names.get(tc.get("name"), tc.get("name")),
                    "args": tc.get("args"),
                }
                for tc in tool_calls
            ]
            if tool_calls
            else None,
        }
    interrupt = _interrupt_to_json(obj)
    if interrupt is not None:
        return interrupt
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def serialize_stream(mode: str, chunk: Any) -> Any:
    """Serialize one (mode, chunk) item from graph.astream(stream_mode=[...])."""
    if mode == "messages":
        if isinstance(chunk, (list, tuple)) and len(chunk) == 2:
            msg, meta = chunk
        else:
            msg, meta = chunk, {}
        return {
            "content": content_to_text(getattr(msg, "content", "")),
            "type": getattr(msg, "type", None),
            "node": (meta or {}).get("langgraph_node"),
        }
    return jsonable(chunk)
