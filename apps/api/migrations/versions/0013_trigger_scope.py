"""Triggers: scope - a team automation, or someone's own

Forge projects are shared, but not every automation in one is. A salesperson's lead-chaser is
theirs; a platform team's prod-monitor belongs to the project. `scope` says which, and drives
who the Triggers screen lists it for.

Existing rows become "project", which is exactly how they behave today - everyone in the project
already sees every trigger - so this migration changes no observable behaviour on its own.

`create_all` builds it on fresh dev DBs; this migration covers managed Postgres. Idempotent.

Revision ID: 0013_trigger_scope
Revises: 0012_trigger_run_as
Create Date: 2026-08-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0013_trigger_scope"
down_revision = "0012_trigger_run_as"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "triggers" not in set(insp.get_table_names()):
        return
    if "scope" in {c["name"] for c in insp.get_columns("triggers")}:
        return
    # server_default backfills every existing row in one statement; the model's own default
    # takes over for new rows.
    op.add_column(
        "triggers",
        sa.Column("scope", sa.String(10), nullable=False, server_default="project"),
    )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "triggers" not in set(insp.get_table_names()):
        return
    if "scope" in {c["name"] for c in insp.get_columns("triggers")}:
        op.drop_column("triggers", "scope")
