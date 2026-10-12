"""Templates round: a template records its Windows Server SKU, and the
seeded Firefox entry is marked as needing a desktop.

`templates.sku` is standard | datacenter (blank for clients and Linux;
question 19). Firefox gets `requires_gui: true`, so Server Core hosts skip
it (question 23). Only the seeded entry is changed; an admin's edit is kept.

Revision ID: 0005_templates_round
Revises: 0004_secret_catalog
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0005_templates_round"
down_revision = "0004_secret_catalog"
branch_labels = None
depends_on = None


def _firefox(requires_gui: bool) -> None:
    bind = op.get_bind()
    if "catalog_items" not in sa.inspect(bind).get_table_names():
        return
    items = sa.table("catalog_items", sa.column("id"), sa.column("kind"), sa.column("name"),
                     sa.column("created_by"), sa.column("updated_by"), sa.column("definition", sa.JSON))
    for row in bind.execute(sa.select(items.c.id, items.c.definition).where(
            items.c.kind == "application", items.c.name == "firefox", items.c.created_by == "seed",
            items.c.updated_by == "seed")):
        d = row.definition if isinstance(row.definition, dict) else json.loads(row.definition or "{}")
        if requires_gui:
            d = {"requires_gui": True, **d}
        else:
            d = {k: v for k, v in d.items() if k != "requires_gui"}
        bind.execute(items.update().where(items.c.id == row.id).values(definition=d))


def upgrade() -> None:
    # Tolerant of a database created from newer models before it was stamped.
    if "sku" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("templates")}:
        with op.batch_alter_table("templates") as t:
            t.add_column(sa.Column("sku", sa.String(16), nullable=False, server_default=""))
    _firefox(True)


def downgrade() -> None:
    _firefox(False)
    if "sku" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("templates")}:
        with op.batch_alter_table("templates") as t:
            t.drop_column("sku")
