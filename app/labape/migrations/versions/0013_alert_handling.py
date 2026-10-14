"""Alert handling (design §31, batch M2): acknowledging an alert, silences, and when an alert was
announced (so one that starts during a silence or maintenance is announced when that ends).

Revision ID: 0013_alert_handling
Revises: 0012_stack_monitoring
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_alert_handling"
down_revision = "0012_stack_monitoring"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("announced_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("acked_by", sa.String(255), nullable=False, server_default=""),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    have = {c["name"] for c in insp.get_columns("monitor_alerts")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("monitor_alerts", col)
    # Every alert so far was announced when it started.
    bind.execute(sa.text("UPDATE monitor_alerts SET announced_at = started_at WHERE announced_at IS NULL"))
    if "monitor_silences" not in insp.get_table_names():
        op.create_table(
            "monitor_silences",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("key", sa.String(255), nullable=False),
            sa.Column("message", sa.Text, nullable=False, server_default=""),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_monitor_silences_key", "monitor_silences", ["key"])


def downgrade() -> None:
    op.drop_table("monitor_silences")
    for col in COLUMNS:
        op.drop_column("monitor_alerts", col.name)
