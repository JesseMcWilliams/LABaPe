"""Monitoring the web stack's hosts (design §30, questions 142-145): a role per host (KVM, stack,
both), the stack's address, the latest stack checks, and alerts with when they started and cleared.

Revision ID: 0012_stack_monitoring
Revises: 0011_primary_file_host
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_stack_monitoring"
down_revision = "0011_primary_file_host"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("role", sa.String(8), nullable=False, server_default="kvm"),
    sa.Column("stack_url", sa.String(255), nullable=False, server_default=""),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    have = {c["name"] for c in insp.get_columns("kvm_hosts")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("kvm_hosts", col)
    # The host the stack runs on (qemu:///system) runs both.
    bind.execute(sa.text("UPDATE kvm_hosts SET role = 'both' WHERE libvirt_uri = 'qemu:///system'"))
    tables = insp.get_table_names()
    if "stack_status" not in tables:
        op.create_table(
            "stack_status",
            sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), primary_key=True),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("data", sa.JSON, nullable=False),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
        )
    if "monitor_alerts" not in tables:
        op.create_table(
            "monitor_alerts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("key", sa.String(255), nullable=False),
            sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), nullable=True),
            sa.Column("level", sa.String(8), nullable=False),
            sa.Column("message", sa.Text, nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_monitor_alerts_key", "monitor_alerts", ["key"])
        op.create_index("ix_monitor_alerts_cleared_at", "monitor_alerts", ["cleared_at"])


def downgrade() -> None:
    op.drop_table("monitor_alerts")
    op.drop_table("stack_status")
    for col in COLUMNS:
        op.drop_column("kvm_hosts", col.name)
