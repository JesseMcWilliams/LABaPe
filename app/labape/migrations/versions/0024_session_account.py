"""A remote session signed in as one of the environment's accounts (E2, question 167).

Revision ID: 0024_session_account
Revises: 0023_host_files
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_session_account"
down_revision = "0023_host_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("remote_sessions")}
    if "account" not in have:
        op.add_column("remote_sessions", sa.Column("account", sa.String(255), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("remote_sessions", "account")
