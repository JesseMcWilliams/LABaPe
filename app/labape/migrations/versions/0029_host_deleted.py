"""A KVM host can be deleted and is purged after a retention period (design \u00a755).

Revision ID: 0029_host_deleted
Revises: 0028_secret_vms
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029_host_deleted"
down_revision = "0028_secret_vms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}
    if "deleted_at" not in have:
        op.add_column("kvm_hosts", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    if "deleted_by" not in have:
        op.add_column("kvm_hosts", sa.Column("deleted_by", sa.String(255), nullable=False, server_default=""))
    if "purged" not in have:
        op.add_column("kvm_hosts", sa.Column("purged", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    for c in ("purged", "deleted_by", "deleted_at"):
        op.drop_column("kvm_hosts", c)
