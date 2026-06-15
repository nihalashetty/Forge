# AI Agent Platform — Schema Contracts (Single Source of Truth)
**Version:** 1.0 · **Date:** June 2026 · **Codename:** Forge · **Audience:** Claude Code (backend compiler + validation) and Claude Design (config forms)

> Document 4 of 4. These JSON Schemas are the **contract** shared by three consumers: (1) the backend **validator** (reject invalid configs on save), (2) the **compiler** (`compile_workflow`, `build_middleware`, tool/auth materialization in Doc 2), and (3) the frontend **`<SchemaForm>`** (Doc 3 §8). Backend and frontend must import the *same* schema files. Schemas use **JSON Schema draft 2020-12** plus an `x-ui` vendor extension that maps fields to form renderers.
>
> Names here match Doc 2 exactly (node types, middleware types, tool kinds, auth kinds, IOType). When you add a capability, add it in three coordinated places: a schema here → a `NodeSpec`/builder registration (Doc 2) → nothing in the UI (the form + palette are generated). That is the whole point of this document.

---

## 0. `x-ui` extension & default renderer mapping

`x-ui` is ignored by validation and read by `<SchemaForm>`. If `x-ui.widget` is absent, the renderer is inferred from the JSON type:

| JSON Schema | Default renderer | `x-ui.widget` overrides |
|---|---|---|
| `string` | text input | `mono`, `textarea`, `code` (+`language`), `model-picker`, `reference` (+`refType`), `jmespath` (+`tokenMeter`), `token-template` (+`tokens`), `color`, `secret-ref` |
| `string` + `enum` | select; **segmented** if ≤3 | `segmented`, `radio` |
| `integer`/`number` | stepper (+`unit`) | `slider` |
| `boolean` | toggle (+`effect` inline text) | `checkbox` |
| `array` of `object` | reorderable **rows-table** (+`columns`) | `chips`, `middleware-stack` |
| `array` of `string` | tag input or multiselect | `chips` (+`refType`) |
| `object` | nested section | — |
| `oneOf` discriminated by `kind`/`type` | **kind-switcher** (swaps subform) | — |

Common `x-ui` keys: `section` (group label), `order` (int), `help` (effect-focused helper text), `modelView: true` (mark "the LLM sees this"), `advanced: true` (collapse under Advanced), `placeholder`, `collapsible`.

Shared token-template variables by context: tool requests use `{{kwargs.*}}`; auth fetch/inject use `{{cred.*}}`, `{{ctx.*}}`, `{{extracted.*}}`; dynamic rules use a RestrictedPython expression over `state`/`context`.

---

## 1. Shared definitions (`$defs`) — `schemas/common.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "forge/common",
  "$defs": {
    "IOType": {
      "type": "string",
      "enum": ["messages","text","json","tool","embedding","vector","any","control"],
      "description": "Port data type. Connection valid iff source/target compatible; 'any' matches all; 'control' only connects to 'control'."
    },
    "Port": {
      "type": "object",
      "required": ["id","io_type","direction"],
      "properties": {
        "id": {"type":"string","pattern":"^[a-z0-9_]+$"},
        "label": {"type":"string"},
        "io_type": {"$ref":"#/$defs/IOType"},
        "direction": {"type":"string","enum":["in","out"]},
        "required": {"type":"boolean","default":true},
        "many": {"type":"boolean","default":false,"description":"Accepts/produces multiple connections (e.g. fanout/join control ports)."}
      }
    },
    "ModelRef": {
      "type":"string",
      "description":"Provider-prefixed model id, e.g. 'anthropic:claude-sonnet-4-6', 'openai:gpt-5.4', 'google_genai:gemini-3.1-pro-preview', or a gateway 'litellm:...'/'openrouter:...'.",
      "x-ui": {"widget":"model-picker"}
    },
    "ContextSize": {
      "type":"array","minItems":2,"maxItems":2,
      "prefixItems":[{"type":"string","enum":["tokens","messages","fraction"]},{"type":"number"}],
      "description":"LangChain ContextSize tuple, e.g. ['tokens',4000] or ['fraction',0.8]."
    },
    "TemplateString": {
      "type":"string",
      "description":"May contain {{...}} token references resolved at runtime."
    },
    "Identifier": {"type":"string","pattern":"^[a-zA-Z0-9_-]+$","description":"snake_case/kebab; no spaces (provider compatibility)."},
    "SecretRef": {
      "type":"string","pattern":"^(secret|vault)://.+",
      "description":"Reference to a stored secret; never a plaintext value.",
      "x-ui":{"widget":"secret-ref"}
    },
    "Expression": {
      "type":"string",
      "description":"RestrictedPython expression evaluated over the run state/context. No imports, no attribute escapes."
    },
    "RetryPolicy": {
      "type":"object",
      "properties":{
        "max_retries":{"type":"integer","minimum":0,"default":2},
        "backoff_factor":{"type":"number","default":2.0},
        "initial_delay":{"type":"number","default":1.0},
        "max_delay":{"type":"number","default":60.0},
        "jitter":{"type":"boolean","default":true},
        "retry_on":{"type":"array","items":{"type":"string"},"description":"Exception class names or HTTP status codes to retry."}
      }
    }
  }
}
```

---

## 2. Workflow (executable) — `schemas/workflow.json`

The compiler input. Canvas JSON (React Flow round-trip) is a separate, UI-owned shape and is **not** validated here.

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"forge/workflow",
  "type":"object",
  "required":["id","version","state","entry_node","nodes","edges"],
  "properties":{
    "id":{"$ref":"forge/common#/$defs/Identifier"},
    "version":{"type":"integer","minimum":1},
    "state":{
      "type":"object",
      "description":"Map of state field name -> StateFieldSpec. Compiled to a TypedDict (NOT pydantic).",
      "additionalProperties":{"$ref":"#/$defs/StateFieldSpec"},
      "x-ui":{"widget":"rows-table","section":"State schema",
              "columns":["__key__","type","reducer"]}
    },
    "entry_node":{"type":"string"},
    "global_middleware":{
      "type":"array","items":{"$ref":"forge/middleware#/$defs/MiddlewareEntry"},
      "description":"Prepended to every agent node's stack at compile time.",
      "x-ui":{"widget":"middleware-stack","section":"Global middleware"}
    },
    "error_policy":{"type":"string","enum":["halt","continue","route_to_error_node"],"default":"halt"},
    "timeout_seconds":{"type":"integer","default":600},
    "max_concurrency":{"type":"integer","default":8},
    "nodes":{"type":"array","items":{"$ref":"#/$defs/NodeInstance"},"minItems":1},
    "edges":{"type":"array","items":{"$ref":"#/$defs/Edge"}}
  },
  "$defs":{
    "StateFieldSpec":{
      "type":"object","required":["type","reducer"],
      "properties":{
        "type":{"type":"string","enum":["list[message]","list[str]","list[json]","str","int","float","bool","json"]},
        "reducer":{"type":"string","enum":["add_messages","add","last","merge"],
          "description":"add_messages for messages; add for accumulating lists; last for overwrite; merge for dict."}
      }
    },
    "NodeInstance":{
      "type":"object","required":["id","type","config"],
      "properties":{
        "id":{"$ref":"forge/common#/$defs/Identifier"},
        "type":{"type":"string","description":"Must exist in the Node Type Registry."},
        "config":{"type":"object","description":"Validated against the node type's own schema (Section 3)."},
        "position":{"type":"object","properties":{"x":{"type":"number"},"y":{"type":"number"}}}
      }
    },
    "Edge":{
      "type":"object","required":["source","target"],
      "properties":{
        "source":{"type":"string"},
        "source_handle":{"type":"string"},
        "target":{"type":"string","description":"Target node id or 'END'."},
        "target_handle":{"type":"string"},
        "condition":{"$ref":"forge/common#/$defs/Expression","description":"If present, becomes a conditional edge."},
        "branches":{"type":"object","additionalProperties":{"type":"string"},
                    "description":"value -> target-node-id map for routers."}
      }
    }
  }
}
```

