## LangGraph - Application, Operations, and Frontend Topics

Sources:

- https://docs.langchain.com/oss/python/langgraph/choosing-apis
- https://docs.langchain.com/oss/python/langgraph/application-structure
- https://docs.langchain.com/oss/python/langgraph/local-server
- https://docs.langchain.com/oss/python/langgraph/deploy
- https://docs.langchain.com/oss/python/langgraph/test
- https://docs.langchain.com/oss/python/langgraph/event-streaming
- https://docs.langchain.com/oss/python/langgraph/frontend/overview
- https://docs.langchain.com/oss/python/langgraph/frontend/graph-execution
- https://docs.langchain.com/oss/python/langgraph/backward-compatibility

Use this reference for application packaging, local development, testing, Agent Server, and frontend/runtime integration decisions.

### Choosing Graph API vs Functional API

Use Graph API when you need:

- explicit shared state across nodes
- complex branching or multiple decision points
- parallel paths that merge later
- graph visualization for debugging and documentation
- team collaboration around a visible workflow
- node-level time travel and per-node checkpoint boundaries

Use Functional API when you want:

- minimal changes to existing procedural code
- standard Python control flow with `if`, loops, and function calls
- rapid prototyping with less boilerplate
- simple linear workflows
- function-scoped state

Both APIs share the same runtime and can be mixed in one app.

### Application structure

A deployable LangGraph app usually contains:

- graph construction code
- node/tool/state modules
- dependency file such as `requirements.txt` or `pyproject.toml`
- optional `.env`
- `langgraph.json`

Typical Python structure:

```text
my-app/
  my_agent/
    utils/
      tools.py
      nodes.py
      state.py
    agent.py
  .env
  pyproject.toml or requirements.txt
  langgraph.json
```

`langgraph.json` declares:

- `dependencies`: packages or local package paths
- `graphs`: graph names mapped to `./path/file.py:variable_or_factory`
- `env`: environment file path for local/deployed app configuration
- `dockerfile_lines`: extra system dependencies where supported

Example:

```json
{
  "dependencies": ["langchain_openai", "./my_agent"],
  "graphs": {
    "agent": "./my_agent/agent.py:agent"
  },
  "env": "./.env"
}
```

### Local server

`langgraph-cli[inmem]` provides a local development Agent Server.

Key behavior:

- Python 3.11+ is required by current docs.
- `langgraph new` can create a starter project.
- `langgraph dev` starts a local API server and Studio URL.
- The in-memory local server is for development/testing.
- Production needs persistent storage and a production deployment path.
- `--tunnel` can help browsers that cannot connect cleanly to localhost.

Agent Server and Studio are useful for testing threads, interrupts, streaming, and frontend features, but hosted Studio/Deployment are not OSS-only runtime requirements.

### Agent Server SDK

The LangGraph SDK can call local or hosted Agent Server instances.

Core concepts:

- assistant ID: graph name from `langgraph.json`
- threadless run: run without creating/reusing a thread
- thread ID: persistent conversation/run state
- run streaming: stream updates/events from a run

Use SDK/server APIs when building a remote app or frontend. Use direct compiled graph calls when embedding graphs inside your own Python service.

### Event streaming

LangGraph supports classic `stream` modes and app-facing `stream_events`.

Use `stream_events(..., version="v3")` for frontend/event consumers when available. It exposes typed channels for messages, tools, lifecycle, checkpoints, interrupts, input, tasks, custom data, and final output.

Use `stream_mode` with `graph.stream`/`graph.astream` for direct library use when you only need values, updates, messages, or custom chunks.

### Frontend graph execution

Frontend graph execution patterns depend on threads and server-side run state:

- submit input to a thread
- stream messages/values/tool progress
- interrupt for HITL
- resume with `Command`
- cancel, disconnect, or rejoin runs depending on app behavior
- inspect checkpoints for time travel/branching where Agent Server is available

Keep a stable `threadId` in the frontend for persistence, resume, join/rejoin, queues, and branching.

### Testing

Test at multiple levels:

- pure node function tests: pass state dictionaries and assert partial updates
- routing tests: assert branch labels or `Command(goto=...)`
- graph integration tests: compile with `InMemorySaver`, invoke/stream, assert final state
- HITL tests: include `thread_id`, assert interrupts, resume with `Command`
- streaming tests: assert stream mode/event shapes rather than exact token text
- server tests: use LangGraph SDK only when testing Agent Server behavior

Keep side-effecting tools behind fakes or test-specific implementations.

### Pregel execution model

LangGraph execution is Pregel-inspired:

- nodes activate when they receive messages/state through channels
- work advances in supersteps
- parallel nodes can run in the same superstep
- execution halts when all nodes are inactive and no messages are in transit

This matters for reducers, parallel branches, checkpoint boundaries, and understanding why loops need recursion limits.

### Deployment and OSS-only caveats

Official deployment docs focus on LangSmith Deployment and LangGraph Platform. For OSS-only projects:

- run compiled graphs in your own process or service
- manage checkpointers/stores yourself
- expose HTTP, SSE, or WebSocket streaming yourself
- use your own auth, tenancy, logs, metrics, and scaling controls

Do not present LangSmith Deployment, Studio, hosted Agent Server, or Platform-only features as available in a pure OSS library deployment.

### Backward compatibility

Treat LangGraph APIs as version-sensitive. Before migration or exact compatibility claims, verify:

- installed `langgraph` version
- `langchain-core` and `langchain` versions
- stream protocol version support
- checkpoint/store package versions
- prebuilt package versions
- platform/server versus library-only environment

### Tutorial and example pages

Official LangGraph tutorial/example pages include quickstart, add memory, agentic RAG, SQL agent, use Graph API, use Functional API, use subgraphs, use time travel, and case studies. Map them to the core references:

- quickstart: `create_react_agent`, tools, messages, and basic invocation
- add memory: checkpointers for thread memory and stores for cross-thread memory
- agentic RAG: routing between query generation, retrieval, and answer generation
- SQL agent: tool calling, database safety, HITL for writes, and error recovery
- use Graph API: `StateGraph`, state, nodes, edges, reducers, and streaming
- use Functional API: `@entrypoint`, `@task`, futures, checkpointing, and Python control flow
- use subgraphs: nested graphs, shared/transformed state, and interrupt bubbling
- use time travel: checkpoint inspection, replay, fork, and `checkpoint_id`
- case studies: production examples, not separate API contracts
