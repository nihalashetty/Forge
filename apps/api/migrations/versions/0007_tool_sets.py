"""tool sets + membership

Adds first-class Tool Sets (describable groups of tools) and their many-to-many membership
join, introduced by the tool-sets / MCP-toolsets work. `create_all` builds these in dev;
managed Postgres needs this migration.

  - tool_sets          (name, slug, description, icon, is_default; unique slug per project)
  - tool_set_members   (tool_set_id <-> tool_id, unique per pair)

Column types / nullability mirror the ORM's `create_all` output so a dev SQLite database and
a migrated Postgres database converge. NOT NULL columns that carry an ORM-side default also
get a `server_default` (mirrors 0004) so the tables are insertable via raw SQL.

Idempotent: each table is created only when absent, so this is safe whether the DB was
created fresh by `create_all` (tables already present) or predates the feature. Tenant-
isolation RLS policies for these tables live in infra/postgres_rls.sql.

Revision ID: 0007_tool_sets
Revises: 0006_drop_tool_agent_version
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0007_tool_sets"
down_revision = "0006_drop_tool_agent_version"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _pk_timestamp_cols() -> list[sa.Column]:
    # Mirrors db.base.PkTimestamp: string-UUID PK + created/updated timestamps (nullable to
    # match the 0002/0003/0004 precedent; the ORM populates them on every insert).
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    insp = _inspector()

    # --- tool_sets -----------------------------------------------------------------------
    if not _has_table(insp, "tool_sets"):
        op.create_table(
            "tool_sets",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("slug", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), server_default="", nullable=False),
            sa.Column("icon", sa.String(60), nullable=True),
            sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.UniqueConstraint("tenant_id", "project_id", "slug", name="uq_tool_set_slug"),
        )
        op.create_index("ix_tool_sets_tenant_id", "tool_sets", ["tenant_id"])
        op.create_index("ix_tool_sets_project_id", "tool_sets", ["project_id"])
        op.create_index("ix_tool_sets_slug", "tool_sets", ["slug"])

    # --- tool_set_members ----------------------------------------------------------------
    if not _has_table(insp, "tool_set_members"):
        op.create_table(
            "tool_set_members",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("tool_set_id", sa.String(36), nullable=False),
            sa.Column("tool_id", sa.String(36), nullable=False),
            sa.UniqueConstraint("tool_set_id", "tool_id", name="uq_tool_set_member"),
        )
        op.create_index("ix_tool_set_members_tenant_id", "tool_set_members", ["tenant_id"])
        op.create_index("ix_tool_set_members_project_id", "tool_set_members", ["project_id"])
        op.create_index("ix_tool_set_members_tool_set_id", "tool_set_members", ["tool_set_id"])
        op.create_index("ix_tool_set_members_tool_id", "tool_set_members", ["tool_id"])


def downgrade() -> None:
    insp = _inspector()
    for table in ("tool_set_members", "tool_sets"):
        if _has_table(insp, table):
            op.drop_table(table)
