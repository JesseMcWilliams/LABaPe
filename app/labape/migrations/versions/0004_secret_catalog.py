"""Secret catalog (design §25): each stored secret names its store and links its rotation policy.

Revision ID: 0004_secret_catalog
Revises: 0003_firefox_debian
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_secret_catalog"
down_revision = "0003_firefox_debian"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("secrets")}


def upgrade() -> None:
    # Tolerant of a database created from newer models before it was stamped.
    have = _columns()
    with op.batch_alter_table("secrets") as t:
        if "store" not in have:
            t.add_column(sa.Column("store", sa.String(64), nullable=False, server_default="builtin"))
        if "policy_id" not in have:
            t.add_column(sa.Column("policy_id", sa.Integer, nullable=True))


def downgrade() -> None:
    # Values in external stores aren't copied back: move them to the built-in store first.
    have = _columns()
    with op.batch_alter_table("secrets") as t:
        if "policy_id" in have:
            t.drop_column("policy_id")
        if "store" in have:
            t.drop_column("store")
