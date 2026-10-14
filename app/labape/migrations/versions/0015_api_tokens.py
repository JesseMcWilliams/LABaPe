"""API tokens (design §37, batch A1): personal and service tokens for scripts and CI.

Revision ID: 0015_api_tokens
Revises: 0014_stack_links
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_api_tokens"
down_revision = "0014_stack_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "api_tokens" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "api_tokens",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("roles", sa.JSON, nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("prefix", sa.String(16), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_ip", sa.String(64), nullable=False, server_default=""),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_by", sa.String(255), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_table("api_tokens")
