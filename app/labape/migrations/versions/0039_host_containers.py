"""The Containers page: what a host's Docker or Podman holds, and the per-host switch for its alerts.

Revision ID: 0039_host_containers
Revises: 0038_grant_subjects
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0039_host_containers"
down_revision = "0038_grant_subjects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "host_containers" not in insp.get_table_names():
        op.create_table(
            "host_containers",
            sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), primary_key=True),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("data", sa.JSON, nullable=False),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
        )
    if "container_alerts" not in {c["name"] for c in insp.get_columns("kvm_hosts")}:
        op.add_column("kvm_hosts", sa.Column("container_alerts", sa.Boolean, nullable=False, server_default=sa.true()))


def downgrade() -> None:
    op.drop_column("kvm_hosts", "container_alerts")
    op.drop_table("host_containers")
