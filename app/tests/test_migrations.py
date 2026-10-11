"""Database migrations: fresh, pre-migration and newer-than-code databases."""
import pytest
from sqlalchemy import create_engine, inspect, text

from labape import migrate
from labape.db import Base


def _engine(tmp_root, name):
    path = tmp_root / f"{name}.db"
    path.unlink(missing_ok=True)
    return create_engine(f"sqlite:///{path.as_posix()}")


def test_fresh_database(tmp_root):
    eng = _engine(tmp_root, "mig-fresh")
    with eng.begin() as conn:
        assert migrate.upgrade(conn).startswith("created the database")
        assert migrate.current(conn) == migrate.head()
        assert "environments" in inspect(conn).get_table_names()
    with eng.begin() as conn:
        assert migrate.upgrade(conn).endswith("(current)")


def test_pre_migration_database_is_stamped(tmp_root):
    eng = _engine(tmp_root, "mig-legacy")
    with eng.begin() as conn:
        Base.metadata.create_all(conn)           # what every install before migrations has
        assert migrate.current(conn) is None
        assert migrate.upgrade(conn).startswith(f"stamped an existing database at {migrate.BASELINE}")
        assert migrate.current(conn) == migrate.head()


def test_newer_database_is_refused(tmp_root):
    eng = _engine(tmp_root, "mig-newer")
    with eng.begin() as conn:
        migrate.upgrade(conn)
        conn.execute(text("UPDATE alembic_version SET version_num = '9999_from_the_future'"))
    with eng.begin() as conn, pytest.raises(migrate.LayoutError, match="newer version"):
        migrate.upgrade(conn)
