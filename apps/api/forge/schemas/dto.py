"""Pydantic request/response DTOs for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- projects ---
class ProjectOut(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    status: str
    config: dict = {}


class ProjectCountsOut(BaseModel):
    """Per-resource counts for the project sidebar badges."""
    workflows: int
    agents: int
    tools: int
    components: int
    knowledge: int
    auth: int
    handoffs: int


class ProjectCreate(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    config: dict | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


# --- traces ---
class TraceOut(ORMModel):
    id: str
    run_id: str
    workflow_id: str | None = None
    name: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    latency_ms: int
    total_tokens: int
    total_cost_usd: float


class SpanOut(ORMModel):
    id: str
    parent_span_id: str | None = None
    name: str
    kind: str
    latency_ms: int
    input: Any = None
    output: Any = None
    model: str | None = None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None


class TraceDetailOut(BaseModel):
    trace: TraceOut
    spans: list[SpanOut]


# --- conversations (Traces view: sessions grouped by end user) ---
class ConversationOut(ORMModel):
    thread_id: str
    actor: str
    source: str
    end_user_id: str | None = None
    workflow_id: str | None = None
    turns: int
    total_tokens: int
    total_cost_usd: float
    started_at: datetime | None = None
    last_activity: datetime | None = None
    status: str
    preview: str = ""


class TurnOut(BaseModel):
    trace_id: str
    run_id: str
    source: str
    user_message: str | None = None
    ai_response: str | None = None
    status: str
    error: str | None = None
    latency_ms: int
    total_tokens: int
    total_cost_usd: float
    started_at: datetime | None = None


class ConversationDetailOut(BaseModel):
    conversation: ConversationOut
    turns: list[TurnOut]


class FacetsOut(BaseModel):
    actors: list[str]
    sources: list[str]


# --- workflows ---
class WorkflowOut(ORMModel):
    id: str
    project_id: str
    name: str
    description: str | None = None
    status: str
    active_version: int
    executable: dict = {}
    canvas: dict = {}


class WorkflowCreate(BaseModel):
    name: str
    description: str | None = None
    executable: dict | None = None
    canvas: dict | None = None


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ExecutableIn(BaseModel):
    executable: dict


class CanvasSaveIn(BaseModel):
    canvas: dict
    executable: dict


class ValidateOut(BaseModel):
    valid: bool
    errors: list[dict] = []
    # Non-blocking wiring problems (e.g. a router branching on a state key nothing writes).
    warnings: list[dict] = []


# --- agents (reusable presets) ---
class AgentOut(ORMModel):
    id: str
    project_id: str
    name: str
    version: int
    config: dict = {}
    created_by: str | None = None
    created_by_email: str | None = None


class AgentCreate(BaseModel):
    name: str
    config: dict = {}


class AgentUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None


# --- runs ---
class EndUser(BaseModel):
    """The end user a run acts for (identity). Generic + app-defined - put any custom claims
    in `attributes`. Set server-to-server by the integrator's authenticated backend on the
    run-create body, or minted into a verified session token for the browser widget (3b)."""

    id: str
    display_name: str | None = None
    email: str | None = None
    roles: list[str] = Field(default_factory=list)
    entitlements: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class RunCreate(BaseModel):
    input: dict[str, Any] | None = None
    # Reuse an existing thread (its checkpointer state holds the conversation);
    # when set, `input` should contain only the NEW user message.
    thread_id: str | None = None
    # The end user this run acts for. Trusted because it's set server-to-server by the
    # integrator's authenticated backend. The browser widget instead sends `session_token`
    # (a verified, server-minted token), which takes precedence over any body end_user.
    end_user: EndUser | None = None
    session_token: str | None = None


class RunOut(ORMModel):
    id: str
    status: str
    thread_id: str


# --- tools ---
class ToolOut(ORMModel):
    id: str
    project_id: str
    name: str
    kind: str
    enabled: bool
    version: int
    auth_provider_id: str | None = None
    last_tested: str | None = None
    config: dict = {}


class ToolCreate(BaseModel):
    name: str
    kind: str
    config: dict = {}
    auth_provider_id: str | None = None


class ToolUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    auth_provider_id: str | None = None
    enabled: bool | None = None


class ToolTestIn(BaseModel):
    args: dict[str, Any] = {}
    context: dict[str, Any] | None = None


# --- auth providers ---
class AuthProviderOut(ORMModel):
    id: str
    project_id: str
    name: str
    kind: str
    credentials_ref: str | None = None
    config: dict = {}


class AuthProviderCreate(BaseModel):
    name: str
    kind: str
    config: dict = {}
    credentials_ref: str | None = None


class AuthProviderUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    config: dict | None = None
    credentials_ref: str | None = None


class AuthTestIn(BaseModel):
    context: dict[str, Any] | None = None


# --- secrets (write-only; value never returned) ---
class SecretOut(ORMModel):
    id: str
    name: str
    kind: str
    version: int


class SecretCreate(BaseModel):
    name: str
    value: Any
    kind: str = "generic"


# --- knowledge ---
class KbSourceOut(ORMModel):
    id: str
    project_id: str
    kind: str
    name: str
    folder: str = ""
    uri: str | None = None
    status: str
    chunks: int
    embedding_model: str | None = None
    chunking_strategy: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class RechunkIn(BaseModel):
    """Optional per-source chunking overrides applied before a re-ingest. Any field left
    None keeps the source's existing value (then the project's rag_defaults)."""

    chunking_strategy: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class RechunkBulkIn(RechunkIn):
    """Re-chunk a set of sources (multi-select) with one shared set of overrides."""

    source_ids: list[str]


class KbSourceCreate(BaseModel):
    kind: str = "text"  # text | url
    name: str
    folder: str = ""  # "" = unfiled; free-form folder names organize sources
    uri: str | None = None
    text: str | None = None
    # How to split this source into chunks: recursive (default) | section | sentence.
    # None -> falls back to the project's rag_defaults.chunking_strategy, then "recursive".
    chunking_strategy: str | None = None
    # Optional per-source ingest knobs stored on the source meta and read at ingest time -
    # e.g. for a crawl: {"max_pages": 50, "max_depth": 2, "crawl_delay": 0.5}.
    meta: dict | None = None


class QaPairOut(ORMModel):
    id: str
    question: str
    answer: str
    kind: str
    tags: list = []
    upvotes: int


class QaPairCreate(BaseModel):
    question: str
    answer: str
    kind: str = "faq"  # free-form category: faq, error_workaround, or any custom kind
    tags: list[str] = []


class KnowledgeSearchIn(BaseModel):
    query: str
    top_k: int = 5
    folders: list[str] | None = None
    # Opt into hybrid retrieval (BM25 lexical + vector, fused via RRF). Default is
    # vector-only. In hybrid mode the returned score is a normalized fusion rank,
    # NOT cosine similarity.
    hybrid: bool = False
    # Opt into a second-stage local cross-encoder rerank (see services.knowledge.search).
    # After rerank the score is the cross-encoder relevance (0..1), not cosine/fusion.
    rerank: bool = False


class KnowledgeMapIn(BaseModel):
    """Chunk-map visualizer request: project all stored chunk vectors to 2-D (PCA). An
    optional query overlays the query point + which chunks retrieval would return."""

    query: str | None = None
    folders: list[str] | None = None
    source_ids: list[str] | None = None
    limit: int = 400  # cap points projected/returned (perf); response flags `truncated`
    hybrid: bool = False
    rerank: bool = False
    top_k: int = 8  # how many chunks the query overlay marks as retrieved


class ResumeIn(BaseModel):
    value: Any = True


class ProjectRunIn(BaseModel):
    """One turn against a project's configured API workflow - the single server-to-server
    endpoint (POST /v1/projects/{id}/run). Framework-generic: any project, any auth scheme;
    per-user secrets travel out-of-band in the X-Forge-Context header, never in this body.

    - New turn: send `input` (+ `thread_id` to continue an existing conversation).
    - HITL: send `resume` to answer an interrupt the WORKFLOW raised - pausing is decided by
      the workflow, not the caller.
    - `stream` is the ONLY per-request knob: True => SSE frames, False => one JSON reply.
    """

    input: dict[str, Any] | None = None
    thread_id: str | None = None
    end_user: EndUser | None = None
    session_token: str | None = None
    resume: ResumeIn | None = None
    stream: bool = True


# --- node catalog ---
class PortOut(BaseModel):
    id: str
    io_type: str
    direction: str
    label: str | None = None
    required: bool = True
    many: bool = False


class NodeTypeOut(BaseModel):
    type: str
    category: str
    label: str
    description: str
    schema_id: str
    allows_cycle: bool
    input_ports: list[PortOut]
    output_ports: list[PortOut]
