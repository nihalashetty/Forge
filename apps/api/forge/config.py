"""Application settings - environment-driven (pydantic-settings).

Local defaults need **no external infra** (SQLite + embedded Chroma + in-process cache).
Every value can be overridden via `.env` or real env vars; the production swaps
(Postgres, Redis, Vault) are pure configuration changes - no code changes.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Repo layout: apps/api/forge/config.py -> parents[3] == repo root. In the container the
# tree is flattened to /app/forge, so parents[3] doesn't exist; fall back to API_ROOT (=/app).
# The Docker image bakes the schemas at /app/packages/schemas (apps/api/Dockerfile:
# `COPY packages/schemas ./packages/schemas`), so the default _DEFAULT_SCHEMAS_DIR
# (/app/packages/schemas) resolves with no FORGE_SCHEMAS_DIR override. Set FORGE_SCHEMAS_DIR
# only to point at an out-of-tree schemas copy.
_HERE = Path(__file__).resolve()
API_ROOT = _HERE.parents[1]
REPO_ROOT = _HERE.parents[3] if len(_HERE.parents) > 3 else API_ROOT
_DEFAULT_SCHEMAS_DIR = REPO_ROOT / "packages" / "schemas"
_DEFAULT_DATA_DIR = API_ROOT / ".data"


def _as_str_list(v: object) -> list[str]:
    """Parse a list-of-strings setting from env leniently: a JSON array (["a","b"]), a
    comma-separated string (a,b), or blank ("" -> []). Env/compose quoting makes strict-JSON
    list fields brittle - a stray bracket or space (e.g. from a ${VAR:-[]} interpolation)
    otherwise crashes startup - so we normalize here instead of requiring valid JSON."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("["):
        import json as _json

        try:
            parsed = _json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed]
        except ValueError:
            pass  # not valid JSON (e.g. unquoted, or a mangled "[") -> strip brackets + split
        s = s.strip("[]")
    return [p.strip().strip('"').strip("'") for p in s.split(",") if p.strip().strip('"').strip("'")]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        # ONE .env, at the repo root - the same file docker-compose reads for ${...}
        # substitution - so both run modes (.venv api and the Docker stack) are configured in a
        # single place. In the flattened container image REPO_ROOT == /app (no .env is copied
        # there); env then comes from the compose `environment:` block and pydantic simply skips
        # the missing file. Real env vars still take precedence over the file either way.
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # List-of-strings settings are marked NoDecode (skip pydantic-settings' strict JSON decode)
    # and parsed here so env/compose values may be JSON, comma-separated, or blank - see
    # _as_str_list. Keeps a stray bracket/space from an interpolated default from crashing boot.
    @field_validator(
        "jwt_secret_previous", "cors_origins", "egress_allow_hosts", "egress_deny_hosts",
        "egress_allow_private_hosts", "trusted_proxies", "trusted_hosts", mode="before",
    )
    @classmethod
    def _parse_str_lists(cls, v: object) -> list[str]:
        return _as_str_list(v)

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
    # Cache dir for the local fastembed embedder's model files. Set to a baked path in the
    # Docker image (see apps/api/Dockerfile) so the default model ships with the image - no
    # first-run download / network dependency. None -> fastembed's own default (a temp dir),
    # fine for local dev.
    fastembed_cache_dir: str | None = None

    # --- Cache / queue (in-process locally; Redis in prod) ---
    redis_url: str | None = None  # None => in-process fakes

    # --- Secrets (Fernet master key; file-backed locally, KMS/Vault in prod) ---
    secret_key_file: str = Field(default_factory=lambda: (_DEFAULT_DATA_DIR / "master.key").as_posix())

    # --- Platform auth (JWT) ---
    jwt_secret: str = "dev-insecure-change-me"
    # Previously-active signing secrets, still ACCEPTED for verification (not for minting),
    # so you can rotate `jwt_secret` without invalidating every live token: set the new key
    # here-as-previous during the overlap window, then drop it. Tokens carry a `kid` header.
    jwt_secret_previous: Annotated[list[str], NoDecode] = []
    jwt_key_id: str = "k1"
    jwt_algorithm: str = "HS256"
    # Shorter access-token lifetime bounds the blast radius of a leaked token (audit S11);
    # the 30-day refresh token (rotated on use) keeps sessions alive without re-login.
    access_token_ttl_minutes: int = 60 * 8
    refresh_token_ttl_days: int = 30
    # Static service token for trusted server-to-server integrations (e.g. an app backend that
    # drives runs on behalf of its users). Sent as `Authorization: Bearer <token>`; when it
    # matches, the request authenticates as a least-privilege (editor) service identity in the
    # seeded workspace - no expiry, revoke by rotating this value. Empty = disabled. This is the
    # outer "is this call from our backend?" barrier; per-user / per-API auth is handled
    # separately (e.g. session+CSRF injected per-run into tools). Keep it long, random, secret.
    service_api_token: str = ""
    # Auth is ON by default - the app behaves like production (real login required), so the
    # flow is actually exercised in dev. The seeded owner (bootstrap_admin_email/password
    # below) lets you log in immediately; self-service signup creates additional workspaces.
    # (Public surfaces - webhooks/MCP/OAuth callback - authenticate by their own key,
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

    # --- Outbound email (SMTP) - used for team invites & notifications. When smtp_host is
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
    # Code tools run RestrictedPython (AST-sandboxed) but NOT OS-isolated: no CPU/memory
    # bound and a runaway thread can't be force-killed. RestrictedPython is a hardening
    # layer, not a sandbox, so it is OFF by default. Only enable it on a trusted, single-
    # tenant install, or once an isolated executor (subprocess/container/gVisor) is wired
    # in. The production guard refuses to boot with this on unless explicitly acknowledged.
    enable_code_tools: bool = False
    # Set true to run code tools in production despite the lack of OS isolation (you accept
    # the in-process RCE/DoS risk - e.g. a trusted single-tenant deployment).
    allow_unsandboxed_code_tools: bool = False
    # Prune the assistant's ~19 tools to the relevant subset per turn (cuts tool-schema
    # tokens). Opt-in: the selection itself is an extra model call, so it's a tradeoff.
    assistant_tool_selector: bool = False
    # Hard cap on a tool response handed to the model when no projection trims it
    # (token-cost guard). 0 = no cap.
    max_tool_response_chars: int = 20000
    # Default per-request timeout (seconds) for REST/HTTP tools when the tool config doesn't
    # set its own `timeout_seconds`.
    tool_request_timeout_seconds: int = 30
    # Max redirect hops a REST tool follows when follow_redirects is on. Each hop is
    # re-validated against the SSRF egress guard, so this bounds a redirect loop / chain.
    tool_max_redirects: int = 5
    # Auto-attach AnthropicPromptCachingMiddleware to Anthropic-model agents (caches the
    # static system-prompt/tools prefix; large multi-turn cost saving). Off => opt-in only.
    default_anthropic_prompt_caching: bool = True

    # --- Models ---
    default_model: str = "fake:echo"  # offline-safe default; set a real provider model in prod
    request_timeout_seconds: int = 600

    # LangGraph checkpoint durability for runs: "async" (default - persist while the
    # next step executes), "sync" (persist before next step), or "exit" (persist only
    # at the end; fastest, but HITL interrupts mid-run rely on per-step checkpoints,
    # so keep async/sync when using human_input nodes).
    run_durability: str = "async"

    # --- Observability (OpenTelemetry export; point at an OTLP collector or Langfuse) ---
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "forge"
    # Expose the unauthenticated /metrics (Prometheus counters) and /version (dependency
    # versions) endpoints. OFF by default: these are an internal operational surface that also
    # aids fingerprinting, so enable only where the scrape endpoint sits on a trusted network.
    expose_metrics: bool = False

    # --- Tool I/O in traces (debug what an agent actually sent a tool) ---
    # Master switch: capture per-tool-call input/output on trace spans (the LLM's tool args,
    # and for REST tools the FRAMED request - method, resolved URL, query, headers, cookies,
    # body - plus the response status/latency/body). Lets you see whether the agent attached
    # proper input, and why a call that "works in test" 401s in a run (e.g. a {{ctx.*}} cookie
    # that never arrived and was silently dropped). Admin-only dashboard surface.
    trace_tool_io: bool = True
    # A REST request/response captured for a trace can contain LIVE session cookies, CSRF
    # tokens, and Authorization headers. Off (default) stores full values for debugging; set
    # true on a shared/production install to MASK the values of sensitive headers/cookies
    # (presence + length kept, e.g. "••• (32 chars)") so secrets aren't persisted in traces.
    trace_tool_io_redact: bool = False
    # Per-field clip so a large body/response can't bloat the spans table. 0 = no cap.
    trace_tool_io_max_chars: int = 20000

    # In-process scheduler for `schedule` triggers (fires due schedules once a minute).
    # OFF by default: with more than one replica each would fire every schedule (duplicate
    # runs). Enable it on EXACTLY ONE instance (set FORGE_SCHEDULER_LEADER=true there), or
    # run a dedicated single scheduler/worker. `enable_scheduler` is the master switch;
    # `scheduler_leader` lets you ship the same image everywhere and elect one leader by env.
    enable_scheduler: bool = False
    scheduler_leader: bool = True

    # Seed demo data (projects/tools/auth) on first run. Off => start from an empty
    # workspace and create projects yourself. Set FORGE_SEED_DEMO=true to populate.
    seed_demo: bool = False

    # --- CORS ---
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # --- Egress / SSRF guard (applies to REST/GraphQL tools, webhooks, web_fetch,
    # URL ingestion, and auth/OAuth token fetches). block_private rejects URLs that
    # resolve to private/loopback/link-local/metadata addresses. allow/deny host
    # lists match a host or any parent domain (e.g. "example.com" covers
    # "api.example.com"). Per-project overrides live in project.config.egress. ---
    egress_block_private: bool = True
    egress_allow_hosts: Annotated[list[str], NoDecode] = []
    egress_deny_hosts: Annotated[list[str], NoDecode] = []
    # Hosts permitted to resolve to a PRIVATE / loopback / link-local address even while
    # block_private is on (default-deny, explicit-allow). Use for trusted internal targets -
    # localhost during dev, an internal service, an on-prem host - WITHOUT disabling the SSRF
    # guard globally (so the app still boots in production). Matches a host or any parent
    # domain. Per-project override: project.config.egress.allow_private_hosts.
    egress_allow_private_hosts: Annotated[list[str], NoDecode] = []

    # --- Rate limits / quotas (per tenant). 0 = unlimited. Per-tenant overrides may
    # live in tenant.settings (max_runs_per_minute / max_runs_per_day). ---
    run_rate_limit_per_minute: int = 60
    api_rate_limit_per_minute: int = 240

    # --- Public embed surface (anonymous, browser-facing). The publishable key is PUBLIC
    # by design, so these are the real abuse/cost ceilings. Per-IP is the important one
    # (a single key is shared by every visitor). 0 = unlimited. The daily tenant quota
    # (above) is ALSO enforced on the embed path. ---
    embed_rate_limit_per_minute: int = 60          # per publishable key
    embed_rate_limit_per_ip_per_minute: int = 20   # per client IP (denial-of-wallet guard)
    embed_stream_limit_per_ip_per_minute: int = 60 # SSE connections per IP

    # Max concurrent in-flight runs per tenant (0 = unlimited). Backpressure / noisy-
    # neighbour guard for the inline execution path until the worker tier is enabled.
    max_concurrent_runs_per_tenant: int = 20

    # How many recent turns (Trace rows) the Traces conversation list scans before grouping
    # by thread in Python. Bounds the query regardless of retention; raise it for projects
    # with very deep history at the cost of a wider scan.
    conversation_scan_limit: int = 2000

    # Reverse-proxy IPs whose X-Forwarded-For we trust for client-IP derivation. Empty =>
    # trust none (use the socket peer). Set to your LB/ingress IPs in production so clients
    # can't spoof their IP for per-IP rate limits / audit. "*" trusts any (only behind a
    # trusted ingress that always overwrites XFF).
    trusted_proxies: Annotated[list[str], NoDecode] = []

    # Host allow-list for the API (TrustedHostMiddleware). Empty => allow any (dev).
    trusted_hosts: Annotated[list[str], NoDecode] = []

    # --- LangGraph checkpointer backend: "sqlite" (default, dev), "memory" (ephemeral),
    # or "postgres" (durable, shared across workers - required for prod/HITL). When
    # "postgres", set FORGE_CHECKPOINT_POSTGRES_URL (or it falls back to database_url). ---
    checkpoint_backend: str = "sqlite"
    checkpoint_postgres_url: str | None = None

    @property
    def data_dir(self) -> Path:
        return _DEFAULT_DATA_DIR

    def ensure_dirs(self) -> None:
        _DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.chroma_path).mkdir(parents=True, exist_ok=True)

    # Environments treated as local/insecure-OK. ANY other value (staging, prod, an
    # unknown string, or the empty string) is treated as security-enforced - so a
    # misconfigured/typo'd FORGE_ENVIRONMENT fails CLOSED rather than silently skipping
    # every guard.
    _DEV_ENVIRONMENTS = ("development", "dev", "local", "test")

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in ("production", "prod")

    @property
    def enforce_security(self) -> bool:
        """True when the deployment must pass the hardening checks. Only the explicit
        local-dev environment names opt out; everything else fails closed."""
        return self.environment.lower() not in self._DEV_ENVIRONMENTS

    def validate_production(self) -> list[str]:
        """Return a list of FATAL misconfigurations. Called at startup; an install with
        any of these should refuse to serve. Enforced for every non-dev environment
        (fail-closed), not just literal 'production'."""
        problems: list[str] = []
        if not self.enforce_security:
            return problems
        if self.jwt_secret in ("", "dev-insecure-change-me"):
            problems.append("FORGE_JWT_SECRET is unset/default - set a strong random secret.")
        if not self.auth_required:
            problems.append("FORGE_AUTH_REQUIRED must be true outside local development.")
        if self.bootstrap_admin_password == "forge-admin":
            problems.append("FORGE_BOOTSTRAP_ADMIN_PASSWORD is the dev default - set a real one.")
        if not self.egress_block_private:
            problems.append("FORGE_EGRESS_BLOCK_PRIVATE must stay true outside dev (SSRF guard).")
        if self.database_url.startswith("sqlite"):
            problems.append("SQLite is not supported outside dev - set a Postgres FORGE_DATABASE_URL.")
        if self.checkpoint_backend not in ("postgres",):
            problems.append(
                "FORGE_CHECKPOINT_BACKEND must be 'postgres' outside dev - a sqlite/memory "
                "checkpointer loses run/HITL state on restart and can't be shared across workers."
            )
        if self.enable_code_tools and not self.allow_unsandboxed_code_tools:
            problems.append(
                "FORGE_ENABLE_CODE_TOOLS is on but code execution is not OS-isolated. Disable it, "
                "or set FORGE_ALLOW_UNSANDBOXED_CODE_TOOLS=true to explicitly accept the RCE/DoS risk."
            )
        return problems

    def startup_warnings(self) -> list[str]:
        """Non-fatal but dangerous configuration, logged loudly at startup regardless of
        environment so an insecure local default is never silently shipped."""
        warns: list[str] = []
        if self.jwt_secret == "dev-insecure-change-me":
            warns.append("JWT secret is the built-in dev default - tokens are forgeable. Set FORGE_JWT_SECRET.")
        if self.bootstrap_admin_password == "forge-admin":
            warns.append("Bootstrap admin password is the dev default. Set FORGE_BOOTSTRAP_ADMIN_PASSWORD.")
        if not self.auth_required:
            warns.append("auth_required is false - unauthenticated requests act as the workspace owner.")
        if self.enable_code_tools:
            warns.append("Code tools are enabled and run unsandboxed (RestrictedPython only).")
        if self.environment.lower() not in (*self._DEV_ENVIRONMENTS, "production", "prod", "staging"):
            warns.append(f"Unrecognized FORGE_ENVIRONMENT={self.environment!r} - treated as security-enforced.")
        return warns


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
