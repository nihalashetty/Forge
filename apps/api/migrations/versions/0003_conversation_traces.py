"""conversation traces + tool-I/O capture columns

Adds the schema introduced for the Traces conversation view and per-tool-call I/O capture
that `create_all` won't apply to an existing database (audit P5, mirrors 0002):
  - spans.input / spans.output (captured tool-call request/response JSON)
  - runs.source (where a run originated)
  - traces.source / actor / end_user_id / user_message / ai_response (one Trace = one
    conversation turn) + the actor / end_user_id / source indexes the facet + filter
    queries rely on

Idempotent: every step checks the live schema first, so it's safe whether the DB was
created fresh by the baseline `create_all` (columns/indexes already present) or predates
these features (they get added). Dev SQLite also auto-adds the columns via
db.base._ensure_new_columns, but that path never creates the indexes - this migration is
the authoritative path for managed Postgres (`alembic upgrade head`).

Revision ID: 0003_conversation_traces
Revises: 0002_embed_components
Create Date: 2026-07-09
"""
import sqlalchemy as sa
from alembic import op

revision = "0003_conversation_traces"
down_revision = "0002_embed_components"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _has_column(insp, table: str, col: str) -> bool:
    return _has_table(insp, table) and any(c["name"] == col for c in insp.get_columns(table))


def _add_column(insp, table: str, column: sa.Column) -> None:
    if _has_table(insp, table) and not _has_column(insp, table, column.name):
        op.add_column(table, column)


def _create_index(insp, name: str, table: str, cols: list[str]) -> None:
    if not _has_table(insp, table):
        return
    if name not in {i["name"] for i in insp.get_indexes(table)}:
        op.create_index(name, table, cols)


def upgrade() -> None:
    insp = _inspector()

    # 1) spans: captured tool-call I/O
    _add_column(insp, "spans", sa.Column("input", sa.JSON(), nullable=True))
    _add_column(insp, "spans", sa.Column("output", sa.JSON(), nullable=True))

    # 2) runs.source (server_default backfills existing rows so NOT NULL holds on Postgres)
    _add_column(insp, "runs", sa.Column("source", sa.String(40), nullable=False, server_default="playground"))

    # 3) traces: conversation-turn columns
    _add_column(insp, "traces", sa.Column("source", sa.String(40), nullable=False, server_default="playground"))
    _add_column(insp, "traces", sa.Column("actor", sa.String(300), nullable=False, server_default="System"))
    _add_column(insp, "traces", sa.Column("end_user_id", sa.String(200), nullable=True))
    _add_column(insp, "traces", sa.Column("user_message", sa.Text(), nullable=True))
    _add_column(insp, "traces", sa.Column("ai_response", sa.Text(), nullable=True))

    # 4) indexes the conversation list / facets / filters depend on
    _create_index(insp, "ix_traces_actor", "traces", ["actor"])
    _create_index(insp, "ix_traces_end_user_id", "traces", ["end_user_id"])
    _create_index(insp, "ix_traces_source", "traces", ["source"])


def downgrade() -> None:
    insp = _inspector()
    for name, table in (
        ("ix_traces_source", "traces"),
        ("ix_traces_end_user_id", "traces"),
        ("ix_traces_actor", "traces"),
    ):
        if _has_table(insp, table) and name in {i["name"] for i in insp.get_indexes(table)}:
            op.drop_index(name, table_name=table)
    for table, col in (
        ("traces", "ai_response"), ("traces", "user_message"), ("traces", "end_user_id"),
        ("traces", "actor"), ("traces", "source"), ("runs", "source"),
        ("spans", "output"), ("spans", "input"),
    ):
        if _has_column(insp, table, col):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(col)
