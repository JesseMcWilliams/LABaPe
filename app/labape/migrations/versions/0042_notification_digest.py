"""A rule can ask for the daily digest (plan N2).

Revision ID: 0042_notification_digest
Revises: 0041_notifications
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_notification_digest"
down_revision = "0041_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("notification_rules")}
    if "send_digest" not in have:
        op.add_column("notification_rules", sa.Column("send_digest", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("notification_rules", "send_digest")
