## 21. Deep Agents - Overview

`create_deep_agent` returns a compiled LangGraph `CompiledStateGraph`.

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[],
    system_prompt="You are a helpful assistant.",
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "hi"}]},
)
reply = result["messages"][-1].content
```

Full current signature shape:

```python
create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ResponseFormat | type | dict[str, Any] | None = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
)
```

Pass an explicit model in real systems. The docs still show `model=None`, but explicit model selection avoids environment-dependent defaults and makes provider behavior reproducible.

## 22. Deep Agents - State and Harness Capabilities

The core built-in state includes:

- `messages`
- `files`
- `todos`
- async task tracking when async subagents are configured
- interpreter snapshot state when `CodeInterpreterMiddleware` is configured

Custom state schemas require `deepagents>=0.6.6` and must subclass `DeepAgentState`:

```python
from deepagents import DeepAgentState, create_deep_agent

class ResearchState(DeepAgentState):
    project_id: str
```

Subclassing `DeepAgentState` preserves Deep Agents' built-in `DeltaChannel` reducer on messages, keeping checkpoint growth manageable.

Harness capabilities:

- Execution environment: tools, virtual filesystem, optional sandbox, optional interpreter.
- Context management: skills, memory, summarization, context offloading, prompt caching.
- Delegation: planning and subagents.
- Steering: human-in-the-loop approval and interrupts.

## 23. Deep Agents - Built-In Tools

Common built-ins:

- `write_todos`: plan and track task state (`pending`, `in_progress`, `completed`).
- Filesystem: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`.
- `execute`: available with sandbox backends and `LocalShellBackend`.
- `task`: spawn synchronous subagents.
- Async subagent tools when configured: `start_async_task`, `check_async_task`, `update_async_task`, `cancel_async_task`, `list_async_tasks`.
- Interpreter tool when configured: default `eval` or custom `tool_name`.

`read_file` supports multimodal content blocks for supported images, video, audio, and document files. Current docs list support including common image formats, video formats, audio formats, PDF, PPT, and PPTX.

## 24. Deep Agents - Backends

Backends define the agent filesystem surface used by `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, and sometimes `execute`.

### StateBackend

Default backend. Stores files in LangGraph state for the current thread.

Properties:

- Files persist across turns within a thread via the checkpointer.
- Files are not shared across threads.
- Good for scratch files, intermediate notes, and offloaded tool results.
- Shared between supervisor and subagents.

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend

agent = create_deep_agent(
    model="openai:gpt-5.4",
    backend=StateBackend(),
)
```

### StoreBackend

Stores files in a LangGraph `BaseStore` for durable cross-thread storage.

```python
from deepagents.backends import StoreBackend

backend = StoreBackend(
    namespace=lambda runtime: (runtime.context.user_id, "files")
)
```

Use for memories, shared instructions, and persistent files.

### CompositeBackend

Routes path prefixes to different backends.

```python
from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend

backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/workspace/": FilesystemBackend(root_dir="/path/to/project", virtual_mode=True),
        "/memories/": StoreBackend(namespace=lambda rt: (rt.context.user_id, "memories")),
    },
)
```

Recommended when using real disk, because Deep Agents write internal files such as `/large_tool_results/` and `/conversation_history/`. Keep internals in `StateBackend` and route only project paths to disk.

### FilesystemBackend

Reads/writes real local files under `root_dir`.

Security facts:

- Grants direct filesystem read/write access.
- Use only for local development, trusted CLI workflows, or CI with proper isolation.
- Avoid for web servers, HTTP APIs, multi-tenant systems, or untrusted user input.
- File modifications are permanent.
- Agents may read secrets if those paths are accessible.
- With network tools, secrets may be exfiltrated.
- Always set `virtual_mode=True` when using `root_dir`.
- Default `virtual_mode=False` provides no security even with `root_dir`.

```python
from deepagents.backends import FilesystemBackend

backend = FilesystemBackend(root_dir="/path/to/project", virtual_mode=True)
```

### LocalShellBackend

Adds host shell execution through `execute`.

Security facts:

- Direct host filesystem and unrestricted shell execution.
- No isolation.
- `virtual_mode=True` does not secure shell access because shell commands can access the host outside the virtual path.
- Use only in controlled development environments.
- Strongly pair with human-in-the-loop approval.

### Sandbox backends

Sandbox backends expose filesystem tools plus `execute` inside an isolated environment.

Supported/current provider families include:

- Modal (`langchain-modal`)
- Daytona (`langchain-daytona`)
- Runloop (`langchain-runloop`)
- Deno/local VFS options where supported
- LangSmith managed sandboxes (outside OSS-only scope)

Use sandboxes for production agents that need shell execution, package installs, tests, or arbitrary code execution.

### ContextHubBackend

Stores files in LangSmith Hub and gives version history. It is current Deep Agents functionality but is outside an OSS-only recommendation because it depends on LangSmith Hub.

## 25. Deep Agents - Permissions

Permissions require `deepagents>=0.5.2`. `mode="interrupt"` requires `deepagents>=0.6.8` and a checkpointer.

Permissions apply only to built-in filesystem tools:

- `ls`
- `read_file`
- `glob`
- `grep`
- `write_file`
- `edit_file`

They do not apply to:

- custom tools
- MCP tools
- sandbox `execute`
- shell commands through `LocalShellBackend`

Rule structure:

```python
from deepagents import FilesystemPermission

FilesystemPermission(
    operations=["read", "write"],
    paths=["/workspace/**"],
    mode="allow",
)
```

Fields:

- `operations`: `["read"]`, `["write"]`, or both.
- `paths`: glob patterns; supports `**` and alternation.
- `mode`: `"allow"`, `"deny"`, or `"interrupt"`.

