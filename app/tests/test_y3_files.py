"""Batch Y3a (design §29.4): the primary file host and copies to the other hosts."""
import io

import pytest

from labape import appsettings
from labape.db import session_factory
from labape.engine import filedist
from labape.models import KvmHost, Template

from test_api import _admin_session, _host


def _hosts(db):
    """kvm1 (local, primary) and kvm2 (remote)."""
    p = db.get(KvmHost, 1)
    p.is_primary = True
    r = KvmHost(name="kvm2", libvirt_uri="qemu+ssh://svc@10.0.0.2/system", vm_storage_path="/vm",
                template_storage_path="/vm/templates", bridge="br0")
    db.add(r)
    db.commit()
    return p, r


class Fake:
    """Sizes per (endpoint, path); records copies; can make the primary unreachable."""

    def __init__(self, monkeypatch, sizes, down=0):
        self.sizes, self.copies, self.down = dict(sizes), [], down
        monkeypatch.setattr(filedist, "endpoint", lambda h: None if h.libvirt_uri == "qemu:///system" else h.name)
        monkeypatch.setattr(filedist, "size_of", self.size_of)
        monkeypatch.setattr(filedist, "copy", self.copy)
        monkeypatch.setattr(filedist, "sha256_of", lambda ep, path: "sha-" + path)

    def size_of(self, ep, path):
        if ep is None and self.down > 0:
            self.down -= 1
            raise filedist.Unreachable("primary: connection refused")
        return self.sizes.get((ep, path), -1)

    def copy(self, src, sp, dst, dp, log):
        self.copies.append((sp, dst, dp))
        self.sizes[(dst, dp)] = self.sizes[(src, sp)]


def test_ensure_copies_skips_and_shares(client, monkeypatch):
    _admin_session(client)
    _host(client)
    log = io.StringIO()
    with session_factory()() as db:
        p, r = _hosts(db)
        f = Fake(monkeypatch, {(None, "/data/isos/a.iso"): 100})
        assert filedist.ensure(db, p, "/data/isos/a.iso", "/x/a.iso", log) == "/data/isos/a.iso"     # the primary itself
        assert filedist.ensure(db, r, "/data/isos/a.iso", "/vm/.labape-isos/a.iso", log, 100) == "/vm/.labape-isos/a.iso"
        assert f.copies == [("/data/isos/a.iso", "kvm2", "/vm/.labape-isos/a.iso")] and "copying a.iso" in log.getvalue()
        filedist.ensure(db, r, "/data/isos/a.iso", "/vm/.labape-isos/a.iso", log, 100)
        assert len(f.copies) == 1                                                      # current: not copied again
        r.files_from_share = True
        db.commit()
        f.sizes[("kvm2", "/data/isos/a.iso")] = 100                              # the share shows it there
        assert filedist.ensure(db, r, "/data/isos/a.iso", "/vm/.labape-isos/a.iso", log) == "/data/isos/a.iso"
        with pytest.raises(filedist.FileDistError, match="isn't there"):
            filedist.ensure(db, r, "/data/isos/b.iso", "/vm/.labape-isos/b.iso", log)


def test_ensure_waits_for_the_primary_then_fails(client, monkeypatch):
    _admin_session(client)
    _host(client)
    log, slept = io.StringIO(), []
    clock = [0.0]
    with session_factory()() as db:
        p, r = _hosts(db)
        appsettings.put(db, "file_distribution", appsettings.FileDistributionSettings(wait_minutes=1))
        f = Fake(monkeypatch, {(None, "/data/isos/a.iso"): 100}, down=2)
        sleep = lambda s: (slept.append(s), clock.__setitem__(0, clock[0] + s))     # noqa: E731
        assert filedist.ensure(db, r, "/data/isos/a.iso", "/vm/c/a.iso", log, sleep=sleep, now=lambda: clock[0]) \
            == "/vm/c/a.iso"                                                       # back after two tries
        assert slept == [30, 30] and "waiting for the primary" in log.getvalue()
        f.down = 99
        with pytest.raises(filedist.FileDistError, match="within 1 minutes"):
            filedist.ensure(db, r, "/data/isos/z.iso", "/vm/c/z.iso", log, sleep=sleep, now=lambda: clock[0])
        # The host already has it (known size): no need for the primary at all.
        f.sizes[("kvm2", "/vm/c/y.iso")] = 7
        assert filedist.ensure(db, r, "/data/isos/y.iso", "/vm/c/y.iso", log, size=7) == "/vm/c/y.iso"


