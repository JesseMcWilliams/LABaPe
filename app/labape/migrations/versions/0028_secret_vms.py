"""A shared environment secret's VM list (V2, R21; design §49.3).

Revision ID: 0028_secret_vms
Revises: 0027_round_applications
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_secret_vms"
down_revision = "0027_round_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("secrets")}
    if "vms" not in have:
        op.add_column("secrets", sa.Column("vms", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("secrets", "vms")