Semantics:

- Rules are evaluated in declaration order.
- First matching rule wins.
- If no rule matches, the operation is allowed.
- Put specific denies before broad allows.

Restrictive workspace example:

```python
from deepagents import FilesystemPermission, create_deep_agent

agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/workspace/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/**"],
            mode="deny",
        ),
    ],
)
```

Interrupt example:

```python
from deepagents import FilesystemPermission, create_deep_agent
from langgraph.checkpoint.memory import InMemorySaver

agent = create_deep_agent(
    model=model,
    backend=backend,
    permissions=[
        FilesystemPermission(
            operations=["write"],
            paths=["/secrets/**"],
            mode="interrupt",
        ),
    ],
    checkpointer=InMemorySaver(),
)
```

Subagents inherit parent permissions by default. A subagent `permissions` field replaces the parent's rules entirely.

With a `CompositeBackend` whose default route is a sandbox, permission paths should be scoped under known non-sandbox route prefixes. Path rules alone cannot stop shell access through `execute`.

## 26. Deep Agents - Synchronous Subagents

Declarative `SubAgent` fields:

- `name`
- `description`
- `system_prompt`
- optional `tools`
- optional `model`
- optional `middleware`
- optional `interrupt_on`
- optional `skills`
- optional `response_format`
- optional `permissions`

Compiled subagents:

```python
from deepagents import CompiledSubAgent

subagent = CompiledSubAgent(
    name="researcher",
    description="Researches a topic and returns findings.",
    runnable=compiled_graph,
)
```

Compiled subagent runnables must expose a `messages` state key.

Default general-purpose subagent:

- Deep Agents automatically adds a synchronous `general-purpose` subagent unless you provide one with that name.
- It has filesystem tools by default.
- To replace it, pass a subagent named `general-purpose`.
- To rename, re-prompt, or disable the auto-added version, configure `GeneralPurposeSubagentProfile` through a `HarnessProfile`.
- To run without the `task` tool, disable the general-purpose subagent in the active harness profile and pass no synchronous subagents.

## 27. Deep Agents - Async Subagents (`deepagents>=0.5.0`, preview)

Async subagents launch background tasks that return immediately. The supervisor can keep interacting with the user while subagents run.

Use async subagents for:

- long-running work
- parallelizable tasks
- tasks needing mid-flight updates
- cancellable background jobs

`AsyncSubAgent` fields:

- `name`: unique identifier.
- `description`: routing description.
- `graph_id`: graph/assistant ID on the Agent Protocol server.
- `url`: optional remote Agent Protocol URL. Omitted means ASGI/in-process transport.
- `headers`: optional HTTP headers for remote/self-hosted auth.

```python
from deepagents import AsyncSubAgent, create_deep_agent

async_subagents = [
    AsyncSubAgent(
        name="researcher",
        description="Researches topics requiring multiple searches and synthesis.",
        graph_id="researcher",
    ),
    AsyncSubAgent(
        name="coder",
        description="Generates and reviews code.",
        graph_id="coder",
        url="https://coder.example.com",
        headers={"Authorization": "Bearer TOKEN"},
    ),
]

agent = create_deep_agent(
    model="openai:gpt-5.4",
    subagents=async_subagents,
)
```

Tools added by `AsyncSubAgentMiddleware`:

- `start_async_task`: start a background task and return a task ID immediately.
- `check_async_task`: get current status and result if complete.
- `update_async_task`: send follow-up instructions to a running task.
- `cancel_async_task`: stop a running task.
- `list_async_tasks`: list tracked tasks and statuses.

Lifecycle facts:

- Launch creates a new thread on the server and starts a run.
- Task ID is the subagent thread ID.
- Task metadata is stored in a dedicated `async_tasks` state channel.
- Update interrupts/restarts the subagent run with the same task ID and full history plus new instructions.
- Terminal statuses include success, error, and cancelled.
- In local `langgraph dev`, size the worker pool for supervisor plus concurrent subagents, e.g. `--n-jobs-per-worker 10`.
- Avoid immediate polling after launch; return control to the user.
- Always check or list live task state before reporting status because conversation history can be stale.

ASGI transport is preferred when supervisor and subagents are co-deployed. HTTP transport is for independently scaled or separately maintained subagents.

## 28. Deep Agents - Middleware Stack

Main-agent default stack order:

1. `TodoListMiddleware`
2. `SkillsMiddleware` if `skills` is provided
3. `FilesystemMiddleware`, including permission enforcement when `permissions` is provided
4. `SubAgentMiddleware` when at least one synchronous subagent exists
5. `SummarizationMiddleware`
6. `PatchToolCallsMiddleware`
7. `AsyncSubAgentMiddleware` when async subagents are configured
8. caller-provided `middleware`
9. harness profile extra middleware
10. excluded-tool filtering from harness profile
11. `AnthropicPromptCachingMiddleware` (no-op on non-Anthropic)
12. `MemoryMiddleware` if `memory` is provided
13. `HumanInTheLoopMiddleware` if `interrupt_on` or permission interrupts are configured

Synchronous declarative subagents use a similar stack, but:

- skills run after `PatchToolCallsMiddleware`
- there is no nested `SubAgentMiddleware`
- subagent `interrupt_on` is forwarded to `create_agent`

Summarization defaults:

- Uses model profile `max_input_tokens` where available.
- Current docs describe an 85% context trigger and retaining about 10% as recent context.
- Fallback behavior is used when model profile data is unavailable.
- Deep Agents can also expose an on-demand `compact_conversation` tool via `create_summarization_tool_middleware`.
- Summarization can trigger on provider `ContextOverflowError` where supported.

