"""Notification channels, rules and the delivery log (plan N1), and the rules an alert was sent to.

Revision ID: 0041_notifications
Revises: 0040_container_levels
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_notifications"
down_revision = "0040_container_levels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "reminded" not in {c["name"] for c in insp.get_columns("monitor_alerts")}:
        op.add_column("monitor_alerts", sa.Column("reminded", sa.JSON, nullable=False, server_default="{}"))
    tables = set(insp.get_table_names())
    if "notification_channels" not in tables:
        op.create_table(
            "notification_channels",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(80), nullable=False, unique=True),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("config", sa.JSON, nullable=False, server_default="{}"),
            sa.Column("secret_set", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("last_ok", sa.Boolean, nullable=True),
            sa.Column("last_error", sa.Text, nullable=False, server_default=""),
            sa.Column("last_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "notification_rules" not in tables:
        op.create_table(
            "notification_rules",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(80), nullable=False, unique=True),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("min_level", sa.String(8), nullable=False, server_default="warning"),
            sa.Column("host_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("categories", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("channel_ids", sa.JSON, nullable=False, server_default="[]"),
            sa.Column("quiet_start", sa.String(5), nullable=False, server_default=""),
            sa.Column("quiet_end", sa.String(5), nullable=False, server_default=""),
            sa.Column("reminder_hours", sa.Integer, nullable=False, server_default="0"),
            sa.Column("send_cleared", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "notification_log" not in tables:
        op.create_table(
            "notification_log",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("at", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("channel_id", sa.Integer, nullable=True),
            sa.Column("channel_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("rule_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("ok", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("summary", sa.Text, nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_table("notification_log")
    op.drop_table("notification_rules")
    op.drop_table("notification_channels")
    op.drop_column("monitor_alerts", "reminded")
