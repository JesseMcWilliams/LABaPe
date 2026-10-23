"""Environment templates made of other templates (design section 69).

Revision ID: 0034_env_template_includes
Revises: 0033_env_templates
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034_env_template_includes"
down_revision = "0033_env_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("env_templates")}
    if "includes" not in cols:
        op.add_column("env_templates", sa.Column("includes", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("env_templates", "includes")
