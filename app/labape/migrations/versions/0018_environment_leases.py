"""Environment leases (design §40, batch E1): when an environment expires, and what the lease has done
so far (reminders sent, shut down, destroy queued).

Revision ID: 0018_environment_leases
Revises: 0017_vm_snapshot_listing
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_environment_leases"
down_revision = "0017_vm_snapshot_listing"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("lease_state", sa.JSON, nullable=True),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("environments")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("environments", col)


def downgrade() -> None:
    for col in COLUMNS:
        op.drop_column("environments", col.name)
