"""Test settings: SQLite in a temp dir, a throwaway secret key, no OIDC."""
import os
import tempfile
from pathlib import Path

import pytest

_tmp = Path(tempfile.mkdtemp(prefix="labape-test-"))
os.environ.update({
    "LABAPE_DATABASE_URL": f"sqlite:///{(_tmp / 'test.db').as_posix()}",
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
    init_db()
    Base.metadata.drop_all(engine())
    init_db()


@pytest.fixture()
def app(db_reset):
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app)
