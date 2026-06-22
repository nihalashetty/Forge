"""embed_key + components table (+ unique constraint backfill)

Adds the schema introduced after the baseline that `create_all` won't apply to an
existing database (audit P5):
  - projects.embed_key (indexed publishable key for the chat widget)
  - the components table (generative-UI widgets) + its (tenant, project, name)
    uniqueness constraint

Idempotent: every step checks the live schema first, so it's safe whether the DB was
created fresh by the baseline `create_all` (table/columns already present) or predates
these features (they get added). Run on managed Postgres with `alembic upgrade head`.

Revision ID: 0002_embed_components
Revises: 0001_baseline
Create Date: 2026-06-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0002_embed_components"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_UQ = "uq_component_tenant_project_name"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, col: str) -> bool:
    return _has_table(insp, table) and any(c["name"] == col for c in insp.get_columns(table))


def upgrade() -> None:
    insp = _inspector()

    # 1) projects.embed_key (+ index)
    if _has_table(insp, "projects") and not _has_column(insp, "projects", "embed_key"):
        op.add_column("projects", sa.Column("embed_key", sa.String(64), nullable=True))
        existing_idx = {i["name"] for i in insp.get_indexes("projects")}
        if "ix_projects_embed_key" not in existing_idx:
            op.create_index("ix_projects_embed_key", "projects", ["embed_key"])

    # 2) components table
    if not _has_table(insp, "components"):
        op.create_table(
            "components",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("title", sa.String(200), nullable=True),
            sa.Column("description", sa.Text(), server_default="", nullable=False),
            sa.Column("props_schema", sa.JSON(), nullable=True),
            sa.Column("html", sa.Text(), server_default="", nullable=False),
            sa.Column("css", sa.Text(), server_default="", nullable=False),
            sa.Column("actions", sa.JSON(), nullable=True),
            sa.Column("sample_props", sa.JSON(), nullable=True),
            sa.Column("kind", sa.String(20), server_default="html", nullable=False),
            sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("version", sa.Integer(), server_default="1", nullable=False),
            sa.UniqueConstraint("tenant_id", "project_id", "name", name=_UQ),
        )
        op.create_index("ix_components_tenant_id", "components", ["tenant_id"])
        op.create_index("ix_components_project_id", "components", ["project_id"])
    else:
        # Table predates the uniqueness constraint - add it (create_all never alters).
        existing_uqs = {u["name"] for u in insp.get_unique_constraints("components")}
        if _UQ not in existing_uqs:
            op.create_unique_constraint(_UQ, "components", ["tenant_id", "project_id", "name"])


def downgrade() -> None:
    insp = _inspector()
    if _has_table(insp, "components"):
        op.drop_table("components")
    if _has_column(insp, "projects", "embed_key"):
        with op.batch_alter_table("projects") as batch:
            batch.drop_column("embed_key")
