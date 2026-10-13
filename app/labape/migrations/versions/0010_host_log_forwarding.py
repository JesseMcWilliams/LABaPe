"""KVM hosts send their own logs to the stack's syslog collector (design §29.1, question 124).

Revision ID: 0010_host_log_forwarding
Revises: 0009_remote_sessions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_host_log_forwarding"
down_revision = "0009_remote_sessions"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("log_forward", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("log_forward_until", sa.DateTime(timezone=True), nullable=True),
    sa.Column("log_forward_error", sa.Text, nullable=False, server_default=""),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("kvm_hosts", col)


def downgrade() -> None:
    for col in COLUMNS:
        op.drop_column("kvm_hosts", col.name)
