"""Container CPU and memory samples for the history charts (plan A1).

Revision ID: 0045_container_samples
Revises: 0044_container_updates
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_container_samples"
down_revision = "0044_container_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "container_samples" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "container_samples",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), nullable=False, index=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("cpu_pct", sa.Float, nullable=True),
            sa.Column("mem_bytes", sa.BigInteger, nullable=True),
        )


def downgrade() -> None:
    op.drop_table("container_samples")
