"""oauth_clients (MCP OAuth 2.1 dynamic client registration)

Adds the `oauth_clients` table: dynamically-registered OAuth 2.1 public clients (RFC 7591) for the
MCP authorization server (a client_id + an exact-match redirect_uri allow-list). Global registry
(no tenant_id) - the user identity is bound later at the authorize step. Only used when
`settings.mcp_oauth_enabled` is on. `create_all` builds this in dev; managed Postgres needs this.

Idempotent: created only when absent.

Revision ID: 0009_oauth_clients
Revises: 0008_apikey_user_scope
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0009_oauth_clients"
down_revision = "0008_apikey_user_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "oauth_clients" not in insp.get_table_names():
        op.create_table(
            "oauth_clients",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("client_id", sa.String(64), nullable=False),
            sa.Column("client_name", sa.String(200), nullable=True),
            sa.Column("redirect_uris", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        )
        op.create_index("ix_oauth_clients_client_id", "oauth_clients", ["client_id"], unique=True)


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "oauth_clients" in insp.get_table_names():
        op.drop_table("oauth_clients")
