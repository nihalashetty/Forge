## LangChain - Advanced Agent and UI Topics

Sources:

- https://docs.langchain.com/oss/python/langchain/context-engineering
- https://docs.langchain.com/oss/python/langchain/guardrails
- https://docs.langchain.com/oss/python/langchain/human-in-the-loop
- https://docs.langchain.com/oss/python/langchain/event-streaming
- https://docs.langchain.com/oss/python/langchain/streaming
- https://docs.langchain.com/oss/python/langchain/multi-agent
- https://docs.langchain.com/oss/python/langchain/frontend/overview
- https://docs.langchain.com/oss/python/langchain/frontend/integrations/overview

Use this reference when the task is about reliability, middleware policy, frontend integration, or multi-agent application structure rather than only model/tool primitives.

### Context engineering

Context engineering is controlling what the model sees, what tools can access, and what happens between agent-loop steps.

Primary context surfaces:

- Model context: system prompt, messages, available tools, model choice, and response format for a model call.
- Tool context: what tools can read or write through `ToolRuntime`, including state, store, context, config, and stream writer.
- Lifecycle context: middleware behavior before/after agent, before/after model, and around tool calls.

Data sources:

- `runtime.context`: static per-run data such as user ID, permissions, credentials, connections, or feature flags.
- `state`: conversation-scoped short-term memory and graph state.
- `store`: cross-thread long-term memory.

Use middleware when the context visible to the model should change dynamically. Use tools directly when only tool execution needs context.

### Guardrails

Guardrails should run at explicit lifecycle boundaries:

- before the agent starts: authentication, rate limits, blocked requests
- before model calls: sanitize or reshape messages/tools
- after model calls: validate or rewrite model output
- around tool calls: approve, block, retry, or recover sensitive operations

LangChain supports deterministic guardrails, model-based guardrails, built-in PII middleware, HITL middleware, and custom middleware.

`PIIMiddleware` supports built-in PII types such as `email`, `credit_card`, `ip`, `mac_address`, and `url`, plus custom detectors. Strategies include:

- `redact`
- `mask`
- `hash`
- `block`

Configuration knobs include `apply_to_input`, `apply_to_output`, and `apply_to_tool_results`. With `apply_to_output=True`, current LangChain versions can redact streamed wire output via middleware stream transformers, so raw PII does not leak through live `stream_events(version="v3")` consumers.

### Human-in-the-loop middleware

`HumanInTheLoopMiddleware` pauses proposed tool calls based on `interrupt_on`.

Decision types:

- `approve`: execute the proposed tool call as-is.
- `edit`: execute the tool call with modified arguments.
- `reject`: deny the tool call and add rejection feedback.
- `respond`: skip the tool and return a human-provided tool result. Use this for ask-user tools, not to deny side-effecting actions.

Configuration surface:

- `interrupt_on`: mapping of tool names to `True`, `False`, or `InterruptOnConfig`.
- `allowed_decisions`: list of allowed decisions per tool.
- `description`: static or callable action description.
- `description_prefix`: default text for action requests.
- `when`: predicate that receives a `ToolCallRequest` and conditionally interrupts; current docs require `langchain>=1.3.3`.

HITL requires a checkpointer and stable `thread_id`. With `version="v2"`, `invoke` returns a `GraphOutput` with `.value` and `.interrupts`. Resume with `Command(resume={"decisions": [...]})` in the same thread.

### Event streaming

For application and frontend use, prefer `stream_events(..., version="v3")` on agents. It returns a run object with typed projections rather than requiring tuple parsing.

Useful projections:

- raw event iteration: full protocol events
- `stream.messages`: one stream per model call
- `message.text`: text deltas/final text
- `message.reasoning`: reasoning blocks when supported by the model
- `message.tool_calls`: model-emitted tool-call argument chunks/final calls
- `stream.tool_calls`: tool execution lifecycle, output, and errors
- `stream.values`: state snapshots
- `stream.output`: final state
- `stream.subagents`: named nested `create_agent` runs
- `stream.subgraphs`: plain nested graph runs
- `stream.extensions`: custom transformer projections

Use `astream_events` with concurrent consumers in async code. Use `stream.interleave(...)` for synchronous concurrent projection consumption.

Middleware can register stream transformers. `create_agent` merges transformers in this order: built-in tool-call transformer, middleware transformers, caller-supplied transformers.

### LangChain multi-agent patterns

Official LangChain multi-agent docs cover:

- subagents called as tools
- handoffs where control passes to another agent/user-facing role
- router agents that choose specialists
- skills-style specialists
- custom multi-agent workflows

