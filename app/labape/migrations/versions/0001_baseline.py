"""Baseline: the schema as of v0.2.0 plus round 3 (created with create_all).

Databases created before migrations existed are stamped at this revision;
new databases are created from the models and stamped at the head. Later
changes are new revisions after this one (design §23.2).

Revision ID: 0001_baseline
Revises:
"""
from __future__ import annotations

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
