"""Connectors: connector_installs table + mcp_clients.auth_provider_id

A connector install is the receipt for a manifest that was expanded into ordinary rows
(AuthProvider + ToolSet + Tools + optionally an McpClient). The table records what was
created so uninstall/upgrade are exact rather than guesswork.

`mcp_clients.auth_provider_id` lets an external MCP server be authenticated with a full Auth
Provider (OAuth with refresh, per-user bundles) instead of only a static `headers_ref` secret -
which is what makes OAuth-protected remote MCP servers reachable.

`create_all` builds both on fresh dev DBs; this migration covers managed Postgres and
pre-existing tables. Idempotent: each piece is added only when absent.

Revision ID: 0011_connectors
Revises: 0010_toolset_exposed
Create Date: 2026-08-13
"""
import sqlalchemy as sa
from alembic import op

revision = "0011_connectors"
down_revision = "0010_toolset_exposed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "connector_installs" not in tables:
        op.create_table(
            "connector_installs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("tenant_id", sa.String(36), nullable=False, index=True),
            sa.Column("project_id", sa.String(36), nullable=False, index=True),
            sa.Column("slug", sa.String(120), nullable=False, index=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("version", sa.String(40), nullable=False, server_default="1.0.0"),
            sa.Column("source", sa.String(20), nullable=False, server_default="catalog"),
            sa.Column("manifest", sa.JSON(), nullable=True),
            sa.Column("auth_provider_id", sa.String(36), nullable=True),
            sa.Column("tool_set_id", sa.String(36), nullable=True),
            sa.Column("mcp_client_id", sa.String(36), nullable=True),
            sa.Column("created_tool_ids", sa.JSON(), nullable=True),
            sa.Column("created_secret_names", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="needs_setup"),
            sa.Column("status_detail", sa.Text(), nullable=True),
            sa.Column("auth_mode", sa.String(20), nullable=False, server_default="shared"),
            sa.UniqueConstraint("tenant_id", "project_id", "slug", name="uq_connector_install_slug"),
        )

    if "mcp_clients" in tables:
        cols = {c["name"] for c in insp.get_columns("mcp_clients")}
        if "auth_provider_id" not in cols:
            op.add_column("mcp_clients", sa.Column("auth_provider_id", sa.String(36), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "mcp_clients" in tables and "auth_provider_id" in {c["name"] for c in insp.get_columns("mcp_clients")}:
        op.drop_column("mcp_clients", "auth_provider_id")
    if "connector_installs" in tables:
        op.drop_table("connector_installs")
