"""Files on each KVM host and its free-space alert (F1, questions 172, 2026-10-14 answers).

Revision ID: 0023_host_files
Revises: 0022_secret_health
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_host_files"
down_revision = "0022_secret_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}
    if "files_path" not in have:
        op.add_column("kvm_hosts", sa.Column("files_path", sa.String(255), nullable=False, server_default=""))
    if "disk_free_pct" not in have:
        op.add_column("kvm_hosts", sa.Column("disk_free_pct", sa.Integer, nullable=True))


def downgrade() -> None:
    op.drop_column("kvm_hosts", "disk_free_pct")
    op.drop_column("kvm_hosts", "files_path")
