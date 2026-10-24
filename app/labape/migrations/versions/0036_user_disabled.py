"""A user can be disabled: by the sweep that finds them deactivated or removed in the sign-in provider
(question 196), later by an admin (question 202).

Revision ID: 0036_user_disabled
Revises: 0035_env_template_versions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_user_disabled"
down_revision = "0035_env_template_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}
    if "disabled_at" not in cols:
        op.add_column("users", sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True))
    if "disabled_reason" not in cols:
        op.add_column("users", sa.Column("disabled_reason", sa.String(32), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("users", "disabled_reason")
    op.drop_column("users", "disabled_at")
