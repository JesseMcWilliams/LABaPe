"""Rolling back across a release that added a table (the 2026-11-12 Pi): the restore must empty
the schema first, since a table the dump lacks that points at a dumped one blocks pg_restore --clean."""
import io
import shutil

import pytest
from sqlalchemy import text

from labape.db import session_factory
from labape.engine import maintenance

pytestmark = pytest.mark.skipif(
    not __import__("os").environ.get("LABAPE_TEST_DATABASE_URL") or not shutil.which("pg_dump")
    or not shutil.which("psql"), reason="needs PostgreSQL (LABAPE_TEST_DATABASE_URL) and its client tools")


def test_restore_drops_a_newer_table_that_points_at_an_older_one(tmp_path):
    db = session_factory()()
    db.execute(text("DROP TABLE IF EXISTS zz_child, zz_parent"))
    db.execute(text("CREATE TABLE zz_parent (id integer PRIMARY KEY, v text)"))
    db.execute(text("INSERT INTO zz_parent VALUES (1, 'before')"))
    db.commit()
    rc, dump = maintenance.dump_database(db, io.StringIO())
    assert rc == 0
    # the "newer release": a child table with a foreign key, and a changed row
    db.execute(text("CREATE TABLE zz_child (id integer PRIMARY KEY, p integer REFERENCES zz_parent(id))"))
    db.execute(text("UPDATE zz_parent SET v = 'after'"))
    db.commit()
    db.close()
    log = io.StringIO()
    assert maintenance.restore_database(dump, log) == 0, log.getvalue()
    db = session_factory()()
    try:
        assert db.execute(text("SELECT v FROM zz_parent")).scalar() == "before"
        assert db.execute(text("SELECT to_regclass('zz_child')")).scalar() is None
    finally:
        db.execute(text("DROP TABLE IF EXISTS zz_parent"))
        db.commit()
        db.close()
