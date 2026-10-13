"""Browser remote sessions through guacd (design §28.5, questions 116-118).

Revision ID: 0009_remote_sessions
Revises: 0008_host_access
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_remote_sessions"
down_revision = "0008_host_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "remote_sessions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "remote_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=True),
        sa.Column("kvm_host_id", sa.Integer, sa.ForeignKey("kvm_hosts.id"), nullable=True),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("address", sa.String(255), nullable=False, server_default=""),
        sa.Column("protocol", sa.String(8), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("recorded", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("client_ip", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_remote_sessions_token_hash", "remote_sessions", ["token_hash"])
    op.create_index("ix_remote_sessions_created_at", "remote_sessions", ["created_at"])


def downgrade() -> None:
    if "remote_sessions" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("remote_sessions")
