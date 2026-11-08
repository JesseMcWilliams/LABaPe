"""A host can turn on the weekly newer-image check (plan C4).

Revision ID: 0044_container_updates
Revises: 0043_host_heartbeat
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0044_container_updates"
down_revision = "0043_host_heartbeat"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "container_updates" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}:
        op.add_column("kvm_hosts", sa.Column("container_updates", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("kvm_hosts", "container_updates")
