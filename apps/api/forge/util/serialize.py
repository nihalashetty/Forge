"""JSON-safe serialization of LangGraph stream chunks and state (messages, etc.)."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage


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
        return {
            "type": obj.type,
            "content": content_to_text(obj.content),
            "name": getattr(obj, "name", None),
            "tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args")} for tc in tool_calls
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
