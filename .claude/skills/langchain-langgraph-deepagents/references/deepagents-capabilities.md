## 29. Deep Agents - Skills

Skills are directories with a `SKILL.md` file and optional `scripts/`, `references/`, and `assets/`.

`SKILL.md` frontmatter:

```yaml
---
name: langgraph-docs
description: Use this skill for requests related to LangGraph documentation and implementation guidance.
---
```

Deep Agents skills follow progressive disclosure:

1. Startup loads only `name` and `description`.
2. When activated, the agent reads the full `SKILL.md`.
3. Supporting resources are read or executed only when the instructions reference them.

Rules:

- `skills` is `list[str]`.
- Paths must use forward slashes.
- Paths are relative to the backend root.
- Later skill sources override earlier ones for the same skill name.
- Keep `SKILL.md` concise, ideally under 5,000 tokens and under 500 lines.
- Keep referenced files one level deep from `SKILL.md`.
- Use specific descriptions to avoid wrong skill activation.

Filesystem-backed skills:

```python
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

backend = FilesystemBackend(root_dir="./my-project", virtual_mode=True)

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    backend=backend,
    skills=["/skills/"],
)
```

StateBackend skills must be seeded through the input `files` field using `create_file_data()`:

```python
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langgraph.checkpoint.memory import MemorySaver

backend = StateBackend()
skills_files = {
    "/skills/langgraph-docs/SKILL.md": create_file_data(skill_content),
}

agent = create_deep_agent(
    model="openai:gpt-5.4",
    backend=backend,
    skills=["/skills/"],
    checkpointer=MemorySaver(),
)

result = agent.invoke(
    {
        "messages": [{"role": "user", "content": "What is LangGraph?"}],
        "files": skills_files,
    },
    config={"configurable": {"thread_id": "12345"}},
)
```

Interpreter skills require passing `skills_backend` to `CodeInterpreterMiddleware`.

## 30. Deep Agents - Interpreter / QuickJS (`deepagents[quickjs]`)

Use interpreters when the agent needs code-first control flow inside the agent loop:

- loops
- branching
- retries
- aggregation
- deterministic data transforms
- calling selected tools from code
- keeping intermediate variables out of model context

Install:

```text
pip install -U "deepagents[quickjs]"
```

Configure:

```python
from deepagents import create_deep_agent
from langchain_quickjs import CodeInterpreterMiddleware

agent = create_deep_agent(
    model="openai:gpt-5.4",
    middleware=[CodeInterpreterMiddleware()],
)
```

The middleware adds an `eval` tool by default. It runs TypeScript in a persistent QuickJS context, captures `console.log`, and returns the last expression result.

Programmatic tool calling (PTC):

```python
agent = create_deep_agent(
    model="openai:gpt-5.4",
    middleware=[CodeInterpreterMiddleware(ptc=["task", "web_search"])],
)
```

PTC exposes allowlisted tools under a JavaScript `tools` namespace. Tool names are converted to camel case, e.g. `web_search` -> `tools.webSearch(...)`.

PTC limitation: tool calls made through the interpreter bridge do not go through the normal tool-calling path, so `interrupt_on` approval workflows are not enforced per PTC-invoked tool call.

Interpreter snapshots:

- `snapshot_between_turns=True` by default.
- Interpreter state is snapshotted after each agent run and restored before the next run.
- Checkpoint time travel can restore interpreter snapshots.
- Snapshots preserve interpreter variables, not external side effects caused by tools.
- Unserializable values such as functions/classes may not restore as usable values.

`CodeInterpreterMiddleware` options:

- `memory_limit`: default 64 MB.
- `timeout`: default 5 seconds per eval.
- `max_ptc_calls`: default 256.
- `tool_name`: default `"eval"`.
- `max_result_chars`: default 4000.
- `capture_console`: default `True`.
- `ptc`: allowlist of tool names or tool objects.
- `skills_backend`: backend for interpreter skill modules.
- `snapshot_between_turns`: default `True`.
- `max_snapshot_bytes`: defaults to `memory_limit`.

Security:

