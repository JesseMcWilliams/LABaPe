"""KVM host administration over SSH (design §28.4, questions 113-115).

kvm_hosts gains the SSH access account's address, port, user and status;
host_facts holds each host's latest facts (and SMART); host_metrics keeps
5-minute samples for 7 days.

Revision ID: 0008_host_access
Revises: 0007_installer_targets
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_host_access"
down_revision = "0007_installer_targets"
branch_labels = None
depends_on = None

COLUMNS = (
    ("ssh_address", sa.String(255), ""), ("ssh_port", sa.Integer, "22"), ("ssh_user", sa.String(32), "localadmin01"),
    ("access_status", sa.String(16), "none"), ("access_error", sa.Text, ""),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    have = {c["name"] for c in insp.get_columns("kvm_hosts")}
    with op.batch_alter_table("kvm_hosts") as t:
        for name, type_, default in COLUMNS:
            if name not in have:
                t.add_column(sa.Column(name, type_, nullable=False, server_default=default))
        if "access_at" not in have:
            t.add_column(sa.Column("access_at", sa.DateTime(timezone=True), nullable=True))
    tables = set(insp.get_table_names())
    if "host_facts" not in tables:
        op.create_table("host_facts",
                        sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), primary_key=True),
                        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=True),
                        sa.Column("facts", sa.JSON, nullable=False),
                        sa.Column("smart_at", sa.DateTime(timezone=True), nullable=True),
                        sa.Column("smart", sa.JSON, nullable=False),
                        sa.Column("error", sa.Text, nullable=False, server_default=""))
    if "host_metrics" not in tables:
        op.create_table("host_metrics",
                        sa.Column("id", sa.Integer, primary_key=True),
                        sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), nullable=False),
                        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
                        sa.Column("data", sa.JSON, nullable=False))
        op.create_index("ix_host_metrics_kvm_host_id", "host_metrics", ["kvm_host_id"])
        op.create_index("ix_host_metrics_at", "host_metrics", ["at"])


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())
    if "host_metrics" in tables:
        op.drop_table("host_metrics")
    if "host_facts" in tables:
        op.drop_table("host_facts")
    have = {c["name"] for c in insp.get_columns("kvm_hosts")}
    with op.batch_alter_table("kvm_hosts") as t:
        for name in [c[0] for c in COLUMNS] + ["access_at"]:
            if name in have:
                t.drop_column(name)
