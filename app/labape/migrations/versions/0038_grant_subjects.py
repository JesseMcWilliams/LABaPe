"""User grants and role bindings are tied to the account they were made for, not just its name (question 200).

Existing rows stay untied (NULL) and are tied at their owner's next sign-in.

Revision ID: 0038_grant_subjects
Revises: 0037_vm_identities
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038_grant_subjects"
down_revision = "0037_vm_identities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table in ("grants", "role_bindings"):
        if "subject" not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, sa.Column("subject", sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column("role_bindings", "subject")
    op.drop_column("grants", "subject")
