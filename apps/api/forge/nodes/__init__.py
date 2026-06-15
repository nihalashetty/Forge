"""Built-in node factories. Importing this package registers every node type.

Registered: start, end, router, agent, deep_agent, llm, classifier, transform,
human_input, handoff, webhook_out, emit_event, tool_call, retrieval, qa_lookup,
subworkflow, parallel_fanout, join, loop, and the triggers (webhook_in, schedule,
email_in, chat_in).
"""

from forge.nodes import (  # noqa: F401  (import => register)
    agent_node,
    data,
    flow,
    llm_node,
    rag,
    triggers,
)


def load_builtin_nodes() -> None:
    """No-op: importing `forge.nodes` already registered the built-ins."""
    return None
