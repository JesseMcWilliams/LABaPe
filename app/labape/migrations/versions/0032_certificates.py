"""The platform CA's certificate register (design \u00a765).

Revision ID: 0032_certificates
Revises: 0031_disk_samples
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032_certificates"
down_revision = "0031_disk_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "certificates" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "certificates",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("serial", sa.String(80), nullable=False, unique=True, index=True),
        sa.Column("subject", sa.String(255), nullable=False, server_default=""),
        sa.Column("sans", sa.JSON, nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_after", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, server_default=""),
        sa.Column("issuer", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("requested_by", sa.String(255), nullable=False, server_default="", index=True),
        sa.Column("environment_id", sa.Integer, sa.ForeignKey("environments.id"), nullable=True, index=True),
        sa.Column("host", sa.String(64), nullable=False, server_default=""),
        sa.Column("source", sa.String(16), nullable=False, server_default="request"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("pem", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("revoke_reason", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_table("certificates")
