"""Secret rotation (design §24): data keys for the built-in store, rotation policies.

Revision ID: 0002_secret_rotation
Revises: 0001_baseline
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_secret_rotation"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tolerant of a database whose tables were created from newer models
    # (create_all) before it was stamped: only create what's missing.
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "secret_data_keys" not in have:
        _data_keys()
    if "secret_policies" not in have:
        _policies()


def _data_keys() -> None:
    op.create_table(
        "secret_data_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("wrapped", sa.Text, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )


def _policies() -> None:
    op.create_table(
        "secret_policies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("label", sa.String(255), nullable=False, server_default=""),
        sa.Column("store", sa.String(64), nullable=False, server_default="builtin"),
        sa.Column("secret_class", sa.String(16), nullable=False, server_default="privileged"),
        sa.Column("interval_hours", sa.Integer, nullable=False, server_default="24"),
        sa.Column("needs_restart", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("managed_externally", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("elevated_ref", sa.String(128), nullable=False, server_default=""),
        sa.Column("params", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=False, server_default=""),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("secret_policies")
    op.drop_table("secret_data_keys")