**Validation rules beyond schema** (implement in the validator): referenced node ids exist; `entry_node` exists; all `required` input ports are connected; no orphan nodes; cycles only through nodes whose `NodeSpec.allows_cycle` is true; referenced tool/auth/model/workflow ids exist and are enabled; budgets present if the project requires.

---

## 3. Node type config schemas — `schemas/nodes/*.json`

Each schema validates the `config` object of a `NodeInstance`. Ports are declared in the `NodeSpec` (shown inline here as comments) and are not part of `config`.

### 3.1 `start` / `end`
```json
{ "$id":"forge/nodes/start", "type":"object", "properties":{}, "additionalProperties":false }
// ports: start -> out:control ; end -> in:control
```

### 3.2 `agent` (and `deep_agent`)
The most important schema. `flavor` switches on Deep-Agent-only sections.
```json
{
  "$id":"forge/nodes/agent",
  "type":"object",
  "required":["flavor","model"],
  "properties":{
    "flavor":{"type":"string","enum":["agent","deep_agent"],"default":"agent",
              "x-ui":{"widget":"segmented","order":1,"section":"Flavor"}},
    "name":{"$ref":"forge/common#/$defs/Identifier","x-ui":{"section":"Flavor"}},
    "model":{"$ref":"forge/common#/$defs/ModelRef","x-ui":{"section":"Model","order":2}},
    "model_params":{"type":"object","x-ui":{"section":"Model","advanced":true},
      "properties":{"temperature":{"type":"number"},"max_tokens":{"type":"integer"},"timeout":{"type":"integer"},"base_url":{"type":"string"}}},
    "dynamic_model":{"type":"object","x-ui":{"section":"Model","advanced":true},
      "description":"Compiles to a @wrap_model_call middleware.",
      "properties":{
        "enabled":{"type":"boolean","default":false},
        "rules":{"type":"array","items":{"type":"object","required":["when","use"],
          "properties":{"when":{"$ref":"forge/common#/$defs/Expression"},"use":{"$ref":"forge/common#/$defs/ModelRef"}}},
          "x-ui":{"widget":"rows-table"}},
        "default":{"$ref":"forge/common#/$defs/ModelRef"}}},
    "system_prompt":{"type":"string","x-ui":{"widget":"textarea","section":"Instructions","order":3,"modelView":true}},
    "dynamic_prompt":{"type":"object","x-ui":{"section":"Instructions","advanced":true},
      "properties":{"enabled":{"type":"boolean"},
        "rules":{"type":"array","items":{"type":"object",
          "properties":{"when":{"$ref":"forge/common#/$defs/Expression"},"prompt":{"type":"string"}}},"x-ui":{"widget":"rows-table"}}}},
    "tools":{"type":"array","items":{"type":"string"},
      "description":"Tool ids (incl. MCP tool ids) available to the agent.",
      "x-ui":{"widget":"chips","refType":"tool","section":"Tools","order":4}},
    "response_format":{"$ref":"#/$defs/ResponseFormat","x-ui":{"section":"Output","order":5}},
    "middleware":{"type":"array","items":{"$ref":"forge/middleware#/$defs/MiddlewareEntry"},
      "x-ui":{"widget":"middleware-stack","section":"Middleware","order":6}},
    "memory":{"type":"object","x-ui":{"section":"Memory","order":7},
      "properties":{
        "long_term":{"type":"boolean","default":false,"x-ui":{"effect":"Persist memory across conversations"}},
        "store_namespace":{"type":"string"},
        "state_extensions":{"type":"array","items":{"type":"object",
          "properties":{"name":{"type":"string"},"type":{"type":"string"},"reducer":{"type":"string"}}},"x-ui":{"widget":"rows-table"}}}},

    "planning":{"type":"boolean","default":true,
      "x-ui":{"section":"Deep Agent","order":8,"effect":"Adds write_todos planning tool","showWhen":"flavor=deep_agent"}},
    "subagents":{"type":"array","items":{"$ref":"#/$defs/SubAgent"},
      "x-ui":{"section":"Deep Agent","order":9,"showWhen":"flavor=deep_agent"}},
    "filesystem":{"$ref":"#/$defs/FilesystemBackend","x-ui":{"section":"Deep Agent","order":10,"showWhen":"flavor=deep_agent"}},
    "sandbox":{"$ref":"#/$defs/SandboxConfig","x-ui":{"section":"Deep Agent","order":11,"showWhen":"flavor=deep_agent"}},
    "skills":{"type":"array","items":{"type":"string"},"x-ui":{"section":"Deep Agent","showWhen":"flavor=deep_agent"}},
    "permissions":{"type":"array","items":{"type":"object",
      "properties":{"path":{"type":"string"},"access":{"type":"string","enum":["read","write","none"]}}},
      "x-ui":{"widget":"rows-table","section":"Deep Agent","showWhen":"flavor=deep_agent"}}
  },
  "$defs":{
    "ResponseFormat":{
      "oneOf":[
        {"type":"object","properties":{"mode":{"const":"freeform"}}},
        {"type":"object","required":["mode","schema"],"properties":{
          "mode":{"const":"structured"},
          "strategy":{"type":"string","enum":["auto","provider","tool"],"default":"auto"},
          "schema":{"type":"object","description":"JSON Schema of the desired structured output."}}}
      ],
      "x-ui":{"widget":"kind-switcher","discriminator":"mode"}
    },
    "SubAgent":{
      "oneOf":[
        {"type":"object","required":["name","description"],"properties":{
          "name":{"$ref":"forge/common#/$defs/Identifier"},
          "description":{"type":"string","modelView":true},
          "system_prompt":{"type":"string","x-ui":{"widget":"textarea"}},
          "tools":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}},
          "model":{"$ref":"forge/common#/$defs/ModelRef"},
          "middleware":{"type":"array","items":{"$ref":"forge/middleware#/$defs/MiddlewareEntry"}}}},
        {"type":"object","required":["name","workflow_ref"],"properties":{
          "name":{"$ref":"forge/common#/$defs/Identifier"},
          "description":{"type":"string"},
          "workflow_ref":{"type":"object","properties":{"workflow_id":{"type":"string"},"version":{"type":"integer"}},
            "description":"Wrap a compiled workflow as CompiledSubAgent.","x-ui":{"widget":"reference","refType":"workflow"}}}}
      ]
    },
    "FilesystemBackend":{
      "type":"object",
      "properties":{
        "kind":{"type":"string","enum":["state","disk","store","composite"],"default":"state"},
        "routes":{"type":"object","additionalProperties":{"type":"string","enum":["state","disk","store"]},
          "description":"For composite, e.g. {'/memories/':'store'}."}}
    },
    "SandboxConfig":{
      "type":"object",
      "properties":{
        "kind":{"type":"string","enum":["none","docker","remote"],"default":"none"},
        "provider":{"type":"string","enum":["e2b","modal","daytona","runloop","agentcore"],"x-ui":{"showWhen":"kind=remote"}},
        "pattern":{"type":"string","enum":["agent_in_sandbox","sandbox_as_tool"],"default":"sandbox_as_tool"},
        "limits":{"$ref":"#/$defs/Limits"}}
    },
    "Limits":{"type":"object","properties":{
      "cpu_seconds":{"type":"integer","default":10},"memory_mb":{"type":"integer","default":512},
      "timeout_seconds":{"type":"integer","default":30},"egress_allowlist":{"type":"array","items":{"type":"string"}}}}
  }
}
// ports: in:messages, out:messages
```

