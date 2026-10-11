"""Database migrations (design §23.2), with Alembic.

At startup, under the advisory lock the API and the worker share:
- a new database is created from the models and stamped at the head;
- a database from before migrations (tables, no alembic_version) is
  stamped at the baseline, then upgraded;
- otherwise pending revisions are applied.

A database at a revision this code doesn't know is newer than the code
(a rollback without restoring the pre-update backup): startup stops with
a message saying so, instead of running against a layout it can't read.
"""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.engine import Connection

BASELINE = "0001_baseline"


class LayoutError(RuntimeError):
    pass


def config(conn: Connection | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    if conn is not None:
        cfg.attributes["connection"] = conn
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(config()).get_current_head()


def current(conn: Connection) -> str | None:
    return MigrationContext.configure(conn).get_current_revision()


def upgrade(conn: Connection) -> str:
    """Bring the database to this code's head; returns what was done."""
    from . import models  # noqa: F401
    from .db import Base

    cfg = config(conn)
    script = ScriptDirectory.from_config(cfg)
    known = {r.revision for r in script.walk_revisions()}
    rev = current(conn)
    if rev is None:
        tables = set(inspect(conn).get_table_names())
        if not tables & set(Base.metadata.tables):
            Base.metadata.create_all(conn)
            command.stamp(cfg, "head")
            return f"created the database at {script.get_current_head()}"
        command.stamp(cfg, BASELINE)
        command.upgrade(cfg, "head")
        return f"stamped an existing database at {BASELINE} and upgraded to {script.get_current_head()}"
    if rev not in known:
        raise LayoutError(f"the database is at revision {rev}, which this LABaPe doesn't know (its newest is "
                          f"{script.get_current_head()}): it was written by a newer version. Restore the backup "
                          "taken before the update, or run the newer version.")
    if rev == script.get_current_head():
        return f"database at {rev} (current)"
    command.upgrade(cfg, "head")
    return f"upgraded the database from {rev} to {script.get_current_head()}"
