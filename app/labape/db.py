"""Database engine and session factory."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def session_factory() -> sessionmaker:
    engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = session_factory()()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create or migrate the schema (labape.migrate, design §23.2)."""
    import logging

    from . import models  # noqa: F401  (registers tables)
    from .migrate import upgrade

    eng = engine()
    with eng.begin() as conn:
        if eng.dialect.name == "postgresql":
            # The API and the worker start together: one migrates, the other waits.
            conn.execute(text("SELECT pg_advisory_xact_lock(4711)"))
        logging.getLogger("labape.db").info("schema: %s", upgrade(conn))
    _seed()


def _seed() -> None:
    """First-start content (the starter catalog). Idempotent: only an empty
    catalog is seeded, under the same lock as table creation."""
    from .catalog import seed
    from .config import get_settings

    eng = engine()
    with session_factory()() as db:
        if eng.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(4711)"))
        seed(db, str(get_settings().engine_dir / "ansible" / "package_catalog.yml"))
