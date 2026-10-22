"""Backlog row 22: a linked stack's templates and files, listed, downloaded (with Range) and copied."""
import json
from types import SimpleNamespace

from labape import linked_library, links
from labape.db import session_factory
from labape.engine import downloads, filedist, linked_copy
from labape.models import Job, KvmHost, StackLink, StoredFile, Template

from test_api import _admin_session, _host

TOKEN = "peer-token-for-tests"


def _stack(tmp_path):
    """A primary host with one template image and one stored file, and a peer that calls with TOKEN."""
    lib = tmp_path / "templates"
    lib.mkdir()
    (lib / "web.qcow2").write_bytes(b"0123456789")
    iso = tmp_path / "rocky.iso"
    iso.write_bytes(b"ISO-CONTENT")
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        h.is_primary, h.template_storage_path = True, str(lib)
        db.add(Template(kvm_host_id=h.id, name="web", os_key="rocky9", disk_gb=20, description="a web template"))
        f = StoredFile(category="isos", name="rocky.iso", path=str(iso), size=11, sha256="ab" * 32, os_key="rocky9")
        db.add(f)
        db.add(StackLink(name="peer.lab", url="https://peer.lab", ca_pem="x", ca_fingerprint="0" * 64,
                         inbound_hash=links._sha(TOKEN)))
        db.commit()
        return f.id


def test_the_peer_endpoints_list_and_download(client, tmp_path):
    _admin_session(client)
    _host(client)
    fid = _stack(tmp_path)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/api/link/library").status_code == 401                      # only a linked stack
    lib = client.get("/api/link/library", headers=auth).json()
    assert [t["name"] for t in lib["templates"]] == ["web"] and lib["templates"][0]["size"] == 10
    assert lib["templates"][0]["os_key"] == "rocky9"
    assert [f["name"] for f in lib["files"]] == ["rocky.iso"] and lib["files"][0]["id"] == fid
    r = client.get("/api/link/download/template/web", headers=auth)
    assert r.status_code == 200 and r.content == b"0123456789" and r.headers["content-length"] == "10"
    r = client.get("/api/link/download/template/web", headers={**auth, "Range": "bytes=4-"})
    assert r.status_code == 206 and r.content == b"456789" and r.headers["content-range"] == "bytes 4-9/10"
    assert client.get(f"/api/link/download/file/{fid}", headers=auth).content == b"ISO-CONTENT"
    assert client.get("/api/link/download/template/nope", headers=auth).status_code == 404
    assert client.get("/api/link/download/template/web", headers={**auth, "Range": "bytes=10-"}).status_code == 416


def test_copy_starts_a_job_for_a_listed_item(client, tmp_path, monkeypatch):
    _admin_session(client)
    _host(client)
    _stack(tmp_path)
    peer = {"templates": [{"name": "db", "os_key": "rocky9", "size": 5}], "files": [{"id": 7, "category": "isos", "name": "x.iso"}]}
    monkeypatch.setattr(linked_library, "peer_library", lambda db, link: peer)
    lib = client.get("/api/linked/library").json()["stacks"]
    assert lib[0]["name"] == "peer.lab" and lib[0]["ok"] and lib[0]["templates"][0]["name"] == "db"
    link_id = lib[0]["link_id"]
    r = client.post("/api/linked/copy", json={"link_id": link_id, "kind": "template", "item": "db"})
    assert r.status_code == 201, r.text
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert job.type == "linked.copy" and job.params["item"]["name"] == "db" and job.params["link_id"] == link_id
    assert client.post("/api/linked/copy", json={"link_id": link_id, "kind": "template", "item": "nope"}).status_code == 404
    assert client.post("/api/linked/copy", json={"link_id": link_id, "kind": "file", "item": "7"}).status_code == 201


def test_a_group_may_take_its_template_from_a_linked_stack(client, tmp_path, monkeypatch):
    _admin_session(client)
    host_id = _host(client)
    _stack(tmp_path)
    with session_factory()() as db:
        link_id = db.query(StackLink).one().id
    monkeypatch.setattr(linked_library, "peer_library", lambda db, link: {"templates": [{"name": "remote9", "os_key": "rocky9"}], "files": []})
    g = {"name": "App", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template", "template": "remote9",
         "template_from": link_id}
    r = client.post("/api/environments", json={"name": "fromlink", "kvm_host_id": host_id, "host_groups": [g]})
    assert r.status_code == 201, r.text
    assert r.json()["environment"]["spec"]["host_groups"][0]["template_from"] == link_id
    bad = client.post("/api/environments", json={"name": "fromlink2", "kvm_host_id": host_id,
                                                 "host_groups": [{**g, "name": "App2", "template": "missing"}]})
    assert bad.status_code == 422 and "has no template" in bad.text


def test_the_deploy_copies_the_template_into_the_primary_once(tmp_path, client, monkeypatch):
    _admin_session(client)
    _host(client)
    _stack(tmp_path)
    fetched = []

    def fake_fetch(db, url, category, name, log, **kw):
        fetched.append((url, kw["target"], kw["register"], kw["headers"]))
        open(kw["target"], "wb").write(b"IMG")
        return SimpleNamespace(path=kw["target"], size=3, sha256="x")

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    monkeypatch.setattr(links, "ssl_context", lambda pem: None)
    monkeypatch.setattr(linked_library, "peer_headers", lambda db, link: {"Authorization": "Bearer t"})
    monkeypatch.setattr(linked_library, "peer_library", lambda db, link: {"templates": [{"name": "remote9", "os_key": "rocky9",
                                                                                       "disk_gb": 30, "description": "d"}], "files": []})
    log = SimpleNamespace(write=lambda s: None)
    with session_factory()() as db:
        link = db.query(StackLink).one()
        assert linked_copy.ensure_template(db, link.id, "remote9", log) is True
        assert fetched[0][0] == f"https://peer.lab/api/link/download/template/remote9" and fetched[0][2] is False
        assert fetched[0][3] == {"Authorization": "Bearer t"} and fetched[0][1].replace("\\", "/").endswith("/templates/remote9.qcow2")
        meta = json.loads(open(fetched[0][1].replace(".qcow2", ".json")).read())
        assert meta["os_key"] == "rocky9" and meta["description"] == "d"
        t = db.query(Template).filter_by(name="remote9").one()
        assert t.present and t.kvm_host_id == filedist.primary(db).id
        assert linked_copy.ensure_template(db, link.id, "remote9", log) is False        # already there: not fetched again
        assert len(fetched) == 1
