"""VM snapshots as the worker last read them (design §39): the state, parent and current flag next
to LABaPe's notes. 0016 creates them on a new database; this adds them where an early 0016 ran
without them.

Revision ID: 0017_vm_snapshot_listing
Revises: 0016_vm_snapshots
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_vm_snapshot_listing"
down_revision = "0016_vm_snapshots"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("state", sa.String(16), nullable=False, server_default=""),
    sa.Column("parent", sa.String(63), nullable=False, server_default=""),
    sa.Column("current", sa.Boolean, nullable=False, server_default=sa.false()),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("vm_snapshots")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("vm_snapshots", col)


def downgrade() -> None:
    pass        # 0016's downgrade drops the table with them
