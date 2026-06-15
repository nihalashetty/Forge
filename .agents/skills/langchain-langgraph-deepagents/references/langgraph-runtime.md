## 10. LangGraph - Core

Use `StateGraph` for explicit graph orchestration:

```python
from typing_extensions import TypedDict
from langgraph.graph import END, START, StateGraph

class State(TypedDict):
    text: str

def add_a(state: State) -> dict:
    return {"text": state["text"] + "a"}

builder = StateGraph(State)
builder.add_node("add_a", add_a)
builder.add_edge(START, "add_a")
builder.add_edge("add_a", END)
graph = builder.compile()

graph.invoke({"text": ""})
```

Important APIs:

- `StateGraph(StateSchema, context_schema=, input_schema=, output_schema=)`
- `add_node`
- `add_edge`
- `add_conditional_edges`
- `START` / `END`
- `.compile(checkpointer=, store=, interrupt_before=, interrupt_after=)`

Nodes can take `state`, `config`, or use `Runtime` for store, stream writer, execution info, and context.

For conceptual LangGraph design guidance, read `references/langgraph-design-patterns.md`. It covers the official `Thinking in LangGraph` and `Workflows and agents` topics: node decomposition, state design, workflow-vs-agent selection, prompt chaining, parallelization, routing, orchestrator-worker, evaluator-optimizer, agent loops, and `ToolNode`.

## 11. LangGraph - State, Reducers, and Channels

State schema options:

- `TypedDict`: common default, low overhead.
- Pydantic model: runtime validation.
- dataclass: structured typed data.

Reducers:

- `Annotated[type, reducer]`.
- `add_messages` merges messages and deduplicates by message ID.
- `operator.add` concatenates sequences.
- `Overwrite` bypasses a reducer.
- `MessagesState` is a prebuilt single-key message state.

Multiple schemas are supported:

- input schema
- output schema
- private/internal state

Nodes return partial dictionaries; LangGraph merges returned keys.

### DeltaChannel (LangGraph `>=1.2`)

`DeltaChannel` is a beta channel for append-heavy or growing state. Instead of serializing the full accumulated value into every checkpoint, it stores only incremental deltas and optional snapshots.

Use it for channels that grow over long threads, such as message history or accumulated files.

```python
from typing import Annotated, Sequence
from typing_extensions import TypedDict
from langgraph.channels import DeltaChannel

def append_reducer(state: list[str], writes: Sequence[list[str]]) -> list[str]:
    result = list(state)
    for write in writes:
        result.extend(write)
    return result

class State(TypedDict):
    messages: Annotated[list[str], DeltaChannel(append_reducer)]
```

Use `snapshot_frequency=K` where available to trade read latency for storage size.

## 12. LangGraph - Control Flow

Use `Command` when a node or tool must update state and route in one return value:

```python
from langgraph.types import Command

def route(state):
    return Command(update={"status": "ok"}, goto="next_node")
```

Use `Command(goto=..., graph=Command.PARENT)` for parent graph handoffs.

Use `Send("node", state)` from conditional edges for map-reduce/fan-out parallelization.

Use `recursion_limit` in config to guard loops.

## 13. LangGraph - Persistence and Checkpointing

Checkpointers conform to `BaseCheckpointSaver` and save graph state at supersteps.

Common checkpointer packages:

- `langgraph-checkpoint`: base interfaces plus in-memory saver.
- `langgraph-checkpoint-sqlite`: `SqliteSaver`, `AsyncSqliteSaver`.
- `langgraph-checkpoint-postgres`: `PostgresSaver`, `AsyncPostgresSaver`.
- `langchain-azure-cosmosdb`: Azure Cosmos DB checkpointers.

```python
from langgraph.checkpoint.postgres import PostgresSaver

with PostgresSaver.from_conn_string("postgresql://user:pass@host/db") as cp:
    cp.setup()  # first run only
    graph = builder.compile(checkpointer=cp)
```

When passing a manual Postgres connection, use `autocommit=True` and `row_factory=dict_row`.

Thread identity:

```python
config = {"configurable": {"thread_id": "thread-1"}}
```

Checkpoint capabilities:

- short-term memory
- resume
- human-in-the-loop
- time travel
- replay/fork via `checkpoint_id`
- pending-writes recovery
- inspection with `get_state`
- mutation with `update_state`

Default serializer: `JsonPlusSerializer` using `ormsgpack`. Set `LANGGRAPH_STRICT_MSGPACK=true` where strict safe deserialization is required.

### Durability modes

Current durability modes:

- `exit`: persist only when execution exits successfully, errors, or interrupts. Best performance; weakest crash recovery.
- `async`: persist asynchronously while the next step executes. Good default performance/durability tradeoff.
- `sync`: persist synchronously before the next step. Highest durability; more overhead.

```python
for chunk in graph.stream(input_data, config=config, durability="sync"):
    ...
```

## 14. LangGraph - Store and Long-Term Memory

The `BaseStore` interface supports:

- `put`
- `get`
- `delete`
- `search`
- `list_namespaces`
- async equivalents

Use stores for cross-thread memory. Use namespaces such as `(user_id, "memories")`.

Production stores include `PostgresStore`, `MongoDBStore`, and `RedisStore` where available. `InMemoryStore` is for development/testing.

Semantic search:

```python
from langgraph.store.memory import InMemoryStore

store = InMemoryStore(
    index={
        "embed": embeddings,
        "dims": 1536,
        "fields": ["text"],
    }
)
```

Search behavior:

- `store.search(namespace, query=..., filter=..., limit=...)`.
- Searching with no query/filter lists items in a namespace prefix.
- Ordering differs by backend; sort client-side if order matters.

## 15. LangGraph - Human in the Loop