### 3.3 `llm`
```json
{ "$id":"forge/nodes/llm","type":"object","required":["model","prompt"],
  "properties":{
    "model":{"$ref":"forge/common#/$defs/ModelRef"},
    "prompt":{"$ref":"forge/common#/$defs/TemplateString","x-ui":{"widget":"textarea","modelView":true}},
    "model_params":{"type":"object"},
    "response_format":{"$ref":"forge/nodes/agent#/$defs/ResponseFormat"}}}
// ports: in:[text,json], out:[text,json]
```

### 3.4 `tool_call`
```json
{ "$id":"forge/nodes/tool_call","type":"object","required":["tool_id"],
  "properties":{
    "tool_id":{"type":"string","x-ui":{"widget":"reference","refType":"tool"}},
    "input_mapping":{"type":"object","description":"Maps state/inputs -> tool args (JMESPath/templates).","x-ui":{"widget":"rows-table"}},
    "handle_tool_errors":{"oneOf":[{"type":"boolean"},{"type":"string"}],"default":true}}}
// ports: in:json, out:json
```

### 3.5 `router`
```json
{ "$id":"forge/nodes/router","type":"object","required":["expression","cases"],
  "properties":{
    "expression":{"$ref":"forge/common#/$defs/Expression","x-ui":{"help":"Evaluated over state; result matched against cases."}},
    "cases":{"type":"object","additionalProperties":{"type":"string"},"x-ui":{"widget":"rows-table","columns":["value","target"]}},
    "default":{"type":"string","description":"Fallback target node id."}}}
// ports: in:any, out: one control port per case + default
```