def test_templates_come_from_the_primary(client, monkeypatch):
    _admin_session(client)
    _host(client)
    log = io.StringIO()
    with session_factory()() as db:
        p, r = _hosts(db)
        db.add(Template(kvm_host_id=p.id, name="rocky9-base", os_key="rocky9", disk_gb=40, description="d"))
        db.commit()
        p_tpl = p.template_storage_path.rstrip("/")
        f = Fake(monkeypatch, {(None, f"{p_tpl}/rocky9-base.qcow2"): 2**30})       # no .json beside it
        r.files_from_share = True                                                   # templates copy anyway
        db.commit()
        assert filedist.ensure_template(db, r, "rocky9-base", log) is True
        assert f.copies == [(f"{p_tpl}/rocky9-base.qcow2", "kvm2", "/vm/templates/rocky9-base.qcow2")]
        t = db.query(Template).filter_by(kvm_host_id=r.id, name="rocky9-base").one()
        assert (t.os_key, t.disk_gb, t.description, t.present) == ("rocky9", 40, "d", True)
        assert filedist.ensure_template(db, r, "rocky9-base", log) is False
        with pytest.raises(filedist.FileDistError, match="neither"):
            filedist.ensure_template(db, r, "nope", log)
    # The API offers the primary's templates on the other host, marked.
    lst = client.get(f"/api/templates?kvm_host_id={r.id}").json()
    assert [t["from_primary"] for t in lst if t["name"] == "rocky9-base"] == [False]   # now it's there


def test_one_primary_and_template_list(client):
    _admin_session(client)
    h = _host(client)
    first = client.get("/api/hosts/1").json()
    assert first["is_primary"] is True                                   # the first host is the primary
    r = client.post("/api/hosts", json={"name": "kvm2", "libvirt_uri": "qemu+ssh://svc@10.0.0.2/system"})
    assert r.status_code == 201 and r.json()["is_primary"] is False
    kid = r.json()["id"]
    with session_factory()() as db:
        db.add(Template(kvm_host_id=1, name="win2022-core", os_key="windows_server_2022"))
        db.commit()
    lst = client.get(f"/api/templates?kvm_host_id={kid}").json()
    assert [(t["name"], t["from_primary"]) for t in lst] == [("win2022-core", True)]
    assert client.get("/api/templates?kvm_host_id=1").json()[0]["from_primary"] is False
    body = {k: v for k, v in r.json().items() if k not in ("id", "resources")}
    bad = client.put(f"/api/hosts/{kid}", json={**body, "is_primary": True, "files_from_share": True})
    assert bad.status_code == 422
    assert client.put(f"/api/hosts/{kid}", json={**body, "is_primary": True}).json()["is_primary"] is True
    assert client.get("/api/hosts/1").json()["is_primary"] is False
    back = client.get("/api/hosts/1").json()
    off = client.put(f"/api/hosts/{kid}", json={**body, "is_primary": False})
    assert off.status_code == 422 and "another host" in off.json()["errors"][0]["message"]
    assert back["files_from_share"] is False and h


def test_scripts_jobs_run_on_hosts_work_with_python_3_9():
    """Jobs on a remote KVM host run scripts/ with that host's python3: 3.9 on RHEL 9."""
    import ast
    import re
    from pathlib import Path
    for p in (Path(__file__).resolve().parents[2] / "scripts").rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        ast.parse(src, feature_version=(3, 9))
        if re.search(r"(->|:)\s*[\w\[\], ]+\s*\|\s*[\w\[\]]+", src):
            assert "from __future__ import annotations" in src, f"{p.name}: X | Y annotations need it on 3.9"


# --- Y3b: the stack apart from the primary (design §29.5) ---------------------------------

def test_remote_store_lists_and_hashes_through_the_host(tmp_path):
    import hashlib
    from types import SimpleNamespace
    from labape import filestore
    (tmp_path / "a.iso").write_bytes(b"abc")
    (tmp_path / ".hidden").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    store = filestore.RemoteStore(SimpleNamespace(libvirt_uri="qemu:///system", name="kvm9"))   # runs locally here
    files = store.files(str(tmp_path))
    assert [(p.endswith("a.iso"), s) for p, s, _ in files] == [(True, 3)]
    sums = store.hashes(str(tmp_path / "a.iso"), {"sha256", "md5"})
    assert sums == {"sha256": hashlib.sha256(b"abc").hexdigest(), "md5": hashlib.md5(b"abc").hexdigest()}
    assert store.files(str(tmp_path / "missing")) == []


