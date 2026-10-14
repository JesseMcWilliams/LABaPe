"""Linked stacks (design §34, batch L1): the stacks this one is linked with, and the one-time link
codes it has handed out.

Revision ID: 0014_stack_links
Revises: 0013_alert_handling
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_stack_links"
down_revision = "0013_alert_handling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "stack_links" not in tables:
        op.create_table(
            "stack_links",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("url", sa.String(255), nullable=False, unique=True),
            sa.Column("ca_pem", sa.Text, nullable=False),
            sa.Column("ca_fingerprint", sa.String(64), nullable=False),
            sa.Column("inbound_hash", sa.String(64), nullable=False, index=True),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_try_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failures", sa.Integer, nullable=False, server_default="0"),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("summary", sa.JSON, nullable=True),
        )
    if "stack_link_invites" not in tables:
        op.create_table(
            "stack_link_invites",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("used_by", sa.String(255), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_table("stack_link_invites")
    op.drop_table("stack_links")
