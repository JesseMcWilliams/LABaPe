"""A user's own preferences (L1, Planning_Lists-Secrets-Settings.md): the page size of every paged list.

Revision ID: 0021_user_preferences
Revises: 0020_link_sso
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_user_preferences"
down_revision = "0020_link_sso"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "preferences" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("users")}:
        op.add_column("users", sa.Column("preferences", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("users", "preferences")
