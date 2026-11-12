"""Test settings: SQLite in a temp dir, a throwaway secret key, no OIDC."""
import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="labape-test-"))
os.environ.update({
    # LABAPE_TEST_DATABASE_URL runs the suite on PostgreSQL instead (tools/helpers/test-postgres.sh); the tests
    # then drop and recreate every table of that database, so point it at a throwaway one.
    "LABAPE_DATABASE_URL": os.environ.get("LABAPE_TEST_DATABASE_URL") or f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    "LABAPE_SECRET_KEY": "test-only-secret-key",
    "LABAPE_PUBLIC_URL": "http://testserver",
    "LABAPE_DATA_DIR": str(_tmp / "data"),
    "LABAPE_CONFIG_DIR": str(_tmp / "config"),
    "LABAPE_SECRETS_DIR": str(_tmp / "secrets"),
    "LABAPE_ENGINE_DIR": str(_tmp / "engine"),
    "LABAPE_WEB_DIR": str(_tmp / "web"),
    "LABAPE_OIDC_ISSUER": "",
})

from fastapi.testclient import TestClient  # noqa: E402

from labape.db import Base, engine, init_db  # noqa: E402
from labape.main import create_app  # noqa: E402


@pytest.fixture()
def tmp_root() -> Path:
    return _tmp


@pytest.fixture()
def db_reset():
    from labape import appsettings, stores
    appsettings._cache.clear()           # settings and store sessions belong to the old database
    stores._instances.clear()
    stores._checks.clear()
    init_db()
    Base.metadata.drop_all(engine())
    with engine().begin() as conn:      # Alembic's own table isn't in the models
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    init_db()


@pytest.fixture()
def app(db_reset):
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app)


def _stub_missing_hosts(session, flush_context, instances):
    """SQLite doesn't enforce foreign keys, so many fixtures point rows at a host id that was never created. On
    PostgreSQL (LABAPE_TEST_DATABASE_URL) add a stub host for each such id first, so those tests run unchanged."""
    from labape.models import KvmHost
    want = {getattr(o, "kvm_host_id", None) for o in session.new} - {None}
    with session.no_autoflush:
        for hid in want:
            if session.get(KvmHost, hid) is None and not any(isinstance(o, KvmHost) and o.id == hid for o in session.new):
                session.add(KvmHost(id=hid, name=f"stub-host-{hid}"))


@pytest.fixture(autouse=True)
def _postgres_foreign_keys(request):
    if not os.environ.get("LABAPE_TEST_DATABASE_URL"):
        yield
        return
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    event.listen(Session, "before_flush", _stub_missing_hosts)
    try:
        yield
    finally:
        event.remove(Session, "before_flush", _stub_missing_hosts)
