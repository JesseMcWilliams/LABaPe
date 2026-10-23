"""Environment templates (design section 68).

Revision ID: 0033_env_templates
Revises: 0032_certificates
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_env_templates"
down_revision = "0032_certificates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "env_templates" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "env_templates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(60), nullable=False, unique=True, index=True),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("owner", sa.String(255), nullable=False, server_default="", index=True),
        sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("builtin", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("env_templates")