### 3.6 `retrieval`
```json
{ "$id":"forge/nodes/retrieval","type":"object",
  "properties":{
    "source_filter":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"kb_source"}},
    "top_k":{"type":"integer","default":5},
    "hybrid":{"type":"boolean","default":true,"x-ui":{"effect":"Combine vector + keyword (FTS) with rank fusion"}},
    "rerank":{"type":"boolean","default":false},
    "min_score":{"type":"number"},
    "projection":{"type":"array","items":{"type":"string"},"description":"Chunk fields to surface to state."}}}
// ports: in:text, out:json
```

### 3.7 `qa_lookup`
```json
{ "$id":"forge/nodes/qa_lookup","type":"object",
  "properties":{
    "threshold":{"type":"number","default":0.85},
    "kind":{"type":"string","enum":["any","faq","error_workaround"],"default":"any"}}}
// ports: in:text, out:text
```

### 3.8 `human_input`
```json
{ "$id":"forge/nodes/human_input","type":"object","required":["prompt"],
  "properties":{
    "prompt":{"type":"string","x-ui":{"widget":"textarea"}},
    "schema":{"type":"object","description":"Optional structured input schema requested from the human."},
    "allowed_decisions":{"type":"array","items":{"type":"string","enum":["approve","edit","reject"]},"default":["approve","reject"]}}}
// ports: in:any, out:any   (calls interrupt(); requires checkpointer)
```

### 3.9 `code`
```json
{ "$id":"forge/nodes/code","type":"object","required":["language","source"],
  "properties":{
    "language":{"type":"string","enum":["python","javascript"],"x-ui":{"widget":"segmented"}},
    "source":{"type":"string","x-ui":{"widget":"code","language":"python"}},
    "input_schema":{"type":"object"},"output_schema":{"type":"object"},
    "sandbox":{"$ref":"forge/nodes/agent#/$defs/SandboxConfig"}}}
// ports: in:json, out:json
```

### 3.10 `transform`
```json
{ "$id":"forge/nodes/transform","type":"object","required":["expression"],
  "properties":{
    "engine":{"type":"string","enum":["jmespath","jq"],"default":"jmespath"},
    "expression":{"type":"string","x-ui":{"widget":"jmespath","tokenMeter":false}}}}
// ports: in:json, out:json
```

### 3.11 `subworkflow`
```json
{ "$id":"forge/nodes/subworkflow","type":"object","required":["workflow_id"],
  "properties":{
    "workflow_id":{"type":"string","x-ui":{"widget":"reference","refType":"workflow"}},
    "version":{"type":"integer"},
    "input_mapping":{"type":"object","x-ui":{"widget":"rows-table"}},
    "output_mapping":{"type":"object","x-ui":{"widget":"rows-table"}}}}
// ports: in:[messages,json], out:[messages,json]
```

### 3.12 `parallel_fanout` / `join`
```json
{ "$id":"forge/nodes/parallel_fanout","type":"object","required":["over","child_node","item_key"],
  "properties":{
    "over":{"type":"string","description":"State key holding a list."},
    "child_node":{"type":"string","description":"Node id to run per item via Send."},
    "item_key":{"type":"string","description":"State key the item is written to for each child."}}}
// ports: in:json, out:control[]

{ "$id":"forge/nodes/join","type":"object",
  "properties":{"reducer":{"type":"string","enum":["concat","merge","first","last"],"default":"concat"}}}
// ports: in:control[], out:json
```

### 3.13 `loop`
```json
{ "$id":"forge/nodes/loop","type":"object","required":["condition","max_iter"],
  "properties":{
    "condition":{"$ref":"forge/common#/$defs/Expression"},
    "max_iter":{"type":"integer","default":10}}}
// ports: in:any, out:any (allows_cycle = true)
```

### 3.14 `webhook_out` / `emit_event`
```json
{ "$id":"forge/nodes/webhook_out","type":"object","required":["url","method"],
  "properties":{
    "url":{"$ref":"forge/common#/$defs/TemplateString"},
    "method":{"type":"string","enum":["GET","POST","PUT","PATCH","DELETE"]},
    "auth_provider_id":{"type":"string","x-ui":{"widget":"reference","refType":"auth_provider"}},
    "body":{"type":"object"},"headers":{"type":"object"}}}
// ports: in:json, out:json

{ "$id":"forge/nodes/emit_event","type":"object","required":["channel"],
  "properties":{"channel":{"type":"string"},"payload":{"type":"object"}}}
// ports: in:any, out:any  (pushes a custom SSE frame)
```

---

