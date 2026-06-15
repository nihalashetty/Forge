# LangChain, LangGraph and Deep Agents - Authoritative Skill Reference (Python, June 10 2026)

## TL;DR

- As of June 10 2026, current stable Python packages are: `langchain==1.3.6`, `langchain-core==1.4.3`, `langgraph==1.2.4`, `langgraph-prebuilt==1.1.0`, `langgraph-checkpoint==4.1.1`, `langgraph-sdk==0.4.2`, and `deepagents==0.6.8`.
- Partner packages named in this reference: `langchain-openai==1.3.0`, `langchain-anthropic==1.4.4`, `langchain-google-genai==4.2.5`, `langchain-ollama==1.1.0`, `langchain-aws==1.5.1`, `langchain-community==0.4.2`, `langchain-classic==1.0.7`, `langchain-text-splitters==1.1.2`, and `langchain-mcp-adapters==0.3.0`.
- The stack layers as `langchain-core -> langchain agents/middleware -> langgraph runtime -> deepagents harness`.
- Use `langchain.agents.create_agent` for most new agent work, raw LangGraph `StateGraph` or the Functional API for custom orchestration, and `deepagents.create_deep_agent` for long-running agents that need planning, files, subagents, skills, memory, permissions, and optional code execution.
- `create_react_agent` in `langgraph.prebuilt` still exists, but `create_agent` is the current LangChain v1 default.
- LangSmith and LangGraph Platform/Studio are excluded by scope. When current docs include LangSmith-backed features, this reference names them for completeness and marks them as outside the OSS-only recommendation.

## Current Version Matrix

| Package | Current version | Notes |
| --- | ---: | --- |
| `langchain` | `1.3.6` | Main agent package; includes `create_agent`, middleware, event streaming v3 support. |
| `langchain-core` | `1.4.3` | Base abstractions: runnables, messages, models, tools, stores, callbacks. |
| `langgraph` | `1.2.4` | Graph runtime, persistence, streaming, interrupts, fault tolerance. |
| `langgraph-prebuilt` | `1.1.0` | Bundled with `langgraph`; do not normally install directly. |
| `langgraph-checkpoint` | `4.1.1` | Base checkpointer interface plus in-memory saver. |
| `langgraph-sdk` | `0.4.2` | SDK for LangGraph API/Agent Protocol servers. |
| `deepagents` | `0.6.8` | Beta harness package; Python `>=3.11`; provides `quickjs` extra. |
| `langchain-openai` | `1.3.0` | OpenAI model integration. |
| `langchain-anthropic` | `1.4.4` | Anthropic model integration. |
| `langchain-google-genai` | `4.2.5` | Google GenAI/Gemini integration. |
| `langchain-ollama` | `1.1.0` | Ollama integration. |
| `langchain-aws` | `1.5.1` | AWS/Bedrock integration. |
| `langchain-community` | `0.4.2` | Being sunset; do not start new systems on it unless required. |
| `langchain-classic` | `1.0.7` | Legacy chains, old memory, old agents, and deprecated APIs. |
| `langchain-text-splitters` | `1.1.2` | Text splitting package used by RAG workflows. |
| `langchain-mcp-adapters` | `0.3.0` | MCP tools to LangChain/LangGraph tool adapters. |

## Key Findings

### The three agent factories

- `langgraph.prebuilt.create_react_agent`: original prebuilt ReAct agent; lower-level and still supported.
- `langchain.agents.create_agent`: current LangChain v1 flagship agent factory; minimal harness built on LangGraph; middleware is the main customization point.
- `deepagents.create_deep_agent`: opinionated harness layered over `create_agent`; adds planning, virtual filesystem, subagents, skills, memory, profiles, permissions, and optional interpreter/sandbox execution.

### Open-source-only scope

Use local/self-hosted runtime pieces when avoiding LangSmith and LangGraph Platform:

- Local compiled graphs in your own service.
- Checkpointers and stores such as in-memory, SQLite, Postgres, Redis/MongoDB stores where available.
- Callbacks and OpenTelemetry-compatible observability such as Langfuse.

Avoid or explicitly mark out-of-scope:

- LangSmith tracing/deployment/Engine.
- LangGraph Platform, Studio, hosted Agent Server, and proprietary deployment workflows.
- Deep Agents `ContextHubBackend`, because it is LangSmith Hub-backed.
- Managed async-subagent deployments if they require LangSmith Deployment; self-host Agent Protocol-compatible servers are acceptable.

## 1. Architecture and Package Layering

`langchain-core` defines the base contracts: runnables, messages, tools, chat models, embeddings, vector stores, stores, callbacks, and serialization. `langchain` adds agent factories and middleware. Provider packages such as `langchain-openai`, `langchain-anthropic`, and `langchain-google-genai` implement model integrations. `langgraph` is the stateful runtime for graph execution, persistence, interrupts, durable execution, streaming, and fault tolerance. `deepagents` is the opinionated harness for long-horizon agents.

Dependency direction:

```text
langchain-core -> langchain -> langgraph runtime integration -> deepagents
```

`langgraph-prebuilt`, `langgraph-checkpoint`, and `langgraph-sdk` are separately versioned pieces of the LangGraph ecosystem. `langgraph-prebuilt` is bundled with `langgraph`.

