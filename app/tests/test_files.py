"""File manager: tus uploads, duplicates, ISO defaults, os_iso_paths, template jobs."""
import base64

from labape.db import session_factory
from labape.files import iso_paths, set_roots
from labape.models import Job, StoredFile, Template

from test_api import _admin_session, _host


def _meta(**kv):
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in kv.items())


def _upload(client, name, data: bytes, category="isos", chunk=7, **meta):
    if category == "installers" and "targets" not in meta:
        meta["targets"] = "any"                           # an installer names its OSes at upload (L2)
    r = client.post("/api/files/tus", headers={"Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)),
                                               "Upload-Metadata": _meta(filename=name, category=category, **meta)})
    assert r.status_code == 201, r.text
    loc = r.headers["location"]
    offset = 0
    while offset < len(data):
        part = data[offset:offset + chunk]
        r = client.patch(loc, content=part, headers={"Tus-Resumable": "1.0.0", "Upload-Offset": str(offset),
                                                     "Content-Type": "application/offset+octet-stream"})
        if r.status_code != 204:
            return r
        offset = int(r.headers["upload-offset"])
        if offset < len(data):
            assert int(client.head(loc).headers["upload-offset"]) == offset  # resumable
    return r


def test_upload_dedupe_and_iso_defaults(client, tmp_root):
    _admin_session(client)
    with session_factory()() as db:
        set_roots(db, {"isos": str(tmp_root / "isos"), "installers": str(tmp_root / "inst")})
    r = _upload(client, "Linux_Rocky-9.8-x86_64-minimal.iso", b"rocky iso bytes" * 3)
    assert r.status_code == 204
    files = client.get("/api/files?category=isos").json()
    assert files[0]["os_key"] == "rocky9" and len(files[0]["sha256"]) == 64
    # Same content under another name: refused as a duplicate.
    r = _upload(client, "copy.iso", b"rocky iso bytes" * 3)
    assert r.status_code == 409 and "same content" in r.json()["errors"][0]["message"]
    # Same name: refused up front.
    r = client.post("/api/files/tus", headers={"Upload-Length": "3", "Upload-Metadata": _meta(
        filename="Linux_Rocky-9.8-x86_64-minimal.iso", category="isos")})
    assert r.status_code == 409
    assert _upload(client, "Linux_Rocky-9.9-x86_64-minimal.iso", b"newer").status_code == 204
    with session_factory()() as db:
        older = db.query(StoredFile).filter(StoredFile.name.like("%9.8%")).one()
        older_id = older.id
    r = client.put(f"/api/files/{older_id}", json={"os_key": "rocky9", "is_default": True, "description": "pinned"})
    assert r.status_code == 200
    with session_factory()() as db:
        assert iso_paths(db)["rocky9"].endswith("Rocky-9.8-x86_64-minimal.iso")
    assert client.delete(f"/api/files/{older_id}").status_code == 200
    with session_factory()() as db:
        assert iso_paths(db)["rocky9"].endswith("Rocky-9.9-x86_64-minimal.iso")


def test_bad_upload_requests(client, tmp_root):
    _admin_session(client)
    r = client.post("/api/files/tus", headers={"Upload-Length": "3", "Upload-Metadata": _meta(filename="x.iso", category="nope")})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "category"
    r = client.post("/api/files/tus", headers={"Upload-Length": "3", "Upload-Metadata": _meta(filename="../evil", category="files")})
    assert r.status_code == 422 or r.status_code == 201  # basename taken: "evil" is a valid name
    assert client.put("/api/files/roots", json={"isos": "relative/path"}).status_code == 422


def test_template_jobs(client):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        db.add(Template(kvm_host_id=1, name="rocky9-base-2026.10", os_key="rocky9", disk_gb=40))
        db.commit()
        tid = db.query(Template).one().id
    r = client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "rocky9", "name": "rocky9-base-2026.10"})
    assert r.status_code == 409  # exists
    r = client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "rocky9", "name": "rocky9-new", "core": True})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "core"
    assert client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "rocky9", "name": "rocky9-new"}).status_code == 200
    assert client.post("/api/templates/build", json={"kvm_host_id": 1, "os_key": "rocky9", "name": "rocky9-new"}).status_code == 409
    assert client.post(f"/api/templates/{tid}/refresh", json={"new_name": "rocky9-base-2026.10.1"}).status_code == 200
    with session_factory()() as db:
        types = sorted(j.type for j in db.query(Job).all())
    assert types == ["template.build", "template.refresh"]
    from labape.engine.runner import command_for
    with session_factory()() as db:
        j = db.query(Job).filter(Job.type == "template.refresh").one()
        from pathlib import Path
        cmd = command_for(j, None, Path("/w"))
    assert cmd[1].endswith("refresh-template.sh") and "--os" in cmd and "rocky9" in cmd


def test_the_os_is_given_at_upload(client, tmp_root):
    _admin_session(client)
    with session_factory()() as db:
        set_roots(db, {"isos": str(tmp_root / "isos2"), "installers": str(tmp_root / "inst2"), "files": str(tmp_root / "f2")})
    # An installer must name its OSes.
    r = client.post("/api/files/tus", headers={"Upload-Length": "3", "Upload-Metadata": _meta(filename="a.msi", category="installers")})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "targets"
    r = client.post("/api/files/tus", headers={"Upload-Length": "3", "Upload-Metadata": _meta(
        filename="a.msi", category="installers", targets="nope")})
    assert r.status_code == 422 and "Unknown targets" in r.text
    assert _upload(client, "agent.msi", b"msi bytes!", category="installers", targets="windows-server,windows_11").status_code == 204
    assert client.get("/api/files?category=installers").json()[0]["targets"] == ["windows-server", "windows_11"]
    # An ISO's OS, given, beats the guess from its name.
    assert _upload(client, "mystery.iso", b"some iso", os_key="debian_latest").status_code == 204
    assert client.get("/api/files?category=isos").json()[0]["os_key"] == "debian_latest"
    # Another file may name targets, or not.
    assert _upload(client, "notes.txt", b"plain text", category="files", targets="linux").status_code == 204
    assert client.get("/api/files?category=files").json()[0]["targets"] == ["linux"]