def test_store_follows_the_primary(client):
    from labape import filestore
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        assert isinstance(filestore.current(db), filestore.LocalStore)          # the stack runs on the primary
        p, r = _hosts(db)
        p.is_primary, r.is_primary = False, True
        db.commit()
        s = filestore.current(db)
        assert isinstance(s, filestore.RemoteStore) and s.where == "kvm2"


class FakeStore:
    """A remote primary in memory: path -> bytes."""
    remote, where = True, "kvm2"

    def __init__(self):
        self.files, self.dirs = {}, set()

    def exists(self, path):
        return path in self.files

    def makedirs(self, path):
        self.dirs.add(path)

    def touch(self, path):
        self.files[path] = b""

    def size(self, path):
        return len(self.files.get(path, b"")) if path in self.files else -1

    def open_write(self, path, append=False):
        store = self

        class W:
            def __enter__(self):
                if not append:
                    store.files[path] = b""
                return self

            def write(self, data):
                store.files[path] = store.files.get(path, b"") + data

            def __exit__(self, *a):
                pass
        return W()

    def replace(self, src, dst):
        self.files[dst] = self.files.pop(src)

    def remove(self, path):
        self.files.pop(path, None)

    def stat(self, path):
        import datetime as dt
        return len(self.files[path]), dt.datetime(2026, 10, 13, tzinfo=dt.timezone.utc)

    def hashes(self, path, algos):
        import hashlib
        return {a: hashlib.new(a, self.files[path]).hexdigest() for a in algos}

    def files_in(self, root):
        return []


def test_uploads_go_to_the_primary(client, monkeypatch):
    import base64
    from labape import filestore
    from labape.models import StoredFile
    _admin_session(client)
    store = FakeStore()
    monkeypatch.setattr(filestore, "current", lambda db: store)
    meta = ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in
                    {"filename": "debian-13-netinst.iso", "category": "isos"}.items())
    r = client.post("/api/files/tus", headers={"Tus-Resumable": "1.0.0", "Upload-Length": "10", "Upload-Metadata": meta})
    assert r.status_code == 201, r.text
    loc = r.headers["Location"]
    for off, part in ((0, b"hello"), (5, b"world")):
        r = client.patch(loc, content=part, headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(off),
                                                      "Content-Type": "application/offset+octet-stream"})
        assert r.status_code == 204, r.text
    root = client.get("/api/files/roots").json()["isos"].rstrip("/")
    assert store.files[f"{root}/debian-13-netinst.iso"] == b"helloworld"            # on the primary, complete
    assert not any(k.endswith(".part") for k in store.files)
    with session_factory()() as db:
        f = db.query(StoredFile).filter_by(name="debian-13-netinst.iso").one()
        assert f.size == 10 and f.os_key == "debian_latest" and f.path == f"{root}/debian-13-netinst.iso"
        fid = f.id
    assert client.delete(f"/api/files/{fid}").status_code == 200
    assert f"{root}/debian-13-netinst.iso" not in store.files


def test_file_roots_and_templates_back_to_the_primary(client, monkeypatch):
    import subprocess
    from labape.engine import hostadmin
    _admin_session(client)
    _host(client)
    cmds = []

    class Ssh:
        def run(self, cmd, input=None, timeout=60):
            cmds.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(hostadmin, "for_host", lambda h: Ssh())
    log = io.StringIO()
    with session_factory()() as db:
        p, r = _hosts(db)
        r.access_status = "ready"
        db.commit()
        assert hostadmin.service_user(r) == "svc"
        made = hostadmin.ensure_file_roots(db, r)
        assert "/data/isos" in made and "install -d -m 0775 -o svc -g svc /data/isos" in cmds[0]
        # A template built on kvm2 goes to the primary (kvm1) too.
        f = Fake(monkeypatch, {("kvm2", "/vm/templates/new.qcow2"): 5, ("kvm2", "/vm/templates/new.json"): 1})
        assert filedist.to_primary(db, r, "new", log) is True
        p_dir = p.template_storage_path.rstrip("/")
        assert [c[2] for c in f.copies] == [f"{p_dir}/new.json", f"{p_dir}/new.qcow2"]
        assert filedist.to_primary(db, r, "new", log) is False                          # already there
        assert filedist.to_primary(db, p, "new", log) is False                          # built on the primary