Use `create_agent(name=...)` for subagents that should appear clearly in event streams. For graph-level orchestration, prefer LangGraph supervisor/swarm patterns or explicit `StateGraph`.

Pattern choice:

- single assistant with tools: simplest path for most applications
- subagents as tools: isolated specialists, central coordination
- handoffs: user-facing agent role changes
- router: classify request then dispatch to a specialist
- LangGraph multi-agent: explicit routing, durable state, and more inspectable control flow

### Frontend integration

The LangChain frontend docs target `@langchain/react` and related client helpers. Many advanced frontend patterns require LangGraph Agent Server, which can be local through `langgraph dev` or hosted through LangSmith Deployment.

Covered frontend patterns:

- `useStream`: submit input, read messages, stream state, and keep a `threadId`.
- Human-in-the-loop UI: render interrupt action requests and resume with decisions.
- Tool calling UI: display tool-call progress and results.
- Structured output UI: extract structured tool-call arguments from the final or partially streamed AI message and render custom components.
- Reasoning tokens: render reasoning content blocks separately from final text when the model exposes them.
- Markdown messages: render rich text safely.
- Branching chat: fork from checkpoint metadata to edit messages or regenerate responses.
- Time travel: inspect checkpoints and resume from a selected checkpoint.
- Join/rejoin streams: call `stream.disconnect()` to detach without canceling the server run, then rejoin with the same `threadId`.
- Message queues: queue submissions for an active thread, cancel entries, or clear pending work.
- Headless tools: register a server-side tool schema that interrupts, implement the browser-only behavior on the client, then resume with the tool result.
- Generative UI: use a small component catalog and structured specs rather than arbitrary UI code.

Do not treat Agent Server-only frontend features as plain OSS library capabilities unless the user is running `langgraph dev`, LangGraph Platform, or LangSmith Deployment.

### Frontend integrations

`useStream` is UI-library agnostic. It exposes messages, values, loading state, tool calls, thread metadata, and submit/cancel methods that can be wired into multiple React UI layers.

Official integration pages:

- AI Elements: shadcn/ui-style source components for chat UIs. Wire `stream.messages` into `Conversation`, `Message`, `Tool`, `Reasoning`, and `PromptInput`. Use `HumanMessage.isInstance` and `AIMessage.isInstance` for safe narrowing.
- assistant-ui: headless/full-runtime React chat framework. Bridge LangChain `useStream` through `useExternalStoreRuntime`, convert LangChain messages into `ThreadMessageLike`, and wrap the UI in `AssistantRuntimeProvider`.
- CopilotKit: full chat runtime plus structured generative UI. Add `CopilotKitMiddleware` to `create_agent` or `create_deep_agent`, optionally expose a FastAPI/AG-UI endpoint beside a LangGraph deployment, and use `CopilotKitState` when CopilotKit state must live in graph state.
- OpenUI: generative UI library for data-rich dashboards/reports. The agent returns `openui-lang` component trees that a renderer turns into React UI.

Choose the integration by UI ownership model:

- custom chat components: direct `useStream`
- shadcn-style editable source components: AI Elements
- full headless runtime and thread UI: assistant-ui
- dedicated chat runtime and AG-UI bridge: CopilotKit
- generated dashboards/reports: OpenUI

All integration-specific package names, component APIs, and examples are version-sensitive; verify the integration page before writing exact setup commands.

### LangSmith Deployment and UI docs

The LangChain `deploy` page describes LangSmith managed deployment. In OSS-only answers, mark this as out of scope and instead describe self-managed hosting of compiled graphs/agents behind an application server.

LangChain UI and voice tutorials are application examples. Use them as patterns, not core runtime requirements.

### Install, help, changelog, and navigation pages

Official pages such as install, quickstart, overview, academy, get-help, philosophy, Studio, and changelog are important for orientation but are not stable implementation surfaces. For exact install commands, migration notes, or "latest" feature status, check the official page live before answering.

### Tutorial and example pages

Official LangChain tutorial/example pages include knowledge-base/RAG apps, SQL agents, voice agents, UI examples, and "deep agent from scratch" material. Map them to the core references instead of treating each example as a new API:

- knowledge-base and RAG examples: retrieval, tools, structured output, and `create_agent`
- SQL agent examples: tool calling, guardrails, HITL for write queries, and database-specific safety
- voice agent examples: streaming, frontend/client transport, and model/provider-specific audio support
- deep-agent-from-scratch: LangGraph-style agent loop plus planning/files/subagents patterns
