"""Batch B3 (design §36): the newest full backup to every linked stack, kept on its primary file host."""
import base64
import datetime as dt
import hashlib
import json

from labape import appsettings, links
from labape.db import session_factory
from labape.engine import stackbackup
from labape.models import Job, StackLink
from labape.security import put_secret

from test_api import _admin_session, _host
from test_l1_links import THEIRS, Resp, _ca  # noqa: F401 — the CA fixture


def _archive(d, name, ok=True, data=b"sealed-bytes"):
    (d / name).write_bytes(data)
    (d / f"{name}.json").write_text(json.dumps({"created_at": "2026-10-14T03:00:00+00:00", "check": {"ok": ok},
                                                "key_file": "/home/x/.config/labape/backup.key"}))


class Sender:
    sent: list = []
    status = 200

    def __init__(self, pem):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None, timeout=None):
        Sender.sent.append((url, content.read(), headers))
        return Resp(Sender.status, {"ok": True})


def test_the_newest_checked_archive_goes_once_to_each_link(client, tmp_path):
    _admin_session(client)
    b = tmp_path / "b"
    b.mkdir()
    _archive(b, "labape-stack-oz-20261013-030000.tar.sealed")
    _archive(b, "labape-stack-oz-20261014-030000.tar.sealed", ok=False)            # failed its check: not sent
    now = dt.datetime(2026, 10, 14, 4, 0, tzinfo=dt.timezone.utc)
    with session_factory()() as db:
        appsettings.put(db, "stack_backup", appsettings.StackBackupSettings(directory=str(b)))
        link = StackLink(name="pi.lab", url="https://pi.lab", ca_pem=THEIRS, ca_fingerprint="0" * 64, inbound_hash="x")
        db.add(link)
        db.commit()
        put_secret(db, f"link.{link.id}.outbound", "tok")
        Sender.sent, Sender.status = [], 500
        r = stackbackup.send_to_links(db, now, client_for=Sender)
        assert "500" in r[link.id]["error"]
        assert stackbackup.send_to_links(db, now + dt.timedelta(minutes=5), client_for=Sender) == {}   # waits to retry
        Sender.status = 200
        r = stackbackup.send_to_links(db, now + dt.timedelta(minutes=11), client_for=Sender)
        assert r[link.id]["archive"] == "labape-stack-oz-20261013-030000.tar.sealed" and not r[link.id]["error"]
        url, body, headers = Sender.sent[-1]
        assert url == "https://pi.lab/api/link/backups/labape-stack-oz-20261013-030000.tar.sealed"
        assert body == b"sealed-bytes" and headers["X-Sha256"] == hashlib.sha256(b"sealed-bytes").hexdigest()
        side = json.loads(base64.b64decode(headers["X-LABaPe-Sidecar"]))
        assert "key_file" not in side and headers["Authorization"] == "Bearer tok"
        assert stackbackup.send_to_links(db, now + dt.timedelta(hours=1), client_for=Sender) == {}      # already there


def test_a_received_archive_is_checked_staged_and_stored(client, tmp_path):
    _admin_session(client)
    _host(client)
    b = tmp_path / "b"
    with session_factory()() as db:
        appsettings.put(db, "stack_backup", appsettings.StackBackupSettings(directory=str(b / "stack"), retention_days=14))
        c = links.parse_code(links.invite(db, "admin")["code"])
        token = links.accept(db, c["secret"], "https://pi.lab", THEIRS, "t" * 40)["token"]
    data = b"x" * 5000
    name = "labape-stack-director-20261014-030000.tar.sealed"
    side = base64.b64encode(json.dumps({"created_at": "2026-10-14T03:00:00+00:00"}).encode()).decode()
    hdr = {"Authorization": f"Bearer {token}", "X-Sha256": hashlib.sha256(data).hexdigest(), "X-LABaPe-Sidecar": side}
    assert client.post(f"/api/link/backups/{name}", content=data, headers={**hdr, "Authorization": "Bearer no"}).status_code == 401
    bad = client.post(f"/api/link/backups/{name}", content=data, headers={**hdr, "X-Sha256": "0" * 64})
    assert bad.status_code == 400 and "damaged" in bad.json()["detail"]
    assert client.post("/api/link/backups/evil.sh", content=data, headers=hdr).status_code == 400
    r = client.post(f"/api/link/backups/{name}", content=data, headers=hdr)
    assert r.status_code == 200
    with session_factory()() as db:
        job = db.get(Job, r.json()["job"])
        assert job.type == "backup.store_linked" and job.params["peer"] == "pi.lab"
        staged = stackbackup.incoming_dir(db) / f"pi.lab--{name}"
        assert staged.read_bytes() == data
        # The job: onto the primary file host (here the local one), indexed, older copies pruned.
        from labape.models import KvmHost
        h = db.get(KvmHost, 1)
        h.is_primary, h.vm_storage_path = True, str(tmp_path / "vms")
        db.commit()
        job.log_path = str(tmp_path / "job.log")
        old = {"archive": "labape-stack-director-20260901-030000.tar.sealed", "size": 1, "created_at": "2026-09-01",
               "received_at": "2026-09-01T03:00:00+00:00", "host": "kvm1", "path": str(tmp_path / "gone")}
        stackbackup._put(db, stackbackup.COPIES_KEY, {"https://pi.lab": [old]})
        assert stackbackup.store_linked(db, job) == 0
        dst = tmp_path / "vms" / ".labape-files" / "linked-backups" / "pi.lab" / name
        assert dst.read_bytes() == data and json.loads(dst.with_name(name + ".json").read_text())["received_from"] == "https://pi.lab"
        assert not staged.exists()
        copies = stackbackup._setting(db, stackbackup.COPIES_KEY)["https://pi.lab"]
        assert [c["archive"] for c in copies] == [name]                       # the September one is past 14 days
    m = client.get("/api/admin/backups/stack").json()
    assert m["copies"]["https://pi.lab"][0]["host"] == "kvm1" and m["sent"][0]["link"] == "pi.lab"
