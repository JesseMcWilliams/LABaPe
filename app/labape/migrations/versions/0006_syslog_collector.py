"""Syslog collector: publish the new Linux service in existing catalogs.

A new install gets it from catalog.SEED on first start; this adds it to a
catalog that was seeded before it existed (design §27). Nothing is added to
an empty catalog (the seed handles that) or when an item of that name is
already there, and the downgrade removes it only while it's still the
seeded, unedited entry.

Revision ID: 0006_syslog_collector
Revises: 0005_templates_round
"""
from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from alembic import op

from labape.catalog import SYSLOG_COLLECTOR, platforms

revision = "0006_syslog_collector"
down_revision = "0005_templates_round"
branch_labels = None
depends_on = None


def _items():
    return sa.table("catalog_items", sa.column("id"), sa.column("kind"), sa.column("name"),
                    sa.column("display_name"), sa.column("description"), sa.column("notes"),
                    sa.column("status"), sa.column("definition", sa.JSON), sa.column("platforms", sa.JSON),
                    sa.column("created_by"), sa.column("updated_by"), sa.column("updated_at"))


def upgrade() -> None:
    bind = op.get_bind()
    if "catalog_items" not in sa.inspect(bind).get_table_names():
        return
    items = _items()
    if bind.execute(sa.select(items.c.id).limit(1)).first() is None:
        return
    kind, name, display, desc, definition = SYSLOG_COLLECTOR
    if bind.execute(sa.select(items.c.id).where(items.c.kind == kind, items.c.name == name)).first():
        return
    bind.execute(items.insert().values(
        kind=kind, name=name, display_name=display, description=desc, notes="", status="published",
        definition=definition, platforms=platforms(kind, definition), created_by="seed", updated_by="seed",
        updated_at=dt.datetime.now(dt.timezone.utc)))


def downgrade() -> None:
    bind = op.get_bind()
    if "catalog_items" not in sa.inspect(bind).get_table_names():
        return
    items = _items()
    kind, name = SYSLOG_COLLECTOR[0], SYSLOG_COLLECTOR[1]
    bind.execute(items.delete().where(items.c.kind == kind, items.c.name == name, items.c.updated_by == "seed"))
