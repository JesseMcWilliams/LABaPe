"""Templates round (Design_Base-Images.md §8): Datacenter, GUI-only packages on Server Core."""
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text

from labape import migrate
from labape.catalog import CatalogError, validate
from labape.db import Base, session_factory
from labape.engine.runner import command_for
from labape.engine.templates import infer
from labape.models import Job

from test_api import _admin_session, _host


def test_sku_inferred_from_name():
    assert infer("win2022-desktop-2026.10")["sku"] == "standard"
    assert infer("win2022-dc-core-2026.10")["sku"] == "datacenter"
    assert infer("win2025-datacenter-2026.11")["sku"] == "datacenter"
    assert infer("rocky9-base-2026.10")["sku"] == ""


def test_build_datacenter(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "rocky9", "name": "r9-dc", "datacenter": True})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "datacenter"
    r = client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "windows_server_2022",
                                                  "name": "win2022-dc-core-2026.10", "core": True, "datacenter": True})
    assert r.status_code == 200
    with session_factory()() as db:
        cmd = command_for(db.get(Job, r.json()["job_id"]), None, Path("/w"))
    assert "--core" in cmd and "--datacenter" in cmd


def test_requires_gui_is_validated(db_reset):
    with session_factory()() as db:
        validate(db, "application", {"chocolatey": "firefox", "requires_gui": True})
        with pytest.raises(CatalogError, match="true or false"):
            validate(db, "application", {"chocolatey": "firefox", "requires_gui": "yes"})
        with pytest.raises(CatalogError, match="install method"):
            validate(db, "application", {"requires_gui": True})


def test_0005_marks_seeded_firefox_only(tmp_root):
    path = tmp_root / "mig-0005.db"
    path.unlink(missing_ok=True)
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    with eng.begin() as conn:
        Base.metadata.create_all(conn)
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0004_secret_catalog')"))
        for name, by in (("firefox", "seed"), ("firefox-x", "seed")):
            conn.execute(text("INSERT INTO catalog_items (kind, name, display_name, description, definition, platforms,"
                              " status, created_by, updated_by, notes, updated_at) VALUES ('application', :n, :n, '',"
                              " :d, '[]', 'published', :b, :b, '', CURRENT_TIMESTAMP)"),
                         {"n": name, "b": by, "d": json.dumps({"chocolatey": "firefox"})})
        conn.execute(text("UPDATE catalog_items SET updated_by = 'admin' WHERE name = 'firefox-x'"))
        migrate.upgrade(conn)
        rows = {n: (json.loads(d) if isinstance(d, str) else d)
                for n, d in conn.execute(text("SELECT name, definition FROM catalog_items")).all()}
    assert rows["firefox"] == {"requires_gui": True, "chocolatey": "firefox"}
    assert rows["firefox-x"] == {"chocolatey": "firefox"}


# --- dated versions: refresh and retention ---------------------------------

import datetime as dt

from labape.engine import template_versions as tv
from labape.models import Environment, KvmHost, Template


def _lib(db, *names, host_id=1):
    for n in names:
        db.add(Template(kvm_host_id=host_id, name=n, os_key="windows_server_2022" if n.startswith("win") else "rocky9"))
    db.commit()


def test_parse_and_lines(db_reset):
    assert tv.parse("win2022-desktop-2026.10.1") == ("win2022-desktop", (2026, 10, 1))
    assert tv.parse("rocky9-base-2026.10") == ("rocky9-base", (2026, 10, 0))
    assert tv.parse("my-golden-image") is None
    with session_factory()() as db:
        db.add(KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system"))
        _lib(db, "win2022-desktop-2026.10", "win2022-desktop-2026.10.1", "win2022-desktop-2026.09", "golden")
        ls = tv.lines(db, 1)
    assert list(ls) == ["win2022-desktop"]
    assert [t.name for t in ls["win2022-desktop"]] == ["win2022-desktop-2026.10.1", "win2022-desktop-2026.10",
                                                       "win2022-desktop-2026.09"]


def test_monthly_refresh_once_per_month(db_reset):
    with session_factory()() as db:
        db.add(KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system"))
        _lib(db, "win2022-desktop-2026.10.1", "rocky9-base-2026.11", "golden")
        early = dt.datetime(2026, 11, 14, 23, 0, tzinfo=dt.timezone.utc)
        assert tv.schedule_refresh(db, early) == []                  # before the 15th, 03:00
        due = dt.datetime(2026, 11, 15, 3, 5, tzinfo=dt.timezone.utc)
        ids = tv.schedule_refresh(db, due)
        jobs = [db.get(Job, i) for i in ids]
        assert [(j.type, j.params["template"], j.params["new_name"]) for j in jobs] == [
            ("template.refresh", "win2022-desktop-2026.10.1", "win2022-desktop-2026.11")]
        assert tv.schedule_refresh(db, due + dt.timedelta(hours=1)) == []   # once a month


def test_retention_keeps_newest_and_skips_used(db_reset):
    with session_factory()() as db:
        db.add(KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system"))
        _lib(db, "rocky9-base-2026.07", "rocky9-base-2026.08", "rocky9-base-2026.09", "rocky9-base-2026.10",
             "rocky9-base-2026.11")
        db.add(Environment(name="e1", kvm_host_id=1, status="deployed", created_by="t", spec={"host_groups": [
            {"image_source": "packer_template", "template": "rocky9-base-2026.07"}]}))
        db.commit()
        ids = tv.schedule_retention(db)
        assert [db.get(Job, i).params["name"] for i in ids] == ["rocky9-base-2026.08"]
        assert tv.schedule_retention(db) == []                       # the delete job is already queued
        assert tv.users(db, 1, "rocky9-base-2026.07") == ["environment e1"]


def test_delete_api_and_job(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        _lib(db, "rocky9-base-2026.09", "rocky9-base-2026.10")
        db.add(Environment(name="e1", kvm_host_id=1, status="deployed", created_by="t", spec={"host_groups": [
            {"image_source": "packer_template", "template": "rocky9-base-2026.10"}]}))
        db.commit()
        used = db.scalar(select(Template).where(Template.name == "rocky9-base-2026.10")).id
        free = db.scalar(select(Template).where(Template.name == "rocky9-base-2026.09")).id
    r = client.delete(f"/api/templates/{used}")
    assert r.status_code == 409 and "environment e1" in r.text
    r = client.delete(f"/api/templates/{free}")
    assert r.status_code == 200
    calls = []

    def fake_python(self, script, args, timeout=60):
        calls.append(args)
        return {"in_use": ["vm1"]} if len(calls) == 1 else {"deleted": True}
    monkeypatch.setattr(tv.hostio.Host, "python", fake_python)
    monkeypatch.setattr("labape.engine.runner._append", lambda job, msg: None)
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert tv.run_delete(db, job) == 1                           # a VM still uses it
        assert tv.run_delete(db, job) == 0
        assert db.get(Template, free).present is False
    assert calls[0]["name"] == "rocky9-base-2026.09"
