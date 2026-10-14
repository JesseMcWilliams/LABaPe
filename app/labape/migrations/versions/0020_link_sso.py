"""Sign-on between linked stacks (design §47.2, F1b): whether this side lets the peer's users sign
in, and the state of setting it up.

Revision ID: 0020_link_sso
Revises: 0019_quotas
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_link_sso"
down_revision = "0019_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("stack_links")}
    if "sso_in" not in cols:
        op.add_column("stack_links", sa.Column("sso_in", sa.Boolean, nullable=False, server_default=sa.false()))
    if "sso_state" not in cols:
        op.add_column("stack_links", sa.Column("sso_state", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("stack_links", "sso_state")
    op.drop_column("stack_links", "sso_in")
