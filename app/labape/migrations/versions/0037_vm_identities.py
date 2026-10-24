"""What each VM is known by (SSH host keys, WinRM certificate fingerprint), so later connections can check it
(question 198).

Revision ID: 0037_vm_identities
Revises: 0036_user_disabled
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037_vm_identities"
down_revision = "0036_user_disabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "vm_identities" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "vm_identities",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("address", "kind"),
    )


def downgrade() -> None:
    op.drop_table("vm_identities")
