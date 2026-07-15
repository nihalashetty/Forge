"""scope user email uniqueness to a workspace

The original schema made ``users.email`` globally unique. A user row belongs to one
tenant, so that prevented the same person from joining more than one workspace. Replace
the unique email index with a normal lookup index plus a composite tenant/email
constraint. Existing data is safe because global uniqueness was stricter.

Revision ID: 0005_tenant_scoped_user_email
Revises: 0004_versions_evals_platform
Create Date: 2026-07-15
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_tenant_scoped_user_email"
down_revision = "0004_versions_evals_platform"
branch_labels = None
depends_on = None


def _indexes() -> dict[str, dict]:
    return {idx["name"]: idx for idx in sa.inspect(op.get_bind()).get_indexes("users")}


def _unique_constraints() -> dict[str, dict]:
    return {
        constraint["name"]: constraint
        for constraint in sa.inspect(op.get_bind()).get_unique_constraints("users")
        if constraint.get("name")
    }


def _create_tenant_email_constraint() -> None:
    if "uq_users_tenant_email" in _unique_constraints():
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.create_unique_constraint("uq_users_tenant_email", ["tenant_id", "email"])
    else:
        op.create_unique_constraint("uq_users_tenant_email", "users", ["tenant_id", "email"])


def _drop_tenant_email_constraint() -> None:
    if "uq_users_tenant_email" not in _unique_constraints():
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("users", recreate="always") as batch:
            batch.drop_constraint("uq_users_tenant_email", type_="unique")
    else:
        op.drop_constraint("uq_users_tenant_email", "users", type_="unique")


def upgrade() -> None:
    indexes = _indexes()
    email_index = indexes.get("ix_users_email")
    if email_index and email_index.get("unique"):
        op.drop_index("ix_users_email", table_name="users")
    _create_tenant_email_constraint()
    if "ix_users_email" not in _indexes():
        op.create_index("ix_users_email", "users", ["email"], unique=False)


def downgrade() -> None:
    indexes = _indexes()
    if "ix_users_email" in indexes:
        op.drop_index("ix_users_email", table_name="users")
    _drop_tenant_email_constraint()
    if "ix_users_email" not in _indexes():
        op.create_index("ix_users_email", "users", ["email"], unique=True)
