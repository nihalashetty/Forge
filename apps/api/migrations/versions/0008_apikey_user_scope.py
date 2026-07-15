"""api_keys: user_id + project_id (personal access tokens for MCP)

Adds two nullable columns to `api_keys` so a key can be a per-user Personal Access Token (PAT)
scoped to a single project - used to authenticate an individual over a project's MCP server as an
end_user. Existing (tenant, role) server-to-server keys leave both NULL and are unaffected.

Idempotent: each column is added only when absent, so this is safe whether the table was created
fresh by `create_all` (columns already present via the ORM) or predates the feature.

Revision ID: 0008_apikey_user_scope
Revises: 0007_tool_sets
Create Date: 2026-07-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0008_apikey_user_scope"
down_revision = "0007_tool_sets"
branch_labels = None
depends_on = None


def _cols(insp, table: str) -> set[str]:
    return {c["name"] for c in insp.get_columns(table)} if table in insp.get_table_names() else set()


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = _cols(insp, "api_keys")
    if "api_keys" not in insp.get_table_names():
        return  # fresh DBs build the column from the ORM; nothing to alter
    if "user_id" not in existing:
        op.add_column("api_keys", sa.Column("user_id", sa.String(36), nullable=True))
    if "project_id" not in existing:
        op.add_column("api_keys", sa.Column("project_id", sa.String(36), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    existing = _cols(insp, "api_keys")
    if "project_id" in existing:
        op.drop_column("api_keys", "project_id")
    if "user_id" in existing:
        op.drop_column("api_keys", "user_id")
