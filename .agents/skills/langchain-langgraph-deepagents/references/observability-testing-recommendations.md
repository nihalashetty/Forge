## 36. Cross-Cutting - Observability Without LangSmith

Use open-source/self-hostable observability when LangSmith is out of scope:

- callbacks via `config={"callbacks": [handler]}`
- Langfuse callback handler
- OpenTelemetry
- OTLP collector/exporters
- trace attributes via baggage/context propagation

Langfuse is open source and can be self-hosted. Its LangChain callback handler captures LLM/tool/chain steps. Use OTEL GenAI semantic conventions where possible and filter unwanted scopes at the client or collector.

## 37. Cross-Cutting - Async, Errors, and Testing

Async APIs:

- `ainvoke`
- `astream`
- `astream_events`
- async middleware hooks
- async checkpointers and stores

Retries/fallbacks:

- `.with_retry()`
- `.with_fallbacks()`
- `RetryPolicy`
- `ToolNode` error handling
- model retry middleware
- tool retry middleware
- `ModelFallbackMiddleware`
- node-level `error_handler=`

Testing:

- `LLMToolEmulator` for tool emulation.
- mock chat models.
- assert returned state and messages.
- use `InMemorySaver` for deterministic HITL tests.
- test middleware state changes explicitly.
- for sandbox integrations, use the standard sandbox integration test suite where applicable.

## Recommendations

1. Pin deliberately: `langchain==1.3.6`, `langchain-core==1.4.3`, `langgraph==1.2.4`, `langgraph-prebuilt==1.1.0`, `langgraph-checkpoint==4.1.1`, `langgraph-sdk==0.4.2`, `deepagents==0.6.8`, and exact partner package versions.
2. Choose the right level: `create_agent` for most agents, raw LangGraph for custom orchestration, `create_deep_agent` for long-horizon autonomy with files/subagents/skills.
3. Use `ToolRuntime` for runtime access in tools.
4. Use checkpointers for thread memory, interrupts, resume, and time travel.
5. Use stores for cross-thread memory.
6. Use LangGraph streaming `version="v2"` for stream chunks and event streaming `version="v3"` for app-facing event streams.
7. Use `DeltaChannel` for append-heavy state when checkpoint size grows with thread length.
8. Treat filesystem, shell, sandbox, MCP, and interpreter PTC tools as capability boundaries.
9. Always set `virtual_mode=True` for `FilesystemBackend(root_dir=...)`.
10. Never rely on path permissions to secure shell execution.
11. Use sandboxes for untrusted code execution.
12. Include `handle_tool_errors` behavior when using MCP tools.
13. Use harness profiles for model-specific Deep Agents defaults.
14. Use skills with progressive disclosure; keep `SKILL.md` lean and move deep reference material into `references/`.
15. Keep LangSmith/Platform-specific features explicitly labeled if the project scope is OSS-only.

## Caveats

- `deepagents` is Beta.
- `DeltaChannel` is Beta.
- Deep Agents async subagents are preview and may change.
- Interpreters are Beta and run QuickJS in-process; they are not a full production security sandbox.
- Model strings in examples must be verified against the target provider.
- `langchain-community` is being sunset. Prefer first-party partner packages or new integration packages.
- `langchain-classic` is for legacy APIs; do not build new systems on `LLMChain`, old agent types, or legacy memory.
- LangGraph hosted Platform/Studio/Agent Server and LangSmith are outside OSS-only recommendations.
- If a document claims to be a Claude Code or Agent Skill, it must be packaged as a `SKILL.md` with valid YAML frontmatter and progressive-disclosure references; this file is a technical reference and should be split before being installed as a production skill.

