## 2. LangChain - Models

Use `init_chat_model("provider:model")` for provider-model strings. Common parameters include `temperature`, `max_tokens`, `timeout`, `max_retries`, and provider-specific kwargs.

```python
from langchain.chat_models import init_chat_model

model = init_chat_model(
    "openai:gpt-5.4",
    temperature=0,
    timeout=30,
    max_retries=2,
)
```

Important current model features:

- `model.with_structured_output(Schema)` supports Pydantic models, TypedDicts, and JSON schema.
- `model.bind_tools(tools)` binds tools to a chat model.
- Model profiles expose capability metadata such as context window, tool calling, and structured output support. Middleware such as summarization and structured output routing can use this.
- `init_chat_model` can accept a `profile` override, e.g. `{"max_input_tokens": 100_000}`.
- Standard content blocks are used for multimodal inputs and streaming.
- Use `InMemoryRateLimiter` for simple in-process rate limiting.
- For OpenAI model strings in Deep Agents, current docs note the Responses API is the default path for `openai:` models.

Model names in docs can be placeholders or current-generation examples. Always verify exact provider model strings at build time.

## 3. LangChain - Messages

Core message types include:

- `HumanMessage`
- `AIMessage`
- `SystemMessage`
- `ToolMessage`
- `AIMessageChunk`

Current patterns:

- Use standard content blocks for text, image, audio, video, tool-call, reasoning, and usage content where supported by the provider.
- `SystemMessage` can be passed directly to `create_agent(system_prompt=...)`, including structured content blocks and provider-specific cache controls.
- LangChain `>=1.1` added model profile data and content-block-centric streaming patterns used by later event-streaming APIs.

## 4. LangChain - Tools

Define tools with:

- `@tool`
- `@tool(parse_docstring=True)`
- `StructuredTool`
- Plain callables passed to `create_agent`, where schemas are inferred from signatures and docstrings.

Use `ToolRuntime` for runtime access. It replaces older injected-state/store patterns such as `InjectedState`, `InjectedStore`, `get_runtime`, and `InjectedToolCallId`.

`ToolRuntime` exposes:

- `state`: mutable graph state.
- `context`: immutable run context.
- `store`: long-term store.
- `stream_writer`: writer for custom stream events.
- `execution_info`: thread/run/attempt metadata.
- `server_info`: server/user identity metadata where available.
- `config`.
- `tool_call_id`.

Reserved tool argument names: `config` and `runtime`.

```python
from langchain.tools import ToolRuntime, tool
from langchain.messages import ToolMessage
from langgraph.types import Command

@tool(parse_docstring=True)
def lookup(query: str, runtime: ToolRuntime) -> str:
    """Look something up.

    Args:
        query: Search query.
    """
    user_id = runtime.context.user_id
    return f"results for {query} for {user_id}"

@tool
def record_status(status: str, runtime: ToolRuntime) -> Command:
    """Record status and update agent state."""
    return Command(
        update={
            "messages": [
                ToolMessage(
                    content=f"Status recorded: {status}",
                    tool_call_id=runtime.tool_call_id,
                )
            ],
            "status": status,
        }
    )
```

Current tool features to remember:

- Tools can return `Command` to update state and route.
- Tools can return content plus artifact.
- `BaseTool.extras` supports provider-specific tool parameters and definitions, including provider built-in tools and native client-side capabilities.
- `ToolNode` has tool error handling, but for `create_agent` the modern pattern is usually middleware with `wrap_tool_call`.

## 5. LangChain - `create_agent` and Middleware

Core signature shape:

```python
from langchain.agents import create_agent

agent = create_agent(
    model,
    tools=[],
    system_prompt=None,
    middleware=(),
    response_format=None,
    state_schema=None,
    context_schema=None,
    checkpointer=None,
    store=None,
    name=None,
)
```

Invoke with `{"messages": [...]}`. When using a checkpointer, include a `thread_id`:

```python
from langchain.agents import create_agent
from langchain_core.utils.uuid import uuid7
from langgraph.checkpoint.memory import InMemorySaver

agent = create_agent(
    "anthropic:claude-sonnet-4-6",
    tools=[lookup],
    system_prompt="Be concise.",
    checkpointer=InMemorySaver(),
)

config = {"configurable": {"thread_id": str(uuid7())}}
result = agent.invoke(
    {"messages": [{"role": "user", "content": "hi"}]},
    config=config,
)
```

`response_format=PydanticModel` returns `result["structured_response"]`. Current structured-output strategies include provider-native structured output (`ProviderStrategy`) and tool-based structured output (`ToolStrategy`). Strict schema adherence is supported where provider/model profiles allow it.

### Middleware hooks

Middleware is the main customization surface. Current hook coverage must include:

| Hook | When it runs | Typical use |
| --- | --- | --- |
| `before_agent` / `abefore_agent` | Before agent execution starts | Load memory, validate input, initialize state. |
| `before_model` / `abefore_model` | Before each model call | Trim messages, update prompt, inject state. |
| `wrap_model_call` / `awrap_model_call` | Around each model call | Dynamic model selection, dynamic tools, modify request/response. |
| `wrap_tool_call` / `awrap_tool_call` | Around each tool call | Tool retries, logging, policy, error conversion. |
| `after_model` / `aafter_model` | After each model response | Guardrails, validation, extra state update. |
| `after_agent` / `aafter_agent` | After agent completion | Save results, cleanup, telemetry. |

Class-based middleware can declare:

- `state_schema`: extend agent state.
- `tools`: add middleware-owned tools.
- `transformers`: add stream transformer factories.

```python
from typing import Any, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langgraph.runtime import Runtime

class LoggingMiddleware(AgentMiddleware):
    def before_model(
        self,
        state: AgentState,
        runtime: Runtime,
    ) -> dict[str, Any] | None:
        print(f"About to call model with {len(state['messages'])} messages")
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(request)
```

Decorator middleware is also supported:

```python
from typing import Callable
from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call

@wrap_model_call
def route_model(
    request: ModelRequest,
    handler: Callable[[ModelRequest], ModelResponse],
) -> ModelResponse:
    if request.runtime.context.user_tier == "premium":
        request = request.override(model="openai:gpt-5.4")
    return handler(request)
```

Do not mutate middleware instance attributes during runs unless you fully understand concurrency implications. Use graph state for per-thread counters or accumulators.

### Built-in and prebuilt middleware

Current built-in/prebuilt middleware areas:

- `SummarizationMiddleware`: trigger/keep logic based on tokens, messages, fractions, and model profiles.
- `HumanInTheLoopMiddleware`: interrupts before configured tool calls.
- Tool-call and model-call limits.
- `ModelFallbackMiddleware`.
- Model retry middleware and tool retry middleware.
- PII detection/redaction middleware, including `apply_to_output=True` where needed.
- To-do list middleware (`write_todos`).
- LLM tool selector.
- `LLMToolEmulator` for tests and simulation.
- Context editing middleware.
- File search and filesystem middleware.
- Subagent middleware.
- Shell tool middleware.
- Provider-specific middleware such as Anthropic prompt caching, AWS prompt caching, OpenAI content moderation, and provider built-in tool support.

Summarization trigger semantics:

- A single `TriggerClause` dict such as `{"tokens": 4000, "messages": 10}` requires all thresholds to be met.
- A list of trigger clauses acts as OR.
- Fraction-based triggers require model profile context-window data.

### Shell tool middleware

`ShellToolMiddleware` exposes a persistent shell session to an agent. It is powerful and security-sensitive.

Key options:

- `workspace_root`
- `startup_commands`
- `shutdown_commands`
- `execution_policy`
- `redaction_rules`
- `tool_description`
- `shell_command`
- `env`

Execution policies:

- `HostExecutionPolicy`: full host access; only for trusted environments already isolated by container/VM.
- `DockerExecutionPolicy`: launches an isolated Docker container per run.
- `CodexSandboxExecutionPolicy`: reuses Codex CLI sandbox restrictions.

