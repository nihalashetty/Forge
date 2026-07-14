"""entity versions + eval history + platform-hardening tables

Adds the tables introduced by the feature-bounty work that `create_all` builds in dev but
that managed Postgres needs an explicit migration for:
  - entity_versions   (point-in-time snapshots for view/restore across all entity types)
  - eval_runs         (persisted eval executions + regression-gate baseline)
  - eval_results      (per-item eval outcomes)
  - api_keys          (hashed, revocable, per-tenant/role server-to-server keys)
  - project_members   (per-project role grants, additive over the tenant-wide role)
  - user_security     (email-verification flag + optional TOTP MFA, kept off `users`)

Column types / nullability / index names mirror the ORM's `create_all` output so a dev
SQLite database and a migrated Postgres database converge on the same schema. NOT NULL
columns that carry an ORM-side default also get a `server_default` (mirrors 0003's
`runs.source`) so the tables are insertable via raw SQL and future backfills are
unambiguous. created_at/updated_at are left nullable to match 0002/0003.

Idempotent: each table is created only when absent, so this is safe whether the DB was
created fresh by the baseline `create_all` (tables already present) or predates these
features. Run on managed Postgres with `alembic upgrade head`. Tenant-isolation RLS
policies for these tables live in infra/postgres_rls.sql.

Revision ID: 0004_versions_evals_platform
Revises: 0003_conversation_traces
Create Date: 2026-07-14
"""
import sqlalchemy as sa
from alembic import op

revision = "0004_versions_evals_platform"
down_revision = "0003_conversation_traces"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(insp, name: str) -> bool:
    return name in insp.get_table_names()


def _pk_timestamp_cols() -> list[sa.Column]:
    # Mirrors db.base.PkTimestamp: string-UUID PK + created/updated timestamps (nullable to
    # match the 0002/0003 precedent; the ORM populates them on every insert).
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    insp = _inspector()

    # --- entity_versions -----------------------------------------------------------------
    if not _has_table(insp, "entity_versions"):
        op.create_table(
            "entity_versions",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=True),
            sa.Column("entity_type", sa.String(40), nullable=False),
            sa.Column("entity_id", sa.String(36), nullable=False),
            sa.Column("version_no", sa.Integer(), server_default="1", nullable=False),
            sa.Column("label", sa.String(300), nullable=True),
            sa.Column("snapshot", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
            sa.Column("author_id", sa.String(36), nullable=True),
            sa.Column("author_email", sa.String(320), nullable=True),
            sa.UniqueConstraint("entity_type", "entity_id", "version_no", name="uq_entity_version"),
        )
        op.create_index("ix_entity_versions_tenant_id", "entity_versions", ["tenant_id"])
        op.create_index("ix_entity_versions_project_id", "entity_versions", ["project_id"])
        op.create_index("ix_entity_versions_entity_type", "entity_versions", ["entity_type"])
        op.create_index("ix_entity_versions_entity_id", "entity_versions", ["entity_id"])

    # --- eval_runs -----------------------------------------------------------------------
    if not _has_table(insp, "eval_runs"):
        op.create_table(
            "eval_runs",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("dataset_id", sa.String(36), nullable=False),
            sa.Column("workflow_id", sa.String(36), nullable=True),
            sa.Column("score_mode", sa.String(20), server_default="contains", nullable=False),
            sa.Column("status", sa.String(20), server_default="done", nullable=False),
            sa.Column("total", sa.Integer(), server_default="0", nullable=False),
            sa.Column("passed", sa.Integer(), server_default="0", nullable=False),
            sa.Column("pass_rate", sa.Float(), server_default="0", nullable=False),
            sa.Column("prev_pass_rate", sa.Float(), nullable=True),
            sa.Column("regressed", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
            sa.Column("total_cost_usd", sa.Float(), server_default="0", nullable=False),
            sa.Column("metadata", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        )
        op.create_index("ix_eval_runs_tenant_id", "eval_runs", ["tenant_id"])
        op.create_index("ix_eval_runs_project_id", "eval_runs", ["project_id"])
        op.create_index("ix_eval_runs_dataset_id", "eval_runs", ["dataset_id"])

    # --- eval_results --------------------------------------------------------------------
    if not _has_table(insp, "eval_results"):
        op.create_table(
            "eval_results",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("eval_run_id", sa.String(36), nullable=False),
            sa.Column("item_index", sa.Integer(), server_default="0", nullable=False),
            sa.Column("input", sa.Text(), nullable=True),
            sa.Column("expected", sa.Text(), nullable=True),
            sa.Column("answer", sa.Text(), nullable=True),
            sa.Column("passed", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("status", sa.String(20), server_default="scored", nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("checks", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        )
        op.create_index("ix_eval_results_tenant_id", "eval_results", ["tenant_id"])
        op.create_index("ix_eval_results_eval_run_id", "eval_results", ["eval_run_id"])

    # --- api_keys ------------------------------------------------------------------------
    if not _has_table(insp, "api_keys"):
        op.create_table(
            "api_keys",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("prefix", sa.String(16), nullable=False),
            sa.Column("key_hash", sa.String(64), nullable=False),
            sa.Column("role", sa.String(30), server_default="editor", nullable=False),
            sa.Column("status", sa.String(20), server_default="active", nullable=False),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_by", sa.String(36), nullable=True),
        )
        op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
        op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
        op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # --- project_members -----------------------------------------------------------------
    if not _has_table(insp, "project_members"):
        op.create_table(
            "project_members",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("role", sa.String(30), server_default="viewer", nullable=False),
            sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        )
        op.create_index("ix_project_members_tenant_id", "project_members", ["tenant_id"])
        op.create_index("ix_project_members_project_id", "project_members", ["project_id"])
        op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    # --- user_security -------------------------------------------------------------------
    if not _has_table(insp, "user_security"):
        op.create_table(
            "user_security",
            *_pk_timestamp_cols(),
            sa.Column("tenant_id", sa.String(36), nullable=False),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("email_verified_at", sa.DateTime(), nullable=True),
            sa.Column("totp_secret", sa.String(64), nullable=True),
            sa.Column("totp_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.UniqueConstraint("user_id", name="uq_user_security_user"),
        )
        op.create_index("ix_user_security_tenant_id", "user_security", ["tenant_id"])
        op.create_index("ix_user_security_user_id", "user_security", ["user_id"])


def downgrade() -> None:
    insp = _inspector()
    for table in (
        "user_security", "project_members", "api_keys",
        "eval_results", "eval_runs", "entity_versions",
    ):
        if _has_table(insp, table):
            op.drop_table(table)