Dynamic interrupts:

```python
from langgraph.types import Command, interrupt

def approval_node(state):
    decision = interrupt({"question": "Approve this action?"})
    return {"decision": decision}

graph.invoke(Command(resume="approved"), config=config)
```

Static interrupts:

- `interrupt_before`
- `interrupt_after`
- use `"*"` to break on every node

Rules:

- Requires a checkpointer.
- Code before `interrupt()` re-runs on resume; keep it idempotent.
- Multiple parallel interrupts can be resumed by mapping interrupt IDs to values.
- Use interrupts for irreversible, sensitive, or high-blast-radius actions.

## 16. LangGraph - Streaming

### Classic stream modes

`graph.stream()` / `graph.astream()` support:

- `values`: full state snapshots.
- `updates`: per-node state deltas.
- `messages`: LLM tokens and metadata.
- `custom`: user-defined events via stream writer.
- `checkpoints`: checkpoint envelopes.
- `tasks`: task lifecycle events.
- `debug`: verbose debug events.

Multiple modes can be combined with `stream_mode=[...]`.

Use `subgraphs=True` to include nested graph streams.

### Recommended v2 stream output (`langgraph>=1.1`)

Pass `version="v2"` to receive unified `StreamPart` dictionaries:

```python
for chunk in graph.stream(
    input_data,
    stream_mode=["updates", "messages", "custom"],
    subgraphs=True,
    version="v2",
):
    print(chunk["type"])  # "updates", "messages", or "custom"
    print(chunk["ns"])    # namespace tuple
    print(chunk["data"])  # typed payload
```

Every `StreamPart` has:

- `type`
- `ns`
- `data`

`invoke(..., version="v2")` returns a `GraphOutput` object with:

- `.value`
- `.interrupts`

Pydantic/dataclass output coercion is supported for `version="v2"`.

### Event streaming v3 (`langgraph>=1.2`, `langchain>=1.3`, Deep Agents `>=0.6`)

Use `stream_events(..., version="v3")` / `astream_events(..., version="v3")` for content-block-centric event streaming with typed projections.

Important channels:

- `values`: full graph state snapshots.
- `updates`: per-node state deltas.
- `messages`: content-block-centric chat model output.
- `tools`: tool start/output/finish/error.
- `lifecycle`: run, subgraph, and subagent status.
- `checkpoints`: lightweight checkpoint envelopes.
- `input`: HITL input requests and responses.
- `tasks`: Pregel task lifecycle.
- `custom`: user payloads.
- `custom:<name>`: app-defined stream transformer output.

Typed projections include:

- `run.messages`
- `run.values`
- `run.lifecycle`
- `run.subgraphs`
- `run.interrupts` where supported

`run.messages` yields typed sub-projections for text, reasoning, tool calls, and usage.

Tag a model with `tags=["nostream"]` to exclude its tokens from message streaming.

## 17. LangGraph - Fault Tolerance

Fault tolerance mechanisms:

- Retries: `RetryPolicy`.
- Caching: `CachePolicy`.
- Timeouts: `timeout=` or `TimeoutPolicy`.
- Error handlers: `error_handler=`.
- Graph defaults: `set_node_defaults`.

Current LangGraph `>=1.2` features:

- Per-node timeouts are Python-only. Use `timeout=` on `add_node` or `TimeoutPolicy(run_timeout=..., idle_timeout=...)`.
- Timeout raises `NodeTimeoutError`, clears writes from that attempt, and hands off to retry policy.
- Node-level `error_handler=` runs after retries are exhausted. It receives `NodeError` and can return `Command`.
- Graceful shutdown uses `RunControl.request_drain()` from another thread. The run raises `GraphDrained` at a superstep boundary and can later resume from checkpoint.

Retries run before error handlers.

## 18. LangGraph - Subgraphs, Functional API, and Durability

Subgraphs:

- Compile a graph and add it as a node.
- Share a key such as `messages`, or transform state at boundaries.
- Interrupts bubble to the parent.

Functional API:

- `@entrypoint(checkpointer=, store=, cache=, retry_policy=, cache_policy=)`
- `@task(retry_policy=, cache_policy=)`
- `previous` accesses prior return on the same thread.
- `entrypoint.final` separates returned value from checkpointed value.
- Tasks return futures and can run in parallel.

Use Functional API when Python control flow is clearer. Use Graph API when graph structure, node-level time travel, and per-node checkpointing matter.

## 19. LangGraph - Multi-Agent

Patterns:

- Supervisor (`langgraph-supervisor`, `create_supervisor`): central router delegates with handoff tools. Start here for debuggability and accuracy.
- Swarm (`langgraph-swarm`, `create_swarm`, `create_handoff_tool`): agents hand off directly and remember the active agent. Use when routing is reliable and latency matters.
- Network/hierarchical: compose supervisors and swarms.

Handoff tools return `Command(goto=..., graph=Command.PARENT)`. Include relevant messages in `Command.update` so the target agent sees valid history.

Always use a checkpointer for multi-agent work and add recursion/handoff guards.

## 20. LangGraph - Prebuilt and Deployment

Prebuilt components:

- `create_react_agent`
- `ToolNode`
- `tools_condition`
- `ValidationNode`
- Agent Inbox interrupt schemas

OSS deployment pattern:

- Run compiled graphs in your own app, e.g. FastAPI plus `astream` or `stream_events`.
- Use self-managed checkpointers/stores.
- Expose SSE/WebSocket/application streams yourself.

Out of OSS-only scope:

- LangGraph Platform.
- Hosted Agent Server.
- LangGraph Studio.
- LangSmith Deployment.

`langgraph-api` and `langgraph-cli[inmem]` can be useful for local dev, but verify license and deployment implications before production use.

