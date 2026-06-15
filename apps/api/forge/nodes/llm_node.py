"""`llm` node — a single model call, no tool loop (Doc 2 §7).

Reads the conversation from `messages`, optionally prepends the configured prompt
as a system message, and appends the model's reply. Structured output binds the
response_format schema. Template variable rendering over state is intentionally
minimal for now (the prompt is used verbatim as a system message).
"""

from __future__ import annotations

from typing import Any

from forge.engine.context import CompileContext
from forge.engine.models import resolve_model
from forge.engine.registry import NodeSpec, Port, register


def llm_factory(config: dict, ctx: CompileContext):
    model = resolve_model(config["model"], ctx, config.get("model_params"))
    prompt = config.get("prompt")
    rf = (config.get("response_format") or {})
    structured_schema = rf.get("schema") if rf.get("mode") == "structured" else None
    runnable = model.with_structured_output(structured_schema) if structured_schema else model

    async def _node(state: dict) -> dict:
        from langchain_core.messages import SystemMessage

        msgs = list(state.get("messages") or [])
        input_msgs: list[Any] = ([SystemMessage(content=prompt)] if prompt else []) + msgs
        result = await runnable.ainvoke(input_msgs)
        if structured_schema:
            # Structured result is not a message; surface it on a conventional channel.
            return {"structured_response": result}
        return {"messages": [result]}

    return _node


register(
    NodeSpec(
        type="llm",
        schema_id="forge/nodes/llm",
        input_ports=[Port(id="in", io_type="text", direction="in")],
        output_ports=[Port(id="out", io_type="text", direction="out")],
        factory=llm_factory,
        category="model_tools",
        label="LLM",
        description="Single model call",
        summarize=lambda c: [str(c.get("model", "—")), "single call"],
    )
)


def classifier_factory(config: dict, ctx: CompileContext):
    """Classify the latest user message into one (or, with `multi_label`, several) of N
    labels (structured output) and write the result to a state key (default `intent`)
    for a downstream router.

    This is the docs' routing pattern: "use structured output for the routing decision,
    then add_conditional_edges". With `multi_label: true` the node writes a LIST of
    labels — pair it with a router configured `multi: true` to fan out to every matching
    branch in parallel (multi-intent questions). A label outside the configured set (or
    a model failure) falls back to a naive keyword match, else writes nothing — the
    router's default/Else path then handles it.
    """
    labels = [str(label) for label in (config.get("labels") or []) if str(label).strip()]
    output_key = config.get("output_key", "intent")
    instructions = config.get("instructions", "")
    multi = bool(config.get("multi_label", False))
    # Intent classification is high-volume + low-stakes — default to the provider's
    # cheapest model (overridable via config.model) instead of the workflow default.
    model_ref = config.get("model")
    if not model_ref:
        from forge.engine.models import cheap_model_for_credentials

        model_ref = cheap_model_for_credentials(getattr(ctx, "provider_credentials", None))
    model = resolve_model(model_ref, ctx, config.get("model_params"))

    if multi:
        schema = {
            "title": "Classification",
            "type": "object",
            "properties": {
                "labels": {
                    "type": "array",
                    "items": {"type": "string", "enum": labels or ["other"]},
                    "minItems": 1,
                    "description": "EVERY label that applies to the user's message.",
                }
            },
            "required": ["labels"],
        }
    else:
        schema = {
            "title": "Classification",
            "type": "object",
            "properties": {"label": {"type": "string", "enum": labels or ["other"]}},
            "required": ["label"],
        }

    def _text_of(message: Any) -> str:
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return content if isinstance(content, str) else str(content or "")

    def _role_of(message: Any) -> str | None:
        return message.get("role") if isinstance(message, dict) else getattr(message, "type", None)

    def _keyword_fallback(text: str) -> list[str]:
        q = text.lower()
        return [
            label for label in labels
            if label.lower() in q or label.lower().replace("_", " ") in q
        ]

    def _recent_context(msgs: list[Any], max_messages: int = 8, max_chars: int = 4000) -> str:
        lines: list[str] = []
        for m in msgs[-max_messages:]:
            role = _role_of(m) or "message"
            text = _text_of(m).strip()
            if text:
                lines.append(f"{role}: {text}")
        return "\n".join(lines)[-max_chars:]

    async def _node(state: dict) -> dict:
        msgs = state.get("messages") or []
        query = ""
        for m in reversed(msgs):
            role = _role_of(m)
            content = _text_of(m)
            if role in ("human", "user") and content:
                query = content
                break
        if not query or not labels:
            return {}
        from langchain_core.messages import SystemMessage

        task = (
            f"Classify the latest user message into EVERY label that applies (one or more) from: {', '.join(labels)}.\n"
            if multi else
            f"Classify the latest user message into exactly one of these labels: {', '.join(labels)}.\n"
        )
        prompt = (
            task
            + "Use the full conversation context to resolve follow-up or elliptical messages "
            "(for example, 'what about Delhi?' should inherit the prior topic). "
            "Return only the structured label(s).\n"
            + (f"{instructions}\n" if instructions else "")
            + f"Latest user message: {query}"
        )
        chosen: list[str] = []
        try:
            res = await model.with_structured_output(schema).ainvoke([SystemMessage(content=prompt), *list(msgs)])
            if multi:
                raw = res.get("labels") if isinstance(res, dict) else getattr(res, "labels", None)
                chosen = [str(x) for x in (raw or []) if str(x) in labels]
            else:
                label = res.get("label") if isinstance(res, dict) else getattr(res, "label", None)
                chosen = [label] if label in labels else []
        except Exception:  # noqa: BLE001 - offline/fake models can't do structured output
            chosen = []
        if not chosen:
            chosen = _keyword_fallback(query) or _keyword_fallback(_recent_context(list(msgs)))
        if not chosen:
            return {}
        return {output_key: chosen if multi else chosen[0]}

    return _node


register(
    NodeSpec(
        type="classifier",
        schema_id="forge/nodes/classifier",
        input_ports=[Port(id="in", io_type="text", direction="in")],
        output_ports=[Port(id="out", io_type="text", direction="out")],
        factory=classifier_factory,
        category="model_tools",
        label="Classifier",
        description="Intent classification",
        summarize=lambda c: [
            f"→ {c.get('output_key', 'intent')}" + (" · multi" if c.get("multi_label") else ""),
            " · ".join((c.get("labels") or [])[:4]) or "no labels",
        ],
    )
)
