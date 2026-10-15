"""Catalog → Updates (U1, question 170): the update sources' table (seeded at start, package_updates.seed).

Revision ID: 0026_update_sources
Revises: 0025_mail_server
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_update_sources"
down_revision = "0025_mail_server"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "update_sources" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "update_sources",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(64), nullable=False, unique=True),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("params", sa.JSON, nullable=False),
            sa.Column("targets", sa.JSON, nullable=False),
            sa.Column("keep", sa.Integer, nullable=False, server_default="3"),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_version", sa.String(64), nullable=False, server_default=""),
            sa.Column("last_error", sa.Text, nullable=False, server_default=""),
            sa.Column("history", sa.JSON, nullable=False),
        )


def downgrade() -> None:
    op.drop_table("update_sources")
