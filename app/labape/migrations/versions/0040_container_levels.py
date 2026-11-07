"""The Containers page's action level per host (plan C2): off, known, new or all, and the known names.

Revision ID: 0040_container_levels
Revises: 0039_host_containers
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040_container_levels"
down_revision = "0039_host_containers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("kvm_hosts")}
    if "container_level" not in have:
        op.add_column("kvm_hosts", sa.Column("container_level", sa.String(8), nullable=False, server_default="off"))
    if "container_level_at" not in have:
        op.add_column("kvm_hosts", sa.Column("container_level_at", sa.DateTime(timezone=True), nullable=True))
    if "container_known" not in have:
        op.add_column("kvm_hosts", sa.Column("container_known", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("kvm_hosts", "container_known")
    op.drop_column("kvm_hosts", "container_level_at")
    op.drop_column("kvm_hosts", "container_level")
