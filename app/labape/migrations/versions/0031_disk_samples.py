"""SMART history per physical drive (design \u00a764).

Revision ID: 0031_disk_samples
Revises: 0030_token_console_scope
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031_disk_samples"
down_revision = "0030_token_console_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "disk_samples" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "disk_samples",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), nullable=False, index=True),
            sa.Column("disk", sa.String(128), nullable=False, index=True),
            sa.Column("device", sa.String(64), nullable=False, server_default=""),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("data", sa.JSON, nullable=False),
        )


def downgrade() -> None:
    op.drop_table("disk_samples")
