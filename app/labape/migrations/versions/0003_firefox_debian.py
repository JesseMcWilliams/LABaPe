"""Firefox on Debian: the seeded catalog entry names firefox-esr for Debian.

The web catalog was seeded from ansible/package_catalog.yml, whose Firefox
entry named one apt package for every distribution; Debian has only
firefox-esr, so Debian hosts failed (Testing_Troubleshooting-Log.md). Only
the seeded entry, still as seeded, is changed; an admin's edit is kept.

Revision ID: 0003_firefox_debian
Revises: 0002_secret_rotation
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0003_firefox_debian"
down_revision = "0002_secret_rotation"
branch_labels = None
depends_on = None

OLD_APT = "firefox"
NEW_APT = {"package": "firefox", "distributions": {"Debian": "firefox-esr"}}


def _update(old, new) -> None:
    bind = op.get_bind()
    if "catalog_items" not in sa.inspect(bind).get_table_names():
        return
    items = sa.table("catalog_items", sa.column("id"), sa.column("kind"), sa.column("name"),
                     sa.column("created_by"), sa.column("definition", sa.JSON))
    for row in bind.execute(sa.select(items.c.id, items.c.definition).where(
            items.c.kind == "application", items.c.name == "firefox", items.c.created_by == "seed")):
        d = row.definition if isinstance(row.definition, dict) else json.loads(row.definition or "{}")
        if d.get("apt") == old:
            bind.execute(items.update().where(items.c.id == row.id).values(definition={**d, "apt": new}))


def upgrade() -> None:
    _update(OLD_APT, NEW_APT)


def downgrade() -> None:
    _update(NEW_APT, OLD_APT)