## 4. Middleware config schemas — `schemas/middleware.json`

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"forge/middleware",
  "$defs":{
    "MiddlewareEntry":{
      "type":"object","required":["type"],
      "properties":{
        "type":{"type":"string","description":"Middleware type key (see catalog)."},
        "enabled":{"type":"boolean","default":true},
        "config":{"type":"object","description":"Validated against the per-type schema below."}
      },
      "x-ui":{"category-map":{
        "summarization":"context","context_editing":"context","todo":"context",
        "pii":"safety","guardrail_regex":"safety","openai_moderation":"safety",
        "tool_retry":"reliability","model_retry":"reliability","model_fallback":"reliability","request_signing":"reliability",
        "model_call_limit":"cost","tool_call_limit":"cost","tenant_budget":"cost","llm_tool_selector":"cost",
        "human_in_the_loop":"human",
        "anthropic_prompt_caching":"provider",
        "tool_emulator":"testing",
        "dynamic_model_by_state":"advanced","tool_filter_by_context":"advanced"
      }}
    }
  },
  "config_schemas":{
    "summarization":{"type":"object","properties":{
      "model":{"$ref":"forge/common#/$defs/ModelRef"},
      "trigger":{"oneOf":[{"$ref":"forge/common#/$defs/ContextSize"},{"type":"array","items":{"$ref":"forge/common#/$defs/ContextSize"}}]},
      "keep":{"$ref":"forge/common#/$defs/ContextSize"},
      "summary_prompt":{"type":"string","x-ui":{"widget":"textarea","advanced":true}}},
      "x-ui":{"help":"Summarize older messages when the conversation grows past the trigger; keep the most recent."}},

    "human_in_the_loop":{"type":"object","required":["interrupt_on"],"properties":{
      "interrupt_on":{"type":"object","additionalProperties":{
        "oneOf":[{"type":"boolean"},
          {"type":"object","properties":{"allowed_decisions":{"type":"array","items":{"type":"string","enum":["approve","edit","reject"]}}}}]},
        "x-ui":{"widget":"rows-table","columns":["tool","allowed_decisions"]}}},
      "x-ui":{"help":"Pause for human approval before chosen tools run. Requires a checkpointer."}},

    "model_call_limit":{"type":"object","properties":{
      "thread_limit":{"type":"integer"},"run_limit":{"type":"integer"},
      "exit_behavior":{"type":"string","enum":["end","error"],"default":"end"}}},

    "tool_call_limit":{"type":"object","properties":{
      "tool_name":{"type":"string","x-ui":{"widget":"reference","refType":"tool"}},
      "thread_limit":{"type":"integer"},"run_limit":{"type":"integer"},
      "exit_behavior":{"type":"string","enum":["continue","error","end"],"default":"continue"}}},

    "model_fallback":{"type":"object","required":["models"],"properties":{
      "models":{"type":"array","items":{"$ref":"forge/common#/$defs/ModelRef"},"minItems":1,
        "x-ui":{"widget":"chips","help":"Tried in order if the primary model fails."}}}},

    "pii":{"type":"object","required":["pii_type"],"properties":{
      "pii_type":{"type":"string","description":"email|credit_card|ip|mac_address|url|<custom>"},
      "strategy":{"type":"string","enum":["block","redact","mask","hash"],"default":"redact"},
      "detector":{"type":"string","description":"Regex pattern for custom types.","x-ui":{"widget":"mono","advanced":true}},
      "apply_to_input":{"type":"boolean","default":true},
      "apply_to_output":{"type":"boolean","default":false},
      "apply_to_tool_results":{"type":"boolean","default":false}}},

    "todo":{"type":"object","properties":{"system_prompt":{"type":"string","x-ui":{"widget":"textarea","advanced":true}}}},

    "llm_tool_selector":{"type":"object","properties":{
      "model":{"$ref":"forge/common#/$defs/ModelRef"},
      "max_tools":{"type":"integer"},
      "always_include":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}}},
      "x-ui":{"help":"Pre-select relevant tools before the main model call (saves tokens when many tools exist)."}},

    "tool_retry":{"allOf":[{"$ref":"forge/common#/$defs/RetryPolicy"}],"properties":{
      "tools":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}},
      "on_failure":{"type":"string","enum":["return_message","raise","continue"],"default":"return_message"}}},

    "model_retry":{"allOf":[{"$ref":"forge/common#/$defs/RetryPolicy"}],"properties":{
      "on_failure":{"type":"string","enum":["continue","error"],"default":"continue"}}},

    "tool_emulator":{"type":"object","properties":{
      "tools":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}},
      "model":{"$ref":"forge/common#/$defs/ModelRef"}},
      "x-ui":{"help":"Emulate tool outputs with an LLM (no real calls). For testing."}},

    "context_editing":{"type":"object","properties":{
      "edits":{"type":"array","items":{"type":"object","properties":{
        "trigger":{"type":"integer","default":100000},
        "keep":{"type":"integer","default":3},
        "clear_at_least":{"type":"integer","default":0},
        "clear_tool_inputs":{"type":"boolean","default":false},
        "exclude_tools":{"type":"array","items":{"type":"string"}},
        "placeholder":{"type":"string","default":"[cleared]"}}}}},
      "x-ui":{"help":"Clear older tool outputs once the conversation passes the trigger; keep the most recent N."}},

    "anthropic_prompt_caching":{"type":"object","properties":{
      "ttl":{"type":"string","enum":["5m","1h"],"default":"5m"}},
      "x-ui":{"help":"Cache the system prompt block on Anthropic models (cost/latency).","showWhen":"model.startsWith(anthropic)"}},

    "openai_moderation":{"type":"object","properties":{
      "apply_to_input":{"type":"boolean","default":true},"apply_to_output":{"type":"boolean","default":true}}},

    "dynamic_model_by_state":{"type":"object","required":["rules"],"properties":{
      "rules":{"type":"array","items":{"type":"object","properties":{
        "when":{"$ref":"forge/common#/$defs/Expression"},"use":{"$ref":"forge/common#/$defs/ModelRef"}}},
        "x-ui":{"widget":"rows-table"}},
      "default":{"$ref":"forge/common#/$defs/ModelRef"}},
      "x-ui":{"help":"Switch model at runtime based on state (compiles to @wrap_model_call)."}},

    "tool_filter_by_context":{"type":"object","properties":{
      "expose_when":{"type":"object","description":"e.g. {role:['admin']} matched against runtime.context."},
      "tools":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}}},
      "x-ui":{"help":"Show/hide tools at runtime by context (auth state, role, flags)."}},

    "guardrail_regex":{"type":"object","properties":{
      "patterns":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips"}},
      "on_match":{"type":"string","enum":["block","redact","flag"],"default":"block"},
      "apply_to":{"type":"string","enum":["input","output","both"],"default":"output"}}},

    "request_signing":{"type":"object","properties":{
      "auth_provider_id":{"type":"string","x-ui":{"widget":"reference","refType":"auth_provider"}},
      "tools":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","refType":"tool"}}},
      "x-ui":{"help":"Inject signed headers/credentials into matching tool calls (wrap_tool_call)."}},

    "tenant_budget":{"type":"object","properties":{
      "max_usd_per_thread":{"type":"number"},"max_tokens_per_run":{"type":"integer"},
      "on_exceed":{"type":"string","enum":["end","error"],"default":"end"}},
      "x-ui":{"help":"Stop the run when accumulated cost/tokens exceed the cap (before_model)."}}
  }
}
```

> The compiler's `MW_BUILDERS` (Doc 2 §8) keys must exactly match `config_schemas` keys here. Add a middleware = add a `config_schemas` entry + a builder + a `category-map` entry.

---

## 5. Tool config schema — `schemas/tool.json`

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"forge/tool",
  "type":"object",
  "required":["name","description","kind"],
  "properties":{
    "name":{"$ref":"forge/common#/$defs/Identifier","x-ui":{"section":"Identity","modelView":false}},
    "description":{"type":"string","x-ui":{"section":"Identity","widget":"textarea","modelView":true,
      "help":"This is what the model reads to decide when to use the tool."}},
    "kind":{"type":"string","enum":["rest_api","graphql","code","mcp","builtin"],"x-ui":{"section":"Identity","widget":"segmented"}},
    "auth_provider_id":{"type":"string","x-ui":{"section":"Auth","widget":"reference","refType":"auth_provider"}},
    "rate_limit":{"type":"object","properties":{"per_minute":{"type":"integer"}},"x-ui":{"section":"Reliability"}},
    "timeout_seconds":{"type":"integer","default":30,"x-ui":{"section":"Reliability"}},
    "retry":{"$ref":"forge/common#/$defs/RetryPolicy","x-ui":{"section":"Reliability","advanced":true}},
    "cache":{"type":"object","properties":{"ttl_seconds":{"type":"integer","default":0}},"x-ui":{"section":"Reliability","advanced":true}}
  },
  "allOf":[
    {"if":{"properties":{"kind":{"const":"rest_api"}}},"then":{"$ref":"#/$defs/RestConfig"}},
    {"if":{"properties":{"kind":{"const":"graphql"}}},"then":{"$ref":"#/$defs/GraphQLConfig"}},
    {"if":{"properties":{"kind":{"const":"code"}}},"then":{"$ref":"#/$defs/CodeConfig"}},
    {"if":{"properties":{"kind":{"const":"mcp"}}},"then":{"$ref":"#/$defs/McpConfig"}},
    {"if":{"properties":{"kind":{"const":"builtin"}}},"then":{"$ref":"#/$defs/BuiltinConfig"}}
  ],
  "$defs":{
    "FieldSpec":{
      "type":"object","required":["path","type","in"],
      "properties":{
        "path":{"type":"string","x-ui":{"widget":"mono"}},
        "type":{"type":"string","enum":["string","integer","number","boolean","object","array"]},
        "in":{"type":"string","enum":["path","query","header","body"]},
        "required":{"type":"boolean","default":false},
        "llm_visible":{"type":"boolean","default":true,"x-ui":{"effect":"Model can set this argument"}},
        "description":{"type":"string","x-ui":{"modelView":true}},
        "default":{}
      }
    },
    "ResponseFieldSpec":{
      "type":"object","required":["path"],
      "properties":{
        "path":{"type":"string","x-ui":{"widget":"mono"}},
        "description":{"type":"string","x-ui":{"modelView":true}},
        "include_in_llm":{"type":"boolean","default":true}
      }
    },
    "RestConfig":{
      "required":["request"],
      "properties":{
        "request":{"type":"object","required":["method","url_template"],"x-ui":{"section":"Request"},
          "properties":{
            "method":{"type":"string","enum":["GET","POST","PUT","PATCH","DELETE"]},
            "url_template":{"$ref":"forge/common#/$defs/TemplateString"},
            "fields":{"type":"array","items":{"$ref":"#/$defs/FieldSpec"},"x-ui":{"widget":"rows-table"}},
            "headers":{"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"value":{"$ref":"forge/common#/$defs/TemplateString"}}},"x-ui":{"widget":"rows-table"}}}},
        "response":{"type":"object","x-ui":{"section":"Response shaping"},
          "properties":{
            "fields":{"type":"array","items":{"$ref":"#/$defs/ResponseFieldSpec"},"x-ui":{"widget":"rows-table"}},
            "projection_jmespath":{"type":"string","x-ui":{"widget":"jmespath","tokenMeter":true,
              "help":"Cut the payload before it reaches the model. Compare Raw vs Projected tokens."}}}}
      }
    },
    "GraphQLConfig":{
      "required":["endpoint","query"],
      "properties":{
        "endpoint":{"type":"string"},
        "query":{"type":"string","x-ui":{"widget":"code","language":"graphql"}},
        "variables":{"type":"array","items":{"$ref":"#/$defs/FieldSpec"},"x-ui":{"widget":"rows-table"}},
        "response":{"$ref":"#/$defs/RestConfig/properties/response"}
      }
    },
    "CodeConfig":{
      "required":["language","source"],
      "properties":{
        "language":{"type":"string","enum":["python","javascript"]},
        "source":{"type":"string","x-ui":{"widget":"code","language":"python"}},
        "args_schema":{"type":"object","description":"JSON Schema of the tool's LLM-visible args."},
        "sandbox":{"$ref":"forge/nodes/agent#/$defs/SandboxConfig"}
      }
    },
    "McpConfig":{
      "required":["mcp_client_id","remote_tool_name"],
      "properties":{
        "mcp_client_id":{"type":"string","x-ui":{"widget":"reference","refType":"mcp_client"}},
        "remote_tool_name":{"type":"string"},
        "inject_context":{"type":"array","items":{"type":"string"},
          "description":"Context keys to inject via a tool interceptor (e.g. user_id, api_key)."}
      }
    },
    "BuiltinConfig":{
      "required":["builtin"],
      "properties":{"builtin":{"type":"string","enum":["web_search","web_fetch","current_time","calculator"]},
        "options":{"type":"object"}}
    }
  }
}
```

