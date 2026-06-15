## LangGraph - Thinking in LangGraph

Sources:

- https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph
- https://docs.langchain.com/oss/python/langgraph/workflows-agents

Use this reference for conceptual design, workflow decomposition, and selecting a LangGraph pattern before implementing.

### Core design loop

When designing a LangGraph agent:

1. Start from the real process or workflow to automate.
2. Split the process into discrete steps. Each step usually becomes a node.
3. Identify which steps make decisions and which steps always continue to the same next step.
4. Decide what each step needs: static prompt/context, dynamic state, external data, tools, side effects, human input, retry behavior, and desired output.
5. Design state as shared memory between nodes.
6. Build nodes as ordinary Python functions that return state updates or `Command`.
7. Wire only the essential graph edges; let nodes route with `Command` where that keeps the flow clearer.
8. Compile with a checkpointer when using interrupts, persistence, resume, or long-running threads.

### Step types

Use different node styles based on the operation:

- LLM steps: classify, reason, decide, transform, generate, or synthesize text. Keep prompts in the node, not in graph state.
- Data steps: retrieve from docs, APIs, CRM, vector stores, databases, or other external systems. Add retries for transient failures and cache only when freshness requirements allow it.
- Action steps: send emails, create tickets, write files, update systems, or call external APIs with side effects. Treat these as irreversible or high-blast-radius unless proven otherwise.
- User input steps: pause for approval, missing information, edits, escalation, or policy-sensitive decisions. Use `interrupt()` with a checkpointer.

### State design

State is the durable shared memory for the graph. Put data in state when later nodes need it, when it is expensive to re-fetch, or when it is needed for resume/debugging.

Do not store values that can be cheaply derived from existing state. Keep state raw and structured; format prompts inside nodes on demand. This makes prompt changes easier and makes debugging clearer.

Typical state fields:

- original input and immutable request metadata
- classification or routing result
- raw search/API/tool results
- draft or generated content
- review decisions and final output
- execution metadata needed for recovery or audit

### Node granularity

Prefer nodes that each do one coherent thing. Smaller nodes improve checkpoint recovery, streaming progress, observability, targeted retries, and isolated testing.

Separate nodes are especially useful when:

- an external API call has its own retry and timeout policy
- a decision should be inspectable before taking action
- an LLM call, database lookup, and side-effecting action have different failure modes
- a step may need human review or resume

Combining nodes is valid when the operations are cheap, tightly coupled, and do not need separate checkpoints or observability.

### Routing with `Command`

When a node both updates state and decides the next node, return `Command(update=..., goto=...)`.

```python
from typing_extensions import Literal
from langgraph.types import Command

def classify(state) -> Command[Literal["search_docs", "human_review", "draft"]]:
    decision = route_request(state["input"])
    if decision.needs_human:
        return Command(update={"classification": decision}, goto="human_review")
    if decision.needs_docs:
        return Command(update={"classification": decision}, goto="search_docs")
    return Command(update={"classification": decision}, goto="draft")
```

Use normal edges for fixed transitions and conditional edges when the routing logic belongs outside the node.

### Error handling as graph behavior

Choose error handling based on who can fix the problem:

- Transient system failures: use `RetryPolicy`, per-node timeouts, and retryable node boundaries.
- LLM-recoverable failures: write the error into state and route back to the LLM/tool-selection node.
- User-fixable failures: pause with `interrupt()` and resume with `Command(resume=...)`.
- Recoverable after retries: use node-level `error_handler=` where available to route to a compensation branch.
- Unexpected errors: let them surface for debugging instead of swallowing them.

For side-effecting actions, make idempotency explicit. Code before `interrupt()` can run again on resume, so keep pre-interrupt work safe to repeat.

## LangGraph - Workflows and Agents

Workflows follow predefined code paths. Agents dynamically choose steps and tools during execution. In LangGraph, both are graphs and can use persistence, streaming, debugging, deployment, and human-in-the-loop.

