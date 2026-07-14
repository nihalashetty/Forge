"""ORM entities (Doc 2 §4, focused subset for the current phases).

All leaf tables carry `tenant_id` for multi-tenant scoping (RLS is added with
Postgres in prod; SQLite uses query-level scoping). JSON columns hold the
schema-validated config / canvas / executable documents.

Note: `metadata` is reserved by SQLAlchemy's declarative Base, so the JSON column
is exposed as the `meta` attribute (DB column name "metadata").
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from forge.db.base import Base, PkTimestamp


class Tenant(PkTimestamp, Base):
    __tablename__ = "tenants"
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(50), default="free")
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class User(PkTimestamp, Base):
    __tablename__ = "users"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(30), default="owner")  # owner|admin|editor|viewer
    status: Mapped[str] = mapped_column(String(20), default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)


class Project(PkTimestamp, Base):
    __tablename__ = "projects"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|draft
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Public, safe-to-embed key for the chat widget (Phase 3b/4), indexed for O(1) lookup by
    # the public /v1/embed/{key} routes. None until embedding is enabled; the rest of the embed
    # settings (enabled, allowed_origins, workflow_id) live in config["embed"].
    embed_key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)


class Workflow(PkTimestamp, Base):
    __tablename__ = "workflows"
    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    canvas: Mapped[dict] = mapped_column(JSON, default=dict)        # React Flow round-trip (UI-owned)
    executable: Mapped[dict] = mapped_column(JSON, default=dict)    # compiler input (backend-owned)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    active_version: Mapped[int] = mapped_column(Integer, default=1)


class Tool(PkTimestamp, Base):
    __tablename__ = "tools"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(20))  # rest_api|graphql|code|mcp|builtin
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    auth_provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_tested: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pass|fail|untested


class Component(PkTimestamp, Base):
    """A user-authored UI component (Feature 2 - generative UI): saved HTML + CSS,
    declarative button `actions`, and a JSON-Schema for the `props` the agent supplies.
    Attached to agents like tools (agent config["components"]); at runtime each becomes a
    widget-tool that, when called, emits a `component` stream frame for the client to
    render - so the markup never enters the model's token stream, only the props do."""

    __tablename__ = "components"
    # The name is used verbatim as the LLM tool name → unique per project so two components
    # can't shadow each other's widget (audit M2). Enforced on fresh DBs; the router also
    # pre-checks for the existing-table case (create_all won't add a constraint after the fact).
    __table_args__ = (UniqueConstraint("tenant_id", "project_id", "name", name="uq_component_tenant_project_name"),)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    props_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    html: Mapped[str] = mapped_column(Text, default="")
    css: Mapped[str] = mapped_column(Text, default="")
    actions: Mapped[list] = mapped_column(JSON, default=list)
    sample_props: Mapped[dict] = mapped_column(JSON, default=dict)
    kind: Mapped[str] = mapped_column(String(20), default="html")  # html (sandboxed) | declarative (future)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Agent(PkTimestamp, Base):
    __tablename__ = "agents"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # validated vs forge/nodes/agent
    version: Mapped[int] = mapped_column(Integer, default=1)
    # Creator attribution (denormalized email snapshot for display without a join).
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AuthProvider(PkTimestamp, Base):
    __tablename__ = "auth_providers"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    credentials_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Secret(PkTimestamp, Base):
    __tablename__ = "secrets"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    kind: Mapped[str] = mapped_column(String(40), default="generic")
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)


class KbSource(PkTimestamp, Base):
    __tablename__ = "kb_sources"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # text|url|file|s3|api
    name: Mapped[str] = mapped_column(String(300))
    folder: Mapped[str] = mapped_column(String(200), default="", server_default="")  # "" = unfiled
    uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|ready|error
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)

    @property
    def chunking_strategy(self) -> str | None:
        """Chunking strategy used at ingest (recursive|section|sentence); lives in meta."""
        return (self.meta or {}).get("chunk_strategy")

    @property
    def chunk_size(self) -> int | None:
        """Target chunk size (chars) used at ingest; lives in meta, None until first ingest."""
        v = (self.meta or {}).get("chunk_size")
        return int(v) if v is not None else None

    @property
    def chunk_overlap(self) -> int | None:
        """Chunk overlap (chars) used at ingest; lives in meta, None until first ingest."""
        v = (self.meta or {}).get("chunk_overlap")
        return int(v) if v is not None else None


class QaPair(PkTimestamp, Base):
    __tablename__ = "qa_pairs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="faq")  # faq|error_workaround
    tags: Mapped[list] = mapped_column(JSON, default=list)
    q_embedding: Mapped[list] = mapped_column(JSON, default=list)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)


class McpClient(PkTimestamp, Base):
    __tablename__ = "mcp_clients"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    transport: Mapped[str] = mapped_column(String(20), default="http")
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    headers_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    disabled_tools: Mapped[list] = mapped_column(JSON, default=list)  # remote tool names toggled off in the External MCP tab


