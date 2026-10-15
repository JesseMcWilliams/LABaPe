"""P1 (design §50.8, question 171): the round's applications, installers from Catalog → Updates, npm."""
import pytest
from sqlalchemy import select

from labape import catalog
from labape.db import session_factory
from labape.engine.runner import resolve_update_installers
from labape.models import StoredFile, UpdateSource


def _source(db):
    src = db.scalar(select(UpdateSource).where(UpdateSource.name == "putty-win"))
    if src is None:
        src = UpdateSource(name="putty-win", kind="page", params={}, targets=["windows"], keep=3, enabled=True, history=[])
        db.add(src)
        db.commit()
    return src


def test_the_applications():
    names = {a[1] for a in catalog.P1_APPS}
    assert {"dotnet-runtime-8", "dotnet-hosting-8", "dotnet-sdk-8", "dotnet-runtime-10", "dotnet-hosting-10",
            "dotnet-sdk-10", "chrome", "keepassxc", "putty", "claude-code"} == names
    by = {a[1]: a[4] for a in catalog.P1_APPS}
    assert by["dotnet-sdk-10"]["windows"] == {"type": "exe", "source": "update", "update_source": "dotnet-10-sdk-win",
                                              "args": "/install /quiet /norestart"}
    assert by["dotnet-sdk-10"]["dnf"] == "dotnet-sdk-10.0" and by["dotnet-sdk-10"]["apt"]["repo"] == "microsoft-prod"
    assert by["chrome"]["dnf"]["repo"] == "google" and by["claude-code"] == {"npm": "@anthropic-ai/claude-code"}
    assert catalog.platforms("application", by["claude-code"]) == ["linux", "windows"]
    assert all(a in catalog.SEED for a in catalog.P1_APPS)


def test_validation(db_reset):
    with session_factory()() as db:
        _source(db)                                                            # seeded at start
        catalog.validate(db, "application", {"windows": {"type": "msi", "source": "update", "update_source": "putty-win"}})
        with pytest.raises(catalog.CatalogError, match="update_source"):
            catalog.validate(db, "application", {"windows": {"type": "msi", "source": "update", "update_source": "nope"}})
        catalog.validate(db, "application", {"npm": "@anthropic-ai/claude-code"})
        with pytest.raises(catalog.CatalogError, match="npm"):
            catalog.validate(db, "application", {"npm": "rm -rf /"})
        with pytest.raises(catalog.CatalogError, match="product_id"):          # still needed for a URL MSI
            catalog.validate(db, "application", {"windows": {"type": "msi", "source": "url", "url": "https://x/a.msi"}})


def test_the_newest_download_or_else_the_package_manager(db_reset):
    d = {"windows": {"type": "msi", "source": "update", "update_source": "putty-win"}, "chocolatey": "putty", "apt": "putty"}
    with session_factory()() as db:
        src = _source(db)
        src.history = []
        db.commit()
        assert resolve_update_installers(db, d) == {"chocolatey": "putty", "apt": "putty"}   # nothing downloaded yet
        old = StoredFile(category="installers", name="putty-0.84.msi", path="/i/putty-0.84.msi", sha256="a", present=False)
        new = StoredFile(category="installers", name="putty-0.85.msi", path="/i/putty-0.85.msi", sha256="b")
        db.add_all([old, new])
        db.commit()
        src.history = [{"version": "0.85", "file_id": new.id}, {"version": "0.84", "file_id": old.id}]
        db.commit()
        got = resolve_update_installers(db, d)
        assert got["windows"] == {"type": "msi", "source": "local", "path": "putty-0.85.msi"} and got["chocolatey"] == "putty"
        new.present = False
        db.commit()
        assert "windows" not in resolve_update_installers(db, d)               # the file's gone: Chocolatey
