"""Console scope and limits on API tokens (design §59).

Revision ID: 0030_token_console_scope
Revises: 0029_host_deleted
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_token_console_scope"
down_revision = "0029_host_deleted"
branch_labels = None
depends_on = None

COLUMNS = (
    ("environments", sa.Column("environments", sa.JSON, nullable=True)),
    ("host_consoles", sa.Column("host_consoles", sa.Boolean, nullable=False, server_default=sa.false())),
    ("require_account", sa.Column("require_account", sa.Boolean, nullable=False, server_default=sa.true())),
    ("rate_per_minute", sa.Column("rate_per_minute", sa.Integer, nullable=False, server_default="30")),
    ("max_session_minutes", sa.Column("max_session_minutes", sa.Integer, nullable=False, server_default="30")),
    ("max_output_kb", sa.Column("max_output_kb", sa.Integer, nullable=False, server_default="256")),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("api_tokens")}
    for name, col in COLUMNS:
        if name not in have:
            op.add_column("api_tokens", col)


def downgrade() -> None:
    for name, _ in reversed(COLUMNS):
        op.drop_column("api_tokens", name)
