"""drop the per-entity version counter from tools and agents

The ``tools.version`` / ``agents.version`` integer counter only bumped on save and was
purely a display badge ("Save v19") - nothing in the runtime read it. The per-entity change
history (``entity_versions``) is now the single source of truth for "what changed when", so
the redundant counter is removed. Component.version (a client render-cache key), Secret.version
and Workflow.active_version are intentionally left in place.

Revision ID: 0006_drop_tool_agent_version
Revises: 0005_tenant_scoped_user_email
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_drop_tool_agent_version"
down_revision = "0005_tenant_scoped_user_email"
branch_labels = None
depends_on = None

_TABLES = ("tools", "agents")


def _has_version(table: str) -> bool:
    return any(c["name"] == "version" for c in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    for table in _TABLES:
        if not _has_version(table):
            continue
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.drop_column("version")
        else:
            op.drop_column(table, "version")


def downgrade() -> None:
    for table in _TABLES:
        if _has_version(table):
            continue
        # Re-add as a non-null counter defaulting to 1 (the original schema default).
        op.add_column(table, sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
