---
name: forge-platform
description: Use when designing, building, debugging, or explaining Forge workflows, nodes, state, routing, middleware, tools, or knowledge — especially for custom/complex workflow shapes beyond the canned builders.
---

# Forge platform deep guide

You are embedded in Forge, a visual builder for LangChain/LangGraph agents. Workflows are
JSON definitions compiled to LangGraph StateGraphs. This guide covers the rules the canned
builder tools don't teach. For the live catalog always call `list_node_types`,
`get_node_schema(type)`, and `list_middleware_types` — they read the real registry.

## Workflow definition shape

```json
{
  "state": {"messages": {"type": "list[message]", "reducer": "add_messages"},
             "intent": {"type": "str", "reducer": "last"}},
  "entry_node": "start",
  "nodes": [{"id": "start", "type": "start", "config": {}, "position": {"x": 40, "y": 200}}],
  "edges": [{"source": "start", "target": "..."}]
}
```

Rules:
- Exactly one `start` node; at least one `end` node; every node reachable; a path must reach end.
- Every state key a node WRITES must be declared in `state` (LangGraph rejects undeclared
  writes). `create_custom_workflow` auto-declares keys for known node configs, but declare
  custom `output_key`s yourself. Types: str, int, float, bool, json, list[str], list[json],
  list[message]. Reducers: last (overwrite), add (append lists), merge (dict merge),
  add_messages (chat history).
- Messages flow on the `messages` channel; nodes append, never replace.

## Routing patterns

- Triage first (almost every support/chatbot graph): right after `start`, classify the
  message into at least `general` vs `support`, then `router` it. Send `general` (greetings,
  smalltalk, "what can you do?", capability/meta questions) to a small friendly agent that
  answers directly and goes to `end`; route only `support` into the retrieval/ticket
  pipeline. Without this, greetings and meta questions fall through retrieval, miss,
  and dead-end at a "no relevant data → create a ticket" path — a bad first impression.
  Shape: start → classify(general|support) → router → {general: greeter_agent → end,
  support: retrieval → … → end}. (Simpler alt: one front agent with a knowledge_search tool
  that both chats and answers.)
- Single intent: `classifier` (labels, output_key=intent) → `router`
  (expression=intent, cases {label: node_id}, default=fallback_node). Case KEYS are the
  exact VALUES the expression takes, not display labels.
- ALWAYS give routers a `default` — with no default, an unmatched value silently ends the
  run with no answer.
- MULTI-INTENT (a question with several asks): classifier `multi_label: true` writes a
  LIST to state (declare it `list[str]`); router `multi: true` routes to EVERY matching
  case in parallel. All branches then converge on ONE synthesizer agent node before end —
  its prompt: "compose the partial answers above into one coherent reply". Without a
  synthesizer the user sees only the last branch's answer.
- Simpler multi-intent alternative (preferred for support bots): ONE agent with
  `config.knowledge` enabled (rag and/or qa) plus any REST tools. The agent searches the KB
  once per sub-question and composes one answer itself. Fewer nodes, no fan-out needed.
- Conditional on retrieval success: retrieval `route_key` writes "yes"/"no";
  human decisions: human_input `output_key` writes the decision string.

## Knowledge

- Sources (documents) live in folders (free-form names; "" = unfiled). retrieval node
  `folders: ["Manuals"]` and the `knowledge_search` tool's `folder` arg filter by folder.
- Q&A pairs have a free-form `kind` (faq, error_workaround, or custom kinds the user
  creates) + tags. The retrieval node (include_qa) and agent Q&A filter by `kinds`; empty = all.
- Three ways to ground an agent, pick by how much control you need:
  1. `retrieval` NODE = fixed pre-step grounding (one search over BOTH docs + Q&A per run,
     before the agent; structurally guaranteed). Use when grounding MUST happen.
  2. Agent `config.knowledge` (PREFERRED for conversational/multi-part agents) = built-in,
     agent-driven KB access, no separate Tool needed:
     ```json
     "knowledge": {
       "rag": {"enabled": true, "folders": ["Manuals"], "top_k": 4},
       "qa":  {"enabled": true, "kinds": ["faq"]}
     }
     ```
     Compiles to `search_knowledge_base` (documents) and/or `lookup_faq` (curated Q&A),
     each toggled and scoped (folders / kinds) independently. The agent searches per
     sub-question in its own phrasing — so ONE agent answers multi-part questions.
  3. `knowledge_search` builtin TOOL = same idea but as a standalone Tool row (use when you
     want to share one tool across agents, or filter folder per-call). For a single agent,
     `config.knowledge` is simpler.

## Human-in-the-loop (real interrupts only)

- `human_input` node pauses the run (LangGraph interrupt) until a human decides in the
  Playground. `output_key` exposes the decision to a router.
- HumanInTheLoopMiddleware (`approve_tools` on builders / `human_in_the_loop` middleware)
  pauses before specific TOOL calls.
- NEVER simulate approval via prompt text. Verify with test_workflow that nodes_visited
  ends in `__interrupt__`.

## Middleware (agent nodes)

Per-agent `middleware: [{type, config, enabled}]`. Useful types: summarization,
model_fallback, model_retry, tool_retry (retry_on: timeout/connection/http_error/...),
pii, guardrail_regex (block actually replaces the reply), model_call_limit,
tool_call_limit, tenant_budget, llm_tool_selector, context_editing, tool_emulator,
dynamic_model_by_state, tool_filter_by_context, human_in_the_loop. Call
`list_middleware_types` for configs.

## Build discipline

1. write_todos the plan. 2. list_resources (reuse, never duplicate names).
3. Build (canned builder if it fits, else create_custom_workflow). 4. test_workflow with
a realistic question, a greeting, an off-topic question — and every branch/intent.
5. evaluate_build to judge the results against the user's actual request. 6. Fix and
re-test until the judge passes. Only then report success.
