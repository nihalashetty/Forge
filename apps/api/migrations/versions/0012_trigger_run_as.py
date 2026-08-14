"""Triggers: run_as_user_id - whose connected accounts an unattended run uses

A webhook or a schedule has no signed-in person. Every catalog connector is per-user, so
without an identity on the trigger a scheduled run has no token to resolve and fails at its
first tool call. This column names the user an unattended run acts as - the editor who saved
the workflow, reassignable afterwards.

NULL is the pre-existing behaviour (no identity), so this is safe to apply to a live DB: nothing
changes until a workflow is next saved, which stamps the owner.

`create_all` builds it on fresh dev DBs; this migration covers managed Postgres. Idempotent.

Revision ID: 0012_trigger_run_as
Revises: 0011_connectors
Create Date: 2026-08-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0012_trigger_run_as"
down_revision = "0011_connectors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "triggers" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("triggers")}
    if "run_as_user_id" not in cols:
        op.add_column("triggers", sa.Column("run_as_user_id", sa.String(36), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "triggers" not in set(insp.get_table_names()):
        return
    if "run_as_user_id" in {c["name"] for c in insp.get_columns("triggers")}:
        op.drop_column("triggers", "run_as_user_id")
