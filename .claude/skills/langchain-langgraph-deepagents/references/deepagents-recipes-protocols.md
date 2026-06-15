## Deep Agents - Recipes, Protocols, Code Agent, and Production Topics

Sources:

- https://docs.langchain.com/oss/python/deepagents/context-engineering
- https://docs.langchain.com/oss/python/deepagents/customization
- https://docs.langchain.com/oss/python/deepagents/models
- https://docs.langchain.com/oss/python/deepagents/mcp
- https://docs.langchain.com/oss/python/deepagents/acp
- https://docs.langchain.com/oss/python/deepagents/a2a
- https://docs.langchain.com/oss/python/deepagents/code/overview
- https://docs.langchain.com/oss/python/deepagents/frontend/overview
- https://docs.langchain.com/oss/python/deepagents/going-to-production
- https://docs.langchain.com/oss/python/deepagents/rubric
- https://docs.langchain.com/oss/python/deepagents/comparison
- https://docs.langchain.com/oss/python/deepagents/content-builder
- https://docs.langchain.com/oss/python/deepagents/data-analysis
- https://docs.langchain.com/oss/python/deepagents/deep-research

Use this reference when a task goes beyond SDK primitives into production use, code-agent behavior, protocols, recipes, frontend integration, or current Deep Agents documentation coverage.

### `create_deep_agent` configuration surface

Important parameters:

- `model`: provider string or initialized chat model
- `system_prompt`: custom instructions prepended to the built-in prompt
- `tools`: domain tools
- `memory`: AGENTS.md-style files loaded at startup
- `skills`: skill folders loaded on demand through frontmatter relevance
- `backend`: filesystem/storage backend
- `permissions`: filesystem/tool permission rules
- `subagents`: synchronous, compiled, or async subagents
- `middleware`: extra middleware appended to the default harness stack
- `interrupt_on`: HITL policies for tool calls
- `response_format`: structured output schema
- `state_schema`: custom graph state
- `profiles`: reusable per-model/provider defaults

Prefer `create_deep_agent` when the task needs planning, file tools, subagents, skills, persistent memory, sandboxing, or long-horizon context management.

### Deep Agents context engineering

Deep Agents context sources:

- input context: system prompt, memory, skills, and tool prompts
- runtime context: per-run user metadata, credentials, feature flags, database/API handles
- context compression: offload/summarize older content as context limits approach
- context isolation: delegate heavy work to subagents and return only useful results
- long-term memory: persistent AGENTS.md or virtual filesystem-backed memory across threads

The assembled system prompt can include:

- custom `system_prompt`
- base deep agent prompt
- to-do planning prompt
- memory prompt
- skills prompt and skill frontmatter
- virtual filesystem prompt
- subagent prompt
- custom middleware prompts
- HITL prompt
- local context prompt in CLI/code-agent environments

Use runtime context for immutable per-run values. Use state when data must be checkpointed, mutable, or visible through `runtime.state`.

### Custom state schema

Current docs state custom Deep Agents state schemas require `deepagents>=0.6.6`.

Custom schemas must subclass `DeepAgentState` to preserve built-in message behavior, including the `DeltaChannel` reducer on messages.

Use `state_schema` when custom fields should be part of mutable graph state, persisted with checkpoints, or visible to tools through `ToolRuntime.state`.

### Model providers

Deep Agents accept:

- `provider:model` strings
- `init_chat_model(...)` model instances
- provider-specific chat model classes

Official docs cover OpenAI, Anthropic, Azure OpenAI, Google Gemini/GenAI, AWS Bedrock, HuggingFace, and "other" providers through LangChain model integrations. Provider package installation and exact model IDs are version-sensitive; verify current provider docs before prescribing an exact string.

### MCP tools

Deep Agents can consume MCP tools through `langchain-mcp-adapters`.

Use MCP when the agent needs external tool servers, databases, APIs, browser/file servers, or OAuth-backed tools. Pass loaded MCP tools into `create_deep_agent(tools=[...])`.

Check adapter docs for:

- stdio/SSE/HTTP transports
- OAuth authentication
- tool filtering
- stateful sessions
- error handling

### ACP and A2A

ACP exposes Deep Agents to editors/IDEs over Agent Client Protocol.

Current ACP path:

- install `deepagents-acp`
- wrap a deep agent in `AgentServerACP`
- run it over stdio with an ACP-compatible client

ACP is for editor/client integration. MCP is for tools the agent calls.

The current Python A2A page exists but has no substantive implementation details in the docs snapshot. Do not invent A2A setup steps; verify the page before advising.

### Deep Agents Code (`dcode`)

Deep Agents Code is an open-source terminal coding agent built on the SDK.

Important capabilities:

- provider/model switching
- persistent memory per agent
- project and user skills
- file read/write/edit tools
- shell execution locally or in remote sandboxes
- Tavily web search and URL fetch
- to-do planning
- subagents and async subagents
- conversation compaction/offloading
- HITL approval controls
- MCP tool loading
- optional JavaScript interpreter middleware
- LangSmith tracing when configured

Built-in tools include:

- `ls`
- `read_file`
- `write_file`
- `edit_file`
- `glob`
- `grep`
- `execute`
- `web_search`
- `fetch_url`
- `task`
- `ask_user`
- `compact_conversation`
- `write_todos`

Safety/configuration notes:

- write/edit/execute/web/fetch/task and compaction actions may be approval-gated.
- `-y` / `--auto-approve` disables approval prompts.
- non-interactive mode disables shell unless `--shell-allow-list` or `DEEPAGENTS_CODE_SHELL_ALLOW_LIST` is set.
- `--shell-allow-list recommended` uses read-only safe defaults; `all` is broad.
- `--sandbox` supports remote sandbox providers where configured.
- `--acp` runs as an ACP server over stdio.
- Deep Agents Code is not officially supported on Windows in the docs snapshot; Windows users are pointed to WSL.

Config locations:

- `~/.deepagents/config.toml`: model/agent defaults, providers, constructor params, profiles, theme/update settings
- `~/.deepagents/.env`: global secrets
- `~/.deepagents/hooks.json`: lifecycle hooks
- `~/.deepagents/<agent_name>/`: per-agent memory, skills, threads
- `.deepagents/` in a project: project-specific memory and skills

### Deep Agents Code docs coverage

Official code-agent docs also cover:

- configuration schema and `config.toml`
- provider credentials and on-demand provider installs
- remote sandboxes
- data locations
- MCP tool loading and trust prompts
- memory and skills
- subagent configuration

For exact CLI flags or config schema, verify the current Deep Agents Code docs; the CLI surface is actively changing.

### Frontend patterns

Deep Agents frontend docs cover:

- overview of integrating long-running deep agents into UIs
- sandbox interaction for code/file execution results
- subagent streaming
- to-do list rendering/progress

Use the LangChain/LangGraph frontend references for generic `useStream`, HITL, queues, branching, and join/rejoin behavior.

### Recipes and tutorials

Official Deep Agents recipes/tutorials include:

- content builder: memory, skills, research subagent, web search, image generation, filesystem backend
- data analysis: analysis workflow with tools/files/interpreter-style execution where applicable
- deep research: research-oriented subagents, source gathering, synthesis, and long-horizon state

Treat these as patterns for composing capabilities, not mandatory architecture for every deep agent.

The comparison page is conceptual. Use it when deciding whether the task belongs in LangChain agents, raw LangGraph orchestration, or the Deep Agents harness.

### Production guidance

Before production:

- choose persistent backends/checkpointers/stores
- configure sandbox isolation for code execution
- tighten permissions and approval policies
- add observability/tracing or your own equivalent
- test recovery from interrupts, retries, and long-running tasks
- define resource limits for tools and subagents
- verify provider limits, timeouts, and retry behavior
- evaluate behavior with a rubric or task-specific tests

The rubric docs are for quality evaluation and acceptance criteria. Use them when defining pass/fail behavior for agents, recipes, or production readiness reviews.
