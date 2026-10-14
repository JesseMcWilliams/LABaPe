"""VM snapshots (design §39, batch V1): LABaPe's notes on the snapshots libvirt keeps.

Revision ID: 0016_vm_snapshots
Revises: 0015_api_tokens
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_vm_snapshots"
down_revision = "0015_api_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "vm_snapshots" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "vm_snapshots",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=False, index=True),
            sa.Column("vm", sa.String(63), nullable=False),
            sa.Column("name", sa.String(63), nullable=False),
            sa.Column("description", sa.Text, nullable=False, server_default=""),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("auto", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("state", sa.String(16), nullable=False, server_default=""),
            sa.Column("parent", sa.String(63), nullable=False, server_default=""),
            sa.Column("current", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.UniqueConstraint("environment_id", "vm", "name"),
        )


def downgrade() -> None:
    op.drop_table("vm_snapshots")
