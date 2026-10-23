"""Versions of environment templates, so a part can be pinned (design section 71).

Revision ID: 0035_env_template_versions
Revises: 0034_env_template_includes
"""
from __future__ import annotations

import datetime as dt
import json

import sqlalchemy as sa
from alembic import op

revision = "0035_env_template_versions"
down_revision = "0034_env_template_includes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "version" not in {c["name"] for c in insp.get_columns("env_templates")}:
        op.add_column("env_templates", sa.Column("version", sa.Integer, nullable=False, server_default="1"))
    if "env_template_versions" in insp.get_table_names():
        return
    op.create_table(
        "env_template_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("template_id", sa.Integer, sa.ForeignKey("env_templates.id"), nullable=False, index=True),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("spec", sa.JSON, nullable=False),
        sa.Column("params", sa.JSON, nullable=False),
        sa.Column("includes", sa.JSON, nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("template_id", "version"),
    )
    # Every template that exists is its own version 1.
    templates = sa.table("env_templates", sa.column("id", sa.Integer), sa.column("spec", sa.JSON), sa.column("params", sa.JSON),
                         sa.column("includes", sa.JSON), sa.column("owner", sa.String))
    versions = sa.table("env_template_versions", sa.column("template_id", sa.Integer), sa.column("version", sa.Integer),
                        sa.column("spec", sa.JSON), sa.column("params", sa.JSON), sa.column("includes", sa.JSON),
                        sa.column("created_by", sa.String), sa.column("created_at", sa.DateTime(timezone=True)))
    now = dt.datetime.now(dt.timezone.utc)
    rows = bind.execute(sa.select(templates.c.id, templates.c.spec, templates.c.params, templates.c.includes, templates.c.owner)).all()
    for r in rows:
        load = lambda v: json.loads(v) if isinstance(v, str) else v  # noqa: E731 — SQLite hands JSON back as text here
        bind.execute(versions.insert().values(template_id=r.id, version=1, spec=load(r.spec), params=load(r.params),
                                              includes=load(r.includes), created_by=r.owner or "", created_at=now))


def downgrade() -> None:
    op.drop_table("env_template_versions")
    op.drop_column("env_templates", "version")
