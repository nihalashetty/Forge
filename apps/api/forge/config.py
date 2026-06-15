"""Application settings — environment-driven (pydantic-settings).

Local defaults need **no external infra** (SQLite + embedded Chroma + in-process cache).
Every value can be overridden via `.env` or real env vars; the production swaps
(Postgres, Redis, Vault) are pure configuration changes — no code changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo layout: apps/api/forge/config.py -> parents[3] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SCHEMAS_DIR = REPO_ROOT / "packages" / "schemas"
_DEFAULT_DATA_DIR = API_ROOT / ".data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=(API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- App ---
    app_name: str = "Forge"
    environment: str = "development"
    debug: bool = True
    api_v1_prefix: str = "/v1"

    # --- Persistence (SQLite default; Postgres is a config-only swap) ---
    database_url: str = Field(
        default_factory=lambda: f"sqlite+aiosqlite:///{(_DEFAULT_DATA_DIR / 'forge.db').as_posix()}"
    )
    # LangGraph durable-execution checkpointer (sqlite file or 'memory').
    checkpoint_db: str = Field(
        default_factory=lambda: (_DEFAULT_DATA_DIR / "checkpoints.sqlite").as_posix()
    )

    # --- Vectors (user-mandated: Chroma; embedded persistent client) ---
    chroma_path: str = Field(default_factory=lambda: (_DEFAULT_DATA_DIR / "chroma").as_posix())

    # --- Cache / queue (in-process locally; Redis in prod) ---
    redis_url: str | None = None  # None => in-process fakes

    # --- Secrets (Fernet master key; file-backed locally, KMS/Vault in prod) ---
    secret_key_file: str = Field(default_factory=lambda: (_DEFAULT_DATA_DIR / "master.key").as_posix())

    # --- Platform auth (JWT) ---
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24
    refresh_token_ttl_days: int = 30
    # Auth is ON by default — the app behaves like production (real login required), so the
    # flow is actually exercised in dev. The seeded owner (bootstrap_admin_email/password
    # below) lets you log in immediately; self-service signup creates additional workspaces.
    # (Public surfaces — webhooks/MCP/OAuth callback — authenticate by their own key,
    # never the JWT, so they keep working regardless.)
    auth_required: bool = True
    # Allow open self-service signup. When False, only an existing admin can invite users.
    allow_open_signup: bool = True
    # Public base URL of THIS API (for OAuth redirect URIs + channel webhooks). Must match
    # what you register with each OAuth provider, e.g. https://forge.yourco.com.
    public_base_url: str = "http://localhost:8000"
    # Public URL of the web console (where the SPA is served). Used to build invite links
    # emailed to new teammates, e.g. https://app.forge.yourco.com.
    public_console_url: str = "http://localhost:3000"

    # --- Outbound email (SMTP) — used for team invites & notifications. When smtp_host is
    # unset, email sending is a no-op and the API returns the invite link so an admin can
    # share it manually. Point at any SMTP relay (Postmark/SendGrid/SES/Mailgun/etc.). ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_use_tls: bool = True
    smtp_from: str = "Forge <no-reply@forge.local>"
    # The seeded workspace owner. The default password lets you log in straight away in dev
    # (email: you@forge.local · password: forge-admin); CHANGE IT in production (the prod
    # guard rejects the default password).
    bootstrap_admin_email: str = "you@forge.local"
    bootstrap_admin_password: str | None = "forge-admin"

    # --- Schemas (shared contract, packages/schemas) ---
    schemas_dir: str = Field(default_factory=lambda: _DEFAULT_SCHEMAS_DIR.as_posix())

    # --- Tools ---
    # Code tools run RestrictedPython (AST-sandboxed) but not OS-isolated; disable in
    # untrusted multi-tenant installs until an isolated executor is configured.
    enable_code_tools: bool = True
    # Prune the assistant's ~19 tools to the relevant subset per turn (cuts tool-schema
    # tokens). Opt-in: the selection itself is an extra model call, so it's a tradeoff.
    assistant_tool_selector: bool = False
    # Hard cap on a tool response handed to the model when no projection trims it
    # (token-cost guard). 0 = no cap.
    max_tool_response_chars: int = 20000
    # Auto-attach AnthropicPromptCachingMiddleware to Anthropic-model agents (caches the
    # static system-prompt/tools prefix; large multi-turn cost saving). Off => opt-in only.
    default_anthropic_prompt_caching: bool = True

    # --- Models ---
    default_model: str = "fake:echo"  # offline-safe default; set a real provider model in prod
    request_timeout_seconds: int = 600

    # LangGraph checkpoint durability for runs: "async" (default — persist while the
    # next step executes), "sync" (persist before next step), or "exit" (persist only
    # at the end; fastest, but HITL interrupts mid-run rely on per-step checkpoints,
    # so keep async/sync when using human_input nodes).
    run_durability: str = "async"

    # --- Observability (OpenTelemetry export; point at an OTLP collector or Langfuse) ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "forge"

    # In-process scheduler for `schedule` triggers (fires due schedules once a minute).
    # Disable in multi-worker deployments and run a single global scheduler instead.
    enable_scheduler: bool = True

    # Seed demo data (projects/tools/auth) on first run. Off => start from an empty
    # workspace and create projects yourself. Set FORGE_SEED_DEMO=true to populate.
    seed_demo: bool = False

    # --- CORS ---
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- Egress / SSRF guard (applies to REST/GraphQL tools, webhooks, web_fetch,
    # URL ingestion, and auth/OAuth token fetches). block_private rejects URLs that
    # resolve to private/loopback/link-local/metadata addresses. allow/deny host
    # lists match a host or any parent domain (e.g. "example.com" covers
    # "api.example.com"). Per-project overrides live in project.config.egress. ---
    egress_block_private: bool = True
    egress_allow_hosts: list[str] = []
    egress_deny_hosts: list[str] = []

    # --- Rate limits / quotas (per tenant). 0 = unlimited. Per-tenant overrides may
    # live in tenant.settings (max_runs_per_minute / max_runs_per_day). ---
    run_rate_limit_per_minute: int = 60
    api_rate_limit_per_minute: int = 240

    @property
    def data_dir(self) -> Path:
        return _DEFAULT_DATA_DIR

    def ensure_dirs(self) -> None:
        _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.chroma_path).mkdir(parents=True, exist_ok=True)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    def validate_production(self) -> list[str]:
        """Return a list of fatal misconfigurations for a production deployment.
        Called at startup; an install with any of these should refuse to serve."""
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.jwt_secret in ("", "dev-insecure-change-me"):
            problems.append("FORGE_JWT_SECRET is unset/default — set a strong random secret.")
        if not self.auth_required:
            problems.append("FORGE_AUTH_REQUIRED must be true in production.")
        if self.bootstrap_admin_password == "forge-admin":
            problems.append("FORGE_BOOTSTRAP_ADMIN_PASSWORD is the dev default — set a real one.")
        if not self.egress_block_private:
            problems.append("FORGE_EGRESS_BLOCK_PRIVATE must stay true in production (SSRF guard).")
        if self.database_url.startswith("sqlite"):
            problems.append("SQLite is not supported in production — set a Postgres FORGE_DATABASE_URL.")
        return problems


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
