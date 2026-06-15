---
name: langchain-langgraph-deepagents
description: Use when building, reviewing, debugging, or answering questions about Python LangChain, LangGraph, or Deep Agents, including current package versions, create_agent, middleware, ToolRuntime, MCP tools, StateGraph, persistence, streaming, human-in-the-loop, frontend/event streaming patterns, Deep Agents backends, permissions, subagents, skills, interpreters, sandboxes, Deep Agents Code, protocols, recipes, and OSS-only deployment choices.
when_to_use: Use for Python agent work involving LangChain 1.x, LangGraph 1.x, Deep Agents 0.6.x, migrations from legacy LangChain APIs, package version compatibility, or selection between create_agent, StateGraph, Functional API, create_react_agent, create_deep_agent, Agent Server, and Deep Agents Code.
argument-hint: "[topic, bug, design, or implementation task]"
allowed-tools: Read Grep WebFetch
---

# LangChain, LangGraph and Deep Agents

Use this skill to answer or implement work involving the current Python LangChain, LangGraph, and Deep Agents stack.

## Default Approach

1. Verify whether the question is version-sensitive. If it is, check official docs or package metadata before giving exact version or API guidance.
2. Prefer `langchain.agents.create_agent` for most new agents.
3. Use raw LangGraph `StateGraph` or the Functional API when the workflow needs explicit state, routing, custom graph structure, or durable orchestration.
4. For LangGraph design questions, first map the process into nodes, state, routing, persistence, and error/HITL behavior before writing code.
5. Use `deepagents.create_deep_agent` when the task needs planning, a virtual filesystem, subagents, skills, memory, permissions, profiles, or optional code execution.
6. Avoid recommending legacy LangChain APIs such as `LLMChain`, old agent types, or old memory classes for new systems.
7. Treat filesystem, shell, sandbox, MCP, and interpreter programmatic-tool-calling capabilities as permission boundaries.
8. If the user requests OSS-only guidance, avoid LangSmith, LangGraph Platform, Studio, hosted Agent Server, and LangSmith-backed Deep Agents features except to mark them out of scope.

## Reference Files

Read only the relevant reference file for the task:

- `references/overview-versioning.md`: current versions, package layering, factory selection, OSS-only scope.
- `references/langchain-agents.md`: models, messages, tools, `ToolRuntime`, `create_agent`, middleware, RAG, LCEL, memory, callbacks, MCP.
- `references/langchain-advanced-ui.md`: context engineering, guardrails, HITL middleware, event streaming, multi-agent patterns, frontend `useStream` patterns, frontend integrations, and Agent Server dependent UI capabilities.
- `references/langgraph-design-patterns.md`: `Thinking in LangGraph`, workflow-vs-agent selection, prompt chaining, parallelization, routing, orchestrator-worker, evaluator-optimizer, agent loops, and `ToolNode`.
- `references/langgraph-runtime.md`: `StateGraph`, state, reducers, `DeltaChannel`, control flow, persistence, stores, HITL, streaming, fault tolerance, subgraphs, Functional API, multi-agent, prebuilt components, deployment.
- `references/langgraph-app-ops.md`: API selection, `langgraph.json`, application structure, local server, Agent Server SDK, frontend graph execution, testing, deployment, and compatibility caveats.
- `references/deepagents-core.md`: `create_deep_agent`, state, built-in tools, backends, permissions, sync/async subagents, middleware stack.
- `references/deepagents-capabilities.md`: skills, interpreter/QuickJS, harness profiles, memory, HITL, streaming, sandboxes versus interpreters.
- `references/deepagents-recipes-protocols.md`: Deep Agents context engineering, custom state, model providers, MCP/ACP/A2A, Deep Agents Code, frontend patterns, recipes, and production guidance.
- `references/observability-testing-recommendations.md`: observability without LangSmith, async/error/testing guidance, recommendations, and caveats.

## Key Rules

- Current pinned versions in this skill are dated June 10 2026. Re-check before claiming they are still latest.
- Use `ToolRuntime` for tool runtime access.
- Use checkpointers for thread memory, interrupts, resume, and time travel.
- Use stores for cross-thread memory.
- Use LangGraph streaming `version="v2"` for stream chunks and event streaming `version="v3"` for app-facing event streams where supported.
- Always set `virtual_mode=True` for `FilesystemBackend(root_dir=...)`.
- Never rely on path permissions to secure shell execution.
- Use sandboxes, not `LocalShellBackend`, for untrusted code execution.
- Include `handle_tool_errors` behavior when advising on MCP tools.
- Always verify with original docs from LangChain, LangGraph, and Deep Agents directly from their official sources for the most current and accurate information, even though this skill is designed to be up-to-date as of June 2026. [IMPORTANT: This skill's information may become outdated, so always cross-reference with official documentation for critical decisions.]
