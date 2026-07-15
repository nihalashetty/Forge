"""tool_sets.exposed (GitHub-style MCP exposure)

Adds `exposed` to `tool_sets`: the MCP surface is exactly the enabled tools of exposed tool sets
(no loose per-tool exposure). Defaults to true so existing sets keep publishing. `create_all`
builds it on fresh dev DBs; this migration covers managed Postgres and pre-existing tables.

Idempotent: added only when absent.

Revision ID: 0010_toolset_exposed
Revises: 0009_oauth_clients
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0010_toolset_exposed"
down_revision = "0009_oauth_clients"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "tool_sets" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("tool_sets")}
    if "exposed" not in cols:
        op.add_column("tool_sets", sa.Column("exposed", sa.Boolean(), server_default=sa.true(), nullable=False))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "tool_sets" in insp.get_table_names() and "exposed" in {c["name"] for c in insp.get_columns("tool_sets")}:
        op.drop_column("tool_sets", "exposed")
