"""Connectors: record which egress hosts an install actually added

`install` appends a connector's hosts to the project's egress allow-list, and uninstall had no
matching removal - so on a deployment running a strict allow-list the list only ever grew, and
hosts stayed reachable with nothing referencing them.

Removing `manifest.hosts()` on the way out would be wrong the other way: a host somebody
allow-listed by hand before the connector existed is not the install's to take away, and after
the fact the two are indistinguishable. So the install records exactly what it added.

Existing rows get an empty list, which means "unknown" - uninstall leaves their hosts alone
rather than guessing.

`create_all` builds it on fresh dev DBs; this migration covers managed Postgres. Idempotent.

Revision ID: 0014_connector_egress_hosts
Revises: 0013_trigger_scope
Create Date: 2026-08-16
"""
import sqlalchemy as sa
from alembic import op

revision = "0014_connector_egress_hosts"
down_revision = "0013_trigger_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "connector_installs" not in set(insp.get_table_names()):
        return
    if "created_egress_hosts" in {c["name"] for c in insp.get_columns("connector_installs")}:
        return
    op.add_column("connector_installs", sa.Column("created_egress_hosts", sa.JSON(), nullable=True))


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "connector_installs" not in set(insp.get_table_names()):
        return
    if "created_egress_hosts" in {c["name"] for c in insp.get_columns("connector_installs")}:
        op.drop_column("connector_installs", "created_egress_hosts")
