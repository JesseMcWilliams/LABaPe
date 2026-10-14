"""Secret health (S1, question 161): when each secret was last checked, the result, the next check.

Revision ID: 0022_secret_health
Revises: 0021_user_preferences
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_secret_health"
down_revision = "0021_user_preferences"
branch_labels = None
depends_on = None

COLS = (("check_status", sa.String(16), "unchecked"), ("check_error", sa.Text, ""))


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("secret_policies")}
    for name, typ, default in COLS:
        if name not in have:
            op.add_column("secret_policies", sa.Column(name, typ, nullable=False, server_default=default))
    for name in ("last_checked_at", "next_check_at"):
        if name not in have:
            op.add_column("secret_policies", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in ("next_check_at", "last_checked_at", "check_error", "check_status"):
        op.drop_column("secret_policies", name)
