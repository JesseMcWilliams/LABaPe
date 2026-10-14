"""Quotas (design §41, batch E2): limits per user, per group, or for everyone.

Revision ID: 0019_quotas
Revises: 0018_environment_leases
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_quotas"
down_revision = "0018_environment_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "quotas" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "quotas",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("principal_type", sa.String(16), nullable=False),
            sa.Column("principal", sa.String(255), nullable=False, server_default=""),
            sa.Column("max_vcpus", sa.Integer, nullable=True),
            sa.Column("max_memory_mb", sa.Integer, nullable=True),
            sa.Column("max_disk_gb", sa.Integer, nullable=True),
            sa.Column("max_environments", sa.Integer, nullable=True),
            sa.UniqueConstraint("principal_type", "principal"),
        )


def downgrade() -> None:
    op.drop_table("quotas")