---

## 6. Auth Provider config schema — `schemas/auth_provider.json`

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"forge/auth_provider",
  "type":"object",
  "required":["name","kind"],
  "properties":{
    "name":{"$ref":"forge/common#/$defs/Identifier"},
    "kind":{"type":"string","enum":["csrf_session","oauth2_client_credentials","bearer","basic","api_key","custom_script"],
      "x-ui":{"widget":"segmented"}},
    "credentials_ref":{"$ref":"forge/common#/$defs/SecretRef"},
    "cache_ttl_seconds":{"type":"integer","default":1800},
    "refresh_on":{"type":"array","items":{"type":"integer"},"default":[401,403]},
    "per_user_context_keys":{"type":"array","items":{"type":"string"},
      "description":"Context keys (e.g. csrf, session from the widget) that vary the cache key and feed templates as {{ctx.*}}."}
  },
  "allOf":[
    {"if":{"properties":{"kind":{"const":"csrf_session"}}},"then":{"$ref":"#/$defs/FetchExtractInject"}},
    {"if":{"properties":{"kind":{"const":"oauth2_client_credentials"}}},"then":{"$ref":"#/$defs/OAuth2"}},
    {"if":{"properties":{"kind":{"const":"bearer"}}},"then":{"$ref":"#/$defs/Bearer"}},
    {"if":{"properties":{"kind":{"const":"basic"}}},"then":{"$ref":"#/$defs/Basic"}},
    {"if":{"properties":{"kind":{"const":"api_key"}}},"then":{"$ref":"#/$defs/ApiKey"}},
    {"if":{"properties":{"kind":{"const":"custom_script"}}},"then":{"$ref":"#/$defs/CustomScript"}}
  ],
  "$defs":{
    "ExtractRule":{"type":"object","required":["name","from"],"properties":{
      "name":{"type":"string"},
      "from":{"type":"string","enum":["header","cookie","json"]},
      "header":{"type":"string"},"cookie":{"type":"string"},"json_path":{"type":"string"},
      "kind":{"type":"string","enum":["value","ttl"],"default":"value"}}},
    "InjectRule":{"type":"object","required":["to","name","value"],"properties":{
      "to":{"type":"string","enum":["header","cookie","query"]},
      "name":{"type":"string"},
      "value":{"$ref":"forge/common#/$defs/TemplateString","x-ui":{"widget":"token-template","tokens":["extracted"]}}}},
    "FetchExtractInject":{
      "required":["token_fetch","extract","inject"],
      "properties":{
        "token_fetch":{"type":"object","required":["method","url"],"x-ui":{"section":"Token fetch"},
          "properties":{
            "method":{"type":"string","enum":["GET","POST"]},
            "url":{"type":"string"},
            "headers":{"type":"object","x-ui":{"widget":"token-template","tokens":["cred","ctx"]}},
            "body":{"type":"object","x-ui":{"widget":"token-template","tokens":["cred","ctx"]}}}},
        "extract":{"type":"array","items":{"$ref":"#/$defs/ExtractRule"},"x-ui":{"widget":"rows-table","section":"Extract"}},
        "inject":{"type":"array","items":{"$ref":"#/$defs/InjectRule"},"x-ui":{"widget":"rows-table","section":"Inject"}}
      }
    },
    "OAuth2":{"required":["token_url"],"properties":{
      "token_url":{"type":"string"},
      "scope":{"type":"string"},
      "client_id_ref":{"$ref":"forge/common#/$defs/SecretRef"},
      "client_secret_ref":{"$ref":"forge/common#/$defs/SecretRef"},
      "audience":{"type":"string"}}},
    "Bearer":{"properties":{"token_ref":{"$ref":"forge/common#/$defs/SecretRef"},
      "header_name":{"type":"string","default":"Authorization"},"prefix":{"type":"string","default":"Bearer "}}},
    "Basic":{"properties":{"username_ref":{"$ref":"forge/common#/$defs/SecretRef"},"password_ref":{"$ref":"forge/common#/$defs/SecretRef"}}},
    "ApiKey":{"required":["in","name"],"properties":{
      "in":{"type":"string","enum":["header","query"]},"name":{"type":"string"},"value_ref":{"$ref":"forge/common#/$defs/SecretRef"}}},
    "CustomScript":{"required":["source"],"properties":{
      "source":{"type":"string","x-ui":{"widget":"code","language":"python",
        "help":"RestrictedPython returning {headers, cookies, ttl}. Advanced — audited."}}}}
  }
}
```

---

## 7. Widget config schema — `schemas/widget.json`

```json
{
  "$schema":"https://json-schema.org/draft/2020-12/schema",
  "$id":"forge/widget",
  "type":"object",
  "required":["workflow_id"],
  "properties":{
    "workflow_id":{"type":"string","x-ui":{"widget":"reference","refType":"workflow","section":"Behavior"}},
    "theme":{"type":"object","x-ui":{"section":"Appearance"},
      "properties":{
        "primary":{"type":"string","x-ui":{"widget":"color"}},
        "bg":{"type":"string","x-ui":{"widget":"color"}},
        "surface":{"type":"string","x-ui":{"widget":"color"}},
        "fg":{"type":"string","x-ui":{"widget":"color"}},
        "radius":{"type":"integer","default":14},
        "font":{"type":"string"},
        "mode":{"type":"string","enum":["light","dark","auto"],"default":"auto"}}},
    "launcher":{"type":"object","x-ui":{"section":"Appearance"},
      "properties":{
        "icon":{"type":"string"},"label":{"type":"string"},
        "position":{"type":"string","enum":["bottom-right","bottom-left"],"default":"bottom-right"}}},
    "greeting":{"type":"string","x-ui":{"section":"Behavior"}},
    "suggested_prompts":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips","section":"Behavior"}},
    "show_tool_activity":{"type":"boolean","default":true,"x-ui":{"section":"Behavior"}},
    "allow_file_upload":{"type":"boolean","default":false,"x-ui":{"section":"Behavior"}},
    "rate_limit":{"type":"object","properties":{"per_minute":{"type":"integer"}},"x-ui":{"section":"Behavior"}},
    "host_variables":{"type":"array","x-ui":{"widget":"rows-table","section":"Host variables",
        "help":"Evaluated on the host page and passed to the run context. JS expressions execute on the host page — advanced, audited."},
      "items":{"type":"object","required":["name","source"],"properties":{
        "name":{"type":"string"},
        "source":{"type":"string","enum":["meta","cookie","js","query"]},
        "selector":{"type":"string","x-ui":{"showWhen":"source=meta"}},
        "attr":{"type":"string","x-ui":{"showWhen":"source=meta"}},
        "cookie":{"type":"string","x-ui":{"showWhen":"source=cookie"}},
        "expression":{"type":"string","x-ui":{"widget":"mono","showWhen":"source=js"}}}}},
    "security":{"type":"object","x-ui":{"section":"Security"},
      "properties":{
        "allowed_origins":{"type":"array","items":{"type":"string"},"x-ui":{"widget":"chips"}},
        "identity_verification":{"type":"boolean","default":false},
        "public_key":{"type":"string","x-ui":{"showWhen":"identity_verification=true"}}}}
  }
}
```

---

## 8. Project config schema (excerpt) — `schemas/project.json`

```json
{
  "$id":"forge/project","type":"object",
  "properties":{
    "default_model":{"$ref":"forge/common#/$defs/ModelRef"},
    "allowed_models":{"type":"array","items":{"$ref":"forge/common#/$defs/ModelRef"},"x-ui":{"widget":"chips"}},
    "provider_credentials":{"type":"object","additionalProperties":{"$ref":"forge/common#/$defs/SecretRef"},
      "description":"provider -> secret ref, e.g. {openai:'secret://proj/openai_key'}"},
    "budgets":{"type":"object","properties":{
      "max_usd_per_run":{"type":"number"},"max_tokens_per_run":{"type":"integer"},
      "monthly_usd_cap":{"type":"number"},"alert_at_fraction":{"type":"number","default":0.8}}},
    "default_middleware":{"type":"array","items":{"$ref":"forge/middleware#/$defs/MiddlewareEntry"},
      "x-ui":{"widget":"middleware-stack"}},
    "data_region":{"type":"string"},
    "rag_defaults":{"type":"object","properties":{
      "embedding_model":{"type":"string","default":"openai:text-embedding-3-small"},
      "chunk_size":{"type":"integer","default":1000},"chunk_overlap":{"type":"integer","default":200}}},
    "features":{"type":"object","properties":{
      "code_nodes":{"type":"boolean","default":false},
      "remote_sandbox":{"type":"boolean","default":false},
      "advanced_scripts":{"type":"boolean","default":false}}},
    "tracing":{"type":"object","properties":{
      "retention_days":{"type":"integer","default":30},
      "langfuse_otlp_url":{"type":"string"}}}
  }
}
```

---

## 9. How the three consumers use these (rules)

1. **Validator** (`POST .../validate`, `PUT .../canvas`): resolve `$ref`s, validate each `config` against its schema, then run the extra workflow rules (Section 2) and reference-existence checks. Return field-pointer errors (`/nodes/3/config/model: required`).
2. **Compiler** (Doc 2): trusts validated configs; `NODE_REGISTRY[type].factory(config)` and `MW_BUILDERS[type](config)` consume the exact shapes above. State -> `TypedDict`; tool args -> Pydantic/JSON Schema.
3. **`<SchemaForm>`** (Doc 3 §8): renders fields by `x-ui` (or the type-default mapping in §0). `oneOf`/`allOf`+`if` become kind-switchers. `showWhen` toggles visibility. `modelView` shows the "the model sees this" hint. `tokenMeter` renders the Raw-vs-Projected meter. The node **palette** is generated from the registry; node **summaries** on the canvas come from a per-type `summarize(config)` function (small, hand-written per node type).

## 10. Extension checklist (add a capability without UI work)
- **New node type:** add `schemas/nodes/<type>.json` + `NodeSpec` (ports, factory, allows_cycle) + a `summarize(config)`. Palette + form appear automatically.
- **New middleware:** add a `config_schemas.<type>` entry + a `MW_BUILDERS.<type>` builder + a `category-map` entry. It shows up in the middleware catalog.
- **New tool kind:** add a `$defs.<Kind>Config` + an `if/then` branch + a materializer in `tools/`.
- **New auth kind:** add a `$defs.<Kind>` + an `if/then` branch + a resolver branch.

Keep these schemas versioned with the app; when a schema changes shape, bump the workflow/tool `version` and write a migration for stored configs.
