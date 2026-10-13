"""Installer OS targets (design §28.2, question 112).

`files.targets`: the OSes an installer may be used on, generic and/or
fine-grained (labape/ostargets.py). Existing installers get a value from
their extension (MSI/EXE -> windows, DEB -> debian-family, RPM -> rhel-family,
else any); admins refine it on the file's page.

Revision ID: 0007_installer_targets
Revises: 0006_syslog_collector
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from labape.ostargets import infer

revision = "0007_installer_targets"
down_revision = "0006_syslog_collector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "files" not in sa.inspect(bind).get_table_names():
        return
    # Tolerant of a database created from newer models before it was stamped.
    if "targets" not in {c["name"] for c in sa.inspect(bind).get_columns("files")}:
        with op.batch_alter_table("files") as t:
            t.add_column(sa.Column("targets", sa.JSON, nullable=True))
    files = sa.table("files", sa.column("id"), sa.column("category"), sa.column("name"),
                     sa.column("targets", sa.JSON))
    for row in bind.execute(sa.select(files.c.id, files.c.category, files.c.name, files.c.targets)).all():
        if row.targets:
            continue
        value = infer(row.name) if row.category == "installers" else []
        bind.execute(files.update().where(files.c.id == row.id).values(targets=value))


def downgrade() -> None:
    bind = op.get_bind()
    if "targets" in {c["name"] for c in sa.inspect(bind).get_columns("files")}:
        with op.batch_alter_table("files") as t:
            t.drop_column("targets")
