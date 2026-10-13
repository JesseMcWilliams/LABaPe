"""Batch O2 (design §28.2): installer OS targets (question 112), enforced in the catalog and
in deploy forms; migration 0007."""
import json

from sqlalchemy import create_engine, text

from labape import migrate, ostargets
from labape.db import Base, session_factory
from labape.models import StoredFile

from test_api import _admin_session, _host


def test_matching_generic_family_and_specific():
    assert ostargets.matches(["any"], "windows_11")
    assert ostargets.matches(["linux"], "debian_latest") and not ostargets.matches(["linux"], "windows_10")
    assert ostargets.matches(["windows-server"], "windows_server_2022")
    assert not ostargets.matches(["windows-server"], "windows_11")
    assert ostargets.matches(["rhel-family"], "rocky9") and not ostargets.matches(["rhel-family"], "ubuntu_lts")
    assert ostargets.matches(["ubuntu_26", "rocky9"], "rocky9") and not ostargets.matches(["ubuntu_26"], "ubuntu_lts")
    assert not ostargets.matches([], "rocky9")                     # not set: never used
    assert ostargets.infer("tool.MSI") == ["windows"] and ostargets.infer("a.rpm") == ["rhel-family"]
    assert ostargets.infer("a.deb") == ["debian-family"] and ostargets.infer("setup.sh") == ["any"]
    assert ostargets.invalid(["linux", "bogus"]) == ["bogus"]


def _installer(name, targets):
    with session_factory()() as db:
        f = StoredFile(category="installers", name=name, path=f"/data/installers/{name}", size=10, sha256=name,
                       targets=targets)
        db.add(f)
        db.commit()
        return f.id


def test_targets_api_and_deploy_enforcement(client):
    _admin_session(client)
    _host(client)
    assert {"any", "linux", "rhel-family", "rocky9"} <= {t["value"] for t in client.get("/api/files/targets").json()}
    fid = _installer("agent-1.0.rpm", ["rhel-family"])
    assert client.get(f"/api/files/{fid}").json()["targets"] == ["rhel-family"]
    for bad, text_ in ((["nope"], "Unknown targets"), ([], "at least one OS")):
        r = client.put(f"/api/files/{fid}", json={"description": "", "notes": "", "targets": bad})
        assert r.status_code == 422 and text_ in r.json()["errors"][0]["message"], r.text
    r = client.put(f"/api/files/{fid}", json={"description": "agent", "notes": "", "targets": ["rocky9", "rocky9"]})
    assert r.status_code == 200 and r.json()["targets"] == ["rocky9"]
    r = client.put(f"/api/files/{fid}", json={"description": "kept", "notes": ""})          # no targets: unchanged
    assert r.json()["targets"] == ["rocky9"]

    # The catalog refuses a Windows installer slot pointing at a Linux-only file.
    r = client.post("/api/catalog", json={"kind": "application", "name": "wrongslot", "display_name": "x", "definition": {
        "windows": {"type": "exe", "source": "local", "path": "agent-1.0.rpm"}}})
    assert r.status_code == 422 and "targets" in r.json()["errors"][0]["message"], r.text
    r = client.post("/api/catalog", json={"kind": "application", "name": "agent", "display_name": "Agent", "definition": {
        "linux": {"type": "rpm", "source": "local", "path": "agent-1.0.rpm"}}})
    assert r.status_code == 201, r.text
    assert client.put(f"/api/catalog/{r.json()['id']}/status", json={"status": "published"}).status_code == 200

    # Deploy forms: allowed on Rocky 9, refused on Ubuntu with the reason.
    g = {"name": "app", "roles": ["linux_server"], "image_source": "packer_template", "template": "rocky9-base",
         "applications": ["agent"]}
    r = client.post("/api/environments", json={"name": "tgtubuntu", "kvm_host_id": 1,
                                               "host_groups": [{**g, "os": "ubuntu_lts"}]})
    assert r.status_code == 422 and "targets Rocky Linux 9" in r.json()["errors"][0]["message"], r.text
    r = client.post("/api/environments", json={"name": "tgtrocky", "kvm_host_id": 1,
                                               "host_groups": [{**g, "os": "rocky9"}]})
    assert r.status_code == 201, r.text


def test_0007_fills_installer_targets(tmp_root):
    path = tmp_root / "mig-targets.db"
    path.unlink(missing_ok=True)
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    with eng.begin() as conn:
        Base.metadata.create_all(conn)
        conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        conn.execute(text("INSERT INTO alembic_version VALUES ('0006_syslog_collector')"))
        for i, (cat, name) in enumerate((("installers", "a.msi"), ("installers", "b.deb"), ("installers", "c.zip"),
                                         ("isos", "rocky.iso"))):
            conn.execute(text("INSERT INTO files (category, name, path, size, sha256, os_key, is_default, description,"
                              " notes, uploaded_by, present, targets) VALUES (:c, :n, :p, 1, :s, '', 0, '', '', '', 1, '[]')"),
                         {"c": cat, "n": name, "p": f"/x/{i}", "s": str(i)})
        migrate.upgrade(conn)
        rows = dict(conn.execute(text("SELECT name, targets FROM files")).all())
    value = {k: json.loads(v) if isinstance(v, str) else v for k, v in rows.items()}
    assert value == {"a.msi": ["windows"], "b.deb": ["debian-family"], "c.zip": ["any"], "rocky.iso": []}
