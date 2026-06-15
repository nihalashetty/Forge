"""Bootstrap + optional demo seed.

`bootstrap` always ensures a single tenant (+ owner user) exists so the app has a
tenant context — but creates NO projects, so you start from an empty workspace and
build from scratch in the UI.

`seed_demo_data` (only when FORGE_SEED_DEMO=true) populates the showcase project,
tools, auth provider, and a runnable workflow.
"""

from __future__ import annotations

from sqlalchemy import select

from forge.models import AuthProvider, Project, Tenant, Tool, User, Workflow
from forge.secrets.store import SecretStore

SEED_EXECUTABLE: dict = {
    "id": "support_router",
    "version": 1,
    "state": {
        "messages": {"type": "list[message]", "reducer": "add_messages"},
        "intent": {"type": "str", "reducer": "last"},
    },
    "entry_node": "start",
    "global_middleware": [{"type": "model_call_limit", "config": {"run_limit": 25}}],
    "nodes": [
        {"id": "start", "type": "start", "config": {}, "position": {"x": 40, "y": 200}},
        {
            "id": "intent_router",
            "type": "router",
            "config": {
                "expression": "intent",
                "cases": {"billing": "billing_agent", "technical": "tech_agent"},
                "default": "billing_agent",
            },
            "position": {"x": 300, "y": 200},
        },
        {
            "id": "billing_agent",
            "type": "agent",
            "config": {
                "flavor": "agent",
                "name": "billing_agent",
                "model": "fake:Thanks — your billing question is resolved.",
                "system_prompt": "You are the billing support agent. Be concise and helpful.",
                "middleware": [
                    {"type": "summarization", "config": {"trigger": ["tokens", 4000]}},
                    {"type": "tool_call_limit", "config": {"run_limit": 3}},
                ],
            },
            "position": {"x": 600, "y": 110},
        },
        {
            "id": "tech_agent",
            "type": "agent",
            "config": {
                "flavor": "agent",
                "name": "tech_agent",
                "model": "fake:Here's how to fix your technical issue.",
                "system_prompt": "You are the technical support agent.",
            },
            "position": {"x": 600, "y": 300},
        },
        {"id": "end", "type": "end", "config": {}, "position": {"x": 900, "y": 200}},
    ],
    "edges": [
        {"source": "start", "target": "intent_router"},
        {"source": "billing_agent", "target": "end"},
        {"source": "tech_agent", "target": "end"},
    ],
}

SEED_CANVAS: dict = {
    "nodes": [
        {"id": n["id"], "type": n["type"], "position": n.get("position", {"x": 0, "y": 0}), "data": {}}
        for n in SEED_EXECUTABLE["nodes"]
    ],
    "edges": [
        {"id": f"e{i}", "source": e["source"], "target": e["target"]}
        for i, e in enumerate(SEED_EXECUTABLE["edges"])
    ],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}

_DEMO_PROJECTS = [
    ("Customer Support", "customer-support", "active", {"workflows": 1, "tools": 3, "runs7d": 1840}),
    ("Internal Ops Bot", "internal-ops-bot", "active", {"workflows": 0, "tools": 0, "runs7d": 620}),
    ("Sales Assistant", "sales-assistant", "active", {"workflows": 0, "tools": 0, "runs7d": 980}),
]


async def bootstrap(session) -> str:
    """Ensure exactly one tenant (+ owner user). Returns the tenant id. No projects.

    The owner's password comes from FORGE_BOOTSTRAP_ADMIN_PASSWORD when set, so the
    first login works once auth is enabled; otherwise the owner has no password and
    is only usable via the no-auth dev fallback (or self-service register)."""
    from forge.config import settings
    from forge.security import hash_password

    existing = (await session.execute(select(Tenant))).scalars().first()
    if existing:
        # Backfill a password on the seeded owner if one is now configured.
        if settings.bootstrap_admin_password:
            owner = (
                await session.execute(
                    select(User).where(User.tenant_id == existing.id, User.role == "owner")
                )
            ).scalars().first()
            if owner and not owner.password_hash:
                owner.password_hash = hash_password(settings.bootstrap_admin_password)
                await session.commit()
        return existing.id
    tenant = Tenant(name="My Workspace", plan="free")
    session.add(tenant)
    await session.flush()
    session.add(User(
        tenant_id=tenant.id, email=settings.bootstrap_admin_email, role="owner",
        password_hash=hash_password(settings.bootstrap_admin_password) if settings.bootstrap_admin_password else None,
    ))
    await session.commit()
    return tenant.id


