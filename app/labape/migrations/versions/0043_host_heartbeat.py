"""A host can opt out of the daily heartbeat (plan N2).

Revision ID: 0043_host_heartbeat
Revises: 0042_notification_digest
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0043_host_heartbeat"
down_revision = "0042_notification_digest"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "heartbeat" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}:
        op.add_column("kvm_hosts", sa.Column("heartbeat", sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("kvm_hosts", "heartbeat")
