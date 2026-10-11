"""Alembic environment for LABaPe (design §23.2). `labape.migrate` runs it
with an open connection (under the startup lock); it never opens its own."""
from __future__ import annotations

from alembic import context

from labape import models  # noqa: F401  (registers the tables)
from labape.db import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations through labape.migrate (it passes the connection)")

context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True,
                  compare_type=True)
with context.begin_transaction():
    context.run_migrations()