async def seed_demo_data(session, tenant_id: str) -> None:
    """Populate showcase projects/tools/auth/workflow. Idempotent (skips if any project exists)."""
    has_project = (
        await session.execute(select(Project).where(Project.tenant_id == tenant_id))
    ).scalars().first()
    if has_project:
        return

    first_project_id = None
    for name, slug, status, stats in _DEMO_PROJECTS:
        project = Project(
            tenant_id=tenant_id, name=name, slug=slug, description=f"{name} agents and workflows.",
            status=status, config={"default_model": "fake:echo", "stats": stats},
        )
        session.add(project)
        await session.flush()
        if first_project_id is None:
            first_project_id = project.id

    session.add(Workflow(
        tenant_id=tenant_id, project_id=first_project_id, name="Support Router",
        description="Intent router → billing/tech agents.",
        executable=SEED_EXECUTABLE, canvas=SEED_CANVAS, status="active",
    ))

    await SecretStore().write(
        session, tenant_id=tenant_id, project_id=first_project_id,
        name="orders_api_creds", kind="csrf_session",
        value={"username": "svc_acme", "password": "s3cr3t"},
    )
    ap = AuthProvider(
        tenant_id=tenant_id, project_id=first_project_id, name="orders_session", kind="csrf_session",
        credentials_ref="secret://proj/orders_api_creds",
        config={
            "kind": "csrf_session", "credentials_ref": "secret://proj/orders_api_creds",
            "token_fetch": {
                "method": "POST", "url": "https://api.acme.dev/auth/login",
                "headers": {"Content-Type": "application/json"},
                "body": {"username": "{{cred.username}}", "password": "{{cred.password}}"},
            },
            "extract": [
                {"name": "csrf", "from": "header", "header": "X-CSRF-Token"},
                {"name": "session", "from": "cookie", "cookie": "SESSIONID"},
            ],
            "inject": [
                {"to": "header", "name": "X-CSRF-Token", "value": "{{extracted.csrf}}"},
                {"to": "cookie", "name": "SESSIONID", "value": "{{extracted.session}}"},
            ],
            "cache_ttl_seconds": 1800, "refresh_on": [401, 403],
        },
    )
    session.add(ap)
    await session.flush()

    session.add(Tool(
        tenant_id=tenant_id, project_id=first_project_id, name="get_order", kind="rest_api",
        auth_provider_id=ap.id, last_tested="pass",
        config={
            "description": "Fetch an order by ID, including line items and totals.",
            "request": {
                "method": "GET", "url_template": "https://api.acme.dev/v2/orders/{order_id}",
                "fields": [
                    {"path": "order_id", "type": "string", "in": "path", "required": True, "llm_visible": True, "description": "The order identifier"},
                    {"path": "include", "type": "string", "in": "query", "required": False, "llm_visible": False, "default": "totals,customer"},
                ],
                "headers": [{"name": "Accept", "value": "application/json"}],
            },
            "response": {"projection_jmespath": "data.{subtotal: totals.subtotal, total: totals.grand_total, status: status}"},
            "timeout_seconds": 30,
        },
    ))
    session.add(Tool(tenant_id=tenant_id, project_id=first_project_id, name="current_time", kind="builtin", last_tested="pass", config={"builtin": "current_time", "description": "Get the current UTC time."}))
    session.add(Tool(tenant_id=tenant_id, project_id=first_project_id, name="calculator", kind="builtin", last_tested="pass", config={"builtin": "calculator", "description": "Evaluate an arithmetic expression."}))

    await session.commit()