Before choosing a pattern, check whether the task has predictable decomposition, independent subtasks, routing categories, unknown subtask counts, iterative quality gates, or open-ended tool use.

### LLM augmentations

Most workflow patterns rely on one or more of:

- structured output for classification, planning, routing, grading, or extraction
- tool calling for model-selected actions
- short-term memory through graph state or `MessagesState`
- persistence through checkpointers

### Prompt chaining

Use prompt chaining when a task is naturally split into a known sequence of LLM calls and each step depends on the previous step.

Good fit:

- translate, verify, then polish
- generate, critique, then revise
- extract, normalize, then summarize

Implementation options:

- Graph API: nodes for each step plus fixed or conditional edges.
- Functional API: `@entrypoint` and `@task` when Python control flow is clearer.

### Parallelization

Use parallelization when subtasks are independent or when multiple attempts/evaluations improve confidence.

Good fit:

- run independent document checks at the same time
- score an answer with multiple evaluators
- generate multiple candidates before selection

In the Graph API, parallel branches can write to a reducer-backed state key such as `Annotated[list, operator.add]`. In the Functional API, launch tasks and collect their futures.

### Routing

Use routing when the input should first be classified and then sent to a specialized path.

Good fit:

- support intent routing
- product/pricing/refund/returns handling
- choose a specialist model, prompt, or toolset

Use structured output for the routing decision, then either:

- return a branch name from `add_conditional_edges`
- return `Command(goto=...)` from the routing node when the node also updates state

### Orchestrator-worker

Use orchestrator-worker when subtasks cannot be fully known before execution.

The orchestrator plans, creates subtasks, delegates them to workers, and synthesizes their outputs. This pattern fits code changes across an unknown number of files, reports with dynamically planned sections, and research tasks with expandable scope.

In the Graph API, use `Send` to create dynamic workers:

```python
from langgraph.types import Send

def assign_workers(state):
    return [
        Send("write_section", {"section": section})
        for section in state["sections"]
    ]
```

Workers should write results to a reducer-backed shared state key, for example `completed_sections: Annotated[list, operator.add]`.

### Evaluator-optimizer

Use evaluator-optimizer when output quality can be judged and the generator may need multiple attempts.

Good fit:

- translation preserving meaning
- code generation against explicit criteria
- policy-sensitive drafting
- response generation with rubric-based acceptance

The generator creates an output. The evaluator grades it with structured feedback. If it fails, route back to generation with the feedback in state. Include a loop limit or stopping condition.

### Agent loop

Use an agent loop when the problem and solution path are unpredictable and the model must choose tools dynamically.

Core loop:

1. LLM receives messages and decides whether to call a tool.
2. If tool calls exist, tool execution produces `ToolMessage` results.
3. Tool results are appended to messages.
4. The loop returns to the LLM.
5. Stop when the LLM produces no tool calls.

Use `MessagesState` or an `add_messages` reducer for message history. Add recursion limits and tool safeguards for open-ended agents.

### `ToolNode`

`ToolNode` is the prebuilt LangGraph tool-execution node. Use it when you need graph-level control over an agent loop but do not want to hand-write parallel tool execution, error handling, and tool/state injection.

```python
from langgraph.graph import MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

builder = StateGraph(MessagesState)
builder.add_node("tools", ToolNode(tools))
```

`ToolNode` is lower-level than `langchain.agents.create_agent`; use it when you need custom graph structure around tool execution.

### Pattern selection

- Predictable sequence: prompt chaining.
- Independent fixed subtasks: parallelization.
- Input category selects path: routing.
- Unknown number of subtasks: orchestrator-worker.
- Quality requires iteration: evaluator-optimizer.
- Model chooses tools/process dynamically: agent loop.
- Need high-level batteries-included agent: `langchain.agents.create_agent`.
- Need explicit orchestration, persistence, resume, or custom graph topology: LangGraph Graph API or Functional API.