- QuickJS has no host filesystem, network, shell, package, or clock access by default.
- Every PTC tool is an explicit capability bridge.
- Treat the PTC allowlist as a permission boundary.
- Use sandboxes, not interpreters, for OS-level command execution, package installs, tests, or filesystem mutation outside the agent tools.

## 31. Deep Agents - Harness Profiles

`HarnessProfile` packages per-provider or per-model defaults applied after the chat model is constructed.

Profile capabilities:

- `base_system_prompt`
- `system_prompt_suffix`
- `tool_description_overrides`
- `excluded_tools`
- `excluded_middleware`
- `extra_middleware`
- `general_purpose_subagent`

```python
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

register_harness_profile(
    "openai:gpt-5.4",
    HarnessProfile(
        system_prompt_suffix="Respond in under 100 words.",
        excluded_tools={"execute"},
        excluded_middleware={"SummarizationMiddleware"},
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)
```

Prompt assembly:

```text
USER -> (BASE or CUSTOM) -> SUFFIX
```

- `USER`: caller `system_prompt=`.
- `BASE`: SDK default prompt.
- `CUSTOM`: `HarnessProfile.base_system_prompt`, replaces `BASE`.
- `SUFFIX`: `HarnessProfile.system_prompt_suffix`, appended last.

For declarative subagents, each subagent resolves the profile for its own model.

To hide filesystem tools, use `excluded_tools`; do not try to remove required scaffolding middleware. Listing `FilesystemMiddleware`, `SubAgentMiddleware`, or the internal permissions middleware in `excluded_middleware` raises an error.

Profile lookup for preconfigured model instances:

1. exact `provider:identifier`
2. identifier-only when identifier already contains `:`
3. provider fallback

Provider profiles are narrower than harness profiles and package model-construction arguments such as API keys, timeouts, retry settings, and profile metadata.

## 32. Deep Agents - Memory

`memory` loads AGENTS-style files at startup. Use it for persistent instructions or policies that should be visible to the agent.

Use backend choice to decide persistence:

- `StateBackend`: thread-scoped memory files.
- `StoreBackend`: cross-thread memory.
- `FilesystemBackend`: disk-backed memory.
- `CompositeBackend`: route memories separately from workspace files.

For shared organization policies, use read-only permission rules on memory paths so the agent can read but not edit them.

## 33. Deep Agents - HITL and Tool Approval

`interrupt_on` config:

```python
from langchain.agents.middleware import InterruptOnConfig

agent = create_deep_agent(
    model=model,
    tools=[dangerous_tool],
    interrupt_on={
        "dangerous_tool": True,
        "write_file": InterruptOnConfig(
            allowed_decisions=["approve", "edit", "reject"]
        ),
    },
    checkpointer=checkpointer,
)
```

Requires a checkpointer. Permission rules with `mode="interrupt"` are merged into the same HITL flow.

Subagents can define their own `interrupt_on`. Synchronous subagents inherit or replace permissions depending on their spec. Async subagents are managed through Agent Protocol servers and their own graph behavior.

## 34. Deep Agents - Streaming and Persistence

Deep Agents return compiled LangGraph graphs, so use LangGraph streaming and persistence:

- `checkpointer=`
- `store=`
- `.stream()`
- `.astream()`
- `stream_events(..., version="v3")`
- `durability=`

Use `version="v2"` for normal stream chunks and `version="v3"` for event streaming where supported.

Deep Agents `0.6` uses `DeltaChannel` for message history and files to reduce checkpoint growth for long-running threads.

## 35. Deep Agents - Sandboxes vs Interpreters vs Local Shell

Use the right execution layer:

| Need | Use |
| --- | --- |
| One or two tool calls | Normal tool calling |
| Loops, branching, aggregation inside agent loop | QuickJS interpreter |
| Programmatic calls to selected agent tools | Interpreter with PTC |
| Reusable deterministic helpers | Interpreter skills |
| Shell commands, tests, package installs, OS filesystem | Sandbox backend |
| Trusted local CLI development with host shell | `LocalShellBackend`, with HITL |

Do not use `LocalShellBackend` for production, shared hosts, web servers, or untrusted input.