Limitation: persistent shell sessions do not currently work with interrupts.

## 6. LangChain - Prompts, Output Parsers, and RAG

Prompts:

- `ChatPromptTemplate`
- few-shot templates
- example selectors
- `SystemMessage` for rich prompt blocks and cache controls

Output parsers still exist, but `with_structured_output`, `ProviderStrategy`, and `ToolStrategy` are preferred for most structured output.

Retrieval/RAG:

- document loaders
- embeddings
- vector stores
- retrievers
- indexing APIs
- `langchain-text-splitters`

Recommended generic splitter:

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)
```

Other splitters:

- `CharacterTextSplitter`
- `TokenTextSplitter` / `.from_tiktoken_encoder`
- `RecursiveCharacterTextSplitter.from_language(Language.X)`
- `MarkdownHeaderTextSplitter`
- `HTMLHeaderTextSplitter`
- `HTMLSemanticPreservingSplitter`
- `RecursiveJsonSplitter`
- `SemanticChunker` for embedding-based semantic boundaries

Use recursive character splitting by default; switch to code/markdown/HTML/JSON-aware splitters for structured sources.

## 7. LangChain - LCEL and Runnables

Runnable interface:

- `invoke`
- `ainvoke`
- `batch`
- `stream`
- `astream`

Composition:

- `RunnableSequence` via `|`
- `RunnableParallel`
- `RunnablePassthrough`
- `RunnableLambda`
- `RunnableBranch`

Configuration:

- `.bind()`
- `.with_config()`
- `.with_retry()`
- `.with_fallbacks()`
- configurable fields

Use LCEL for deterministic data-processing chains such as retrieval pipelines and transforms. Use `create_agent` or LangGraph for agentic loops.

## 8. LangChain - Memory, Callbacks, and Caching

Legacy memory classes such as `ConversationBufferMemory` are deprecated for new work. Use:

- LangGraph checkpointers for short-term, per-thread memory.
- LangGraph stores for long-term, cross-thread memory.

Open-source observability hooks:

- `BaseCallbackHandler`
- `config={"callbacks": [handler]}`
- OpenTelemetry-compatible callbacks and instrumentations

LLM caching uses `BaseCache`.

## 9. LangChain MCP Integration (`langchain-mcp-adapters==0.3.0`)

`langchain-mcp-adapters` converts MCP tools into LangChain tools usable with `create_agent`, `create_react_agent`, and raw LangGraph `ToolNode`.

```python
from langchain.agents import create_agent
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient(
    {
        "math": {
            "transport": "stdio",
            "command": "python",
            "args": ["/absolute/path/math_server.py"],
        },
        "weather": {
            "transport": "http",
            "url": "http://localhost:8000/mcp",
            "headers": {"Authorization": "Bearer TOKEN"},
        },
    }
)

tools = await client.get_tools()
agent = create_agent("openai:gpt-5.4", tools)
```

Current MCP details:

- Transports include `stdio`, `http`, `streamable_http`, and `sse` where supported.
- HTTP transports support runtime headers.
- `load_mcp_tools(session)` supports explicit session management.
- `MultiServerMCPClient.get_tools()` creates a fresh session per tool invocation by default.
- Use `client.session("server_name")` when persistent explicit MCP sessions are required.
- `handle_tool_errors=True` is the default: MCP execution errors (`CallToolResult(isError=True)`) are returned as `ToolMessage(status="error")` so the model can self-correct.
- Set `handle_tool_errors=False` to raise `ToolException` for MCP execution errors.
- Transport/session failures and content-conversion errors still raise regardless of `handle_tool_errors`.
- MCP content blocks map to LangChain standard content blocks and `ToolMessage.artifact` where appropriate.
- Avoid `stdio` MCP servers in web-server contexts unless there is a strong reason; use HTTP/streamable HTTP for remote or server-side deployment.