class Thread(PkTimestamp, Base):
    __tablename__ = "threads"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    user_external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lg_thread_id: Mapped[str] = mapped_column(String(100))  # passed to LangGraph configurable.thread_id
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Run(PkTimestamp, Base):
    __tablename__ = "runs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    thread_id: Mapped[str] = mapped_column(String(36), index=True)
    # Where this run originated, for the Traces conversation view. Set at create_run by each
    # caller: playground|api|embed|channel_email|channel_teams|webhook|schedule (assistant runs
    # have no Run row). Copied onto the Trace at finalize.
    source: Mapped[str] = mapped_column(String(40), default="playground")
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|interrupted|done|error
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class Trace(PkTimestamp, Base):
    __tablename__ = "traces"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="running")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # --- Conversation view (Traces): one Trace = one turn; group by thread_id for a session. ---
    # Origin of the turn (copied from Run.source; assistant turns = "assistant").
    # Indexed: the Traces filter facets run a DISTINCT over this column.
    source: Mapped[str] = mapped_column(String(40), default="playground", index=True)
    # Display + filter label: "System" for playground/test/assistant, else the end user's name,
    # else "Unknown user". Denormalized so the conversation list is a single Trace query.
    actor: Mapped[str] = mapped_column(String(300), default="System", index=True)
    # Stable end-user id (disambiguates same-named users); None for anonymous / system.
    end_user_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # This turn's user message and the AI's response, captured from live state at finalize.
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Trigger(PkTimestamp, Base):
    """An event-driven entry point synced from a workflow's trigger nodes. The
    dispatcher (webhook route / scheduler / inbound email / chat) fires runs from these."""

    __tablename__ = "triggers"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str] = mapped_column(String(36), index=True)
    node_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))  # webhook_in|schedule|email_in|chat_in|app_event
    key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)  # webhook URL key
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fired_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Runtime state (e.g. app_event dedupe cursor / seen ids); NOT synced from the node.
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Channel(PkTimestamp, Base):
    """A deployment surface that feeds a workflow: email mailbox or Microsoft Teams bot.
    `config` holds type-specific settings (secret refs for SMTP/IMAP or Teams app creds).
    `key` is the public, unguessable id used in inbound endpoint URLs (teams/email-inbound)."""

    __tablename__ = "channels"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    type: Mapped[str] = mapped_column(String(20))  # email|teams
    name: Mapped[str] = mapped_column(String(120))
    key: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class HandoffRequest(PkTimestamp, Base):
    """A conversation escalated to a human (live-agent handoff). The run is paused at a
    `handoff` interrupt; an agent replies via the inbox, which resumes the run and pushes
    the answer back over the originating channel."""

    __tablename__ = "handoff_requests"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    thread_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    customer: Mapped[str | None] = mapped_column(String(300), nullable=True)  # email / display name
    customer_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|answered|closed
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reply_context: Mapped[dict] = mapped_column(JSON, default=dict)


class Memory(PkTimestamp, Base):
    """A long-term memory an agent stored - facts that should persist across threads
    (vs. the per-thread checkpointer). Recalled by semantic search; scoped per project
    (+ optional `scope` for per-user/per-conversation memory)."""

    __tablename__ = "memories"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    scope: Mapped[str] = mapped_column(String(120), default="default", index=True)
    text: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="note")


class ModelPrice(PkTimestamp, Base):
    """Admin-editable per-model pricing override (USD per 1M tokens). Overlays the
    built-in defaults in tracing/pricing.py so rates can be corrected without a deploy."""

    __tablename__ = "model_prices"
    model: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    input_per_1m: Mapped[float] = mapped_column(Float, default=0.0)
    output_per_1m: Mapped[float] = mapped_column(Float, default=0.0)


class Dataset(PkTimestamp, Base):
    """An evaluation dataset: inputs + expected outputs run against a workflow to score
    quality and catch regressions before publish."""

    __tablename__ = "datasets"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    score_mode: Mapped[str] = mapped_column(String(20), default="contains")  # contains|exact|regex|judge
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{input, expected}]
    last_pass_rate: Mapped[float | None] = mapped_column(Float, nullable=True)


class AuditLog(PkTimestamp, Base):
    """Append-only audit trail: who did what, when (Doc 2 §12). Written for auth
    events, secret reads, and create/update/delete of every resource. Never updated
    or deleted in normal operation."""

    __tablename__ = "audit_logs"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)  # e.g. auth.login, secret.read, workflow.delete
    resource_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|denied|error
    meta: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class EntityVersion(PkTimestamp, Base):
    """Immutable point-in-time snapshot of a versionable entity's config, captured on each
    save so a user can view history and restore a prior version. Generic across entity types
    (workflow|agent|tool|component|auth_provider|kb_source|project) - the `snapshot` JSON holds
    the entity's restorable fields. Retention is pruned to the configured version_history_limit
    per (entity_type, entity_id). See forge.services.versions."""

    __tablename__ = "entity_versions"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "version_no", name="uq_entity_version"),
    )
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)  # entity name at snapshot time / note
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class Span(PkTimestamp, Base):
    __tablename__ = "spans"
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    trace_id: Mapped[str] = mapped_column(String(36), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20))  # llm|tool|chain|retriever|agent|node|subagent
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
