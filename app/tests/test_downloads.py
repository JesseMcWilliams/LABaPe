"""Batch I: downloads from a URL, expected hashes on downloads and uploads."""
import hashlib
import http.server
import threading

import pytest

from labape.db import session_factory
from labape.engine import downloads
from labape.engine.runner import claim, run
from labape.files import set_roots
from labape.models import Job, StoredFile

from test_api import _admin_session
from test_files import _meta

DATA = b"installer bytes " * 4096


def test_parse_expected_and_urls():
    sha1 = hashlib.sha1(DATA).hexdigest()
    sha256 = hashlib.sha256(DATA).hexdigest()
    assert downloads.parse_expected(sha256.upper(), "sha256") == sha256
    assert downloads.parse_expected(f"{sha256}  tool.msi\n", "sha256") == sha256          # SHA256SUMS line
    assert downloads.parse_expected(f"SHA1 (tool.msi) = {sha1}", "sha1") == sha1          # BSD style
    for bad, kind in ((sha1, "sha256"), (f"{sha256} {sha256[::-1]}", "sha256"), (sha256, "crc32")):
        with pytest.raises(downloads.DownloadError):
            downloads.parse_expected(bad, kind)
    downloads.check_url("https://example.test/a.iso", allow_http=False)
    for url, allow in (("http://example.test/a.iso", False), ("ftp://example.test/a", True),
                       ("https://169.254.169.254/latest/meta-data", False), ("https://[fe80::1]/x", False)):
        with pytest.raises(downloads.DownloadError):
            downloads.check_url(url, allow)
    assert downloads.filename_from("https://x.test/dl/My%20Tool%201.2.msi?sig=1") == "My Tool 1.2.msi"


@pytest.fixture()
def server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", "/tool.msi")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(DATA)))
            self.end_headers()
            self.wfile.write(DATA)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _run_next():
    with session_factory()() as db:
        job = claim(db, "test")
    run(job.id, "test")
    with session_factory()() as db:
        return db.get(Job, job.id)


def test_download_job(client, tmp_root, server):
    _admin_session(client)
    with session_factory()() as db:
        set_roots(db, {"installers": str(tmp_root / "dl-installers")})
    body = {"category": "installers", "url": f"{server}/moved"}
    r = client.post("/api/files/download", json={**body, "filename": "tool.msi"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "url"           # http not allowed yet
    s = client.get("/api/admin/settings").json()["downloads"]
    assert client.put("/api/admin/settings/downloads", json={**s, "allow_http": True}).status_code == 200
    r = client.post("/api/files/download", json={**body, "filename": "../evil"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "filename"
    r = client.post("/api/files/download", json={**body, "filename": "bad.msi", "hash_type": "sha1",
                                                 "expected_hash": hashlib.sha1(b"other").hexdigest()})
    assert r.status_code == 200
    assert _run_next().state == "failed"                                               # SHA-1 mismatch
    assert not (tmp_root / "dl-installers" / "bad.msi").exists()
    r = client.post("/api/files/download", json={**body, "filename": "tool.msi", "hash_type": "sha1",
                                                 "expected_hash": f"{hashlib.sha1(DATA).hexdigest()}  tool.msi"})
    assert r.status_code == 200
    assert client.post("/api/files/download", json={**body, "filename": "tool.msi"}).status_code == 409  # queued
    job = _run_next()
    assert job.state == "succeeded"
    with session_factory()() as db:
        f = db.query(StoredFile).filter(StoredFile.name == "tool.msi").one()
        assert f.sha256 == hashlib.sha256(DATA).hexdigest() and f.size == len(DATA)    # internal hash: SHA-256
    assert client.post("/api/files/download", json={**body, "filename": "tool.msi"}).status_code == 409  # exists
    client.post("/api/files/download", json={**body, "filename": "copy.msi"})
    assert _run_next().state == "failed"                                               # same content


def test_upload_expected_hash(client, tmp_root):
    _admin_session(client)
    with session_factory()() as db:
        set_roots(db, {"files": str(tmp_root / "up-files")})
    data = b"some file"

    def start(expected, kind="md5"):
        return client.post("/api/files/tus", headers={
            "Tus-Resumable": "1.0.0", "Upload-Length": str(len(data)),
            "Upload-Metadata": _meta(filename="f.txt", category="files", hash_type=kind, expected_hash=expected)})

    assert start("not-a-hash").status_code == 422
    for expected, status in ((hashlib.md5(b"x").hexdigest(), 422), (hashlib.md5(data).hexdigest(), 204)):
        r = start(expected)
        assert r.status_code == 201
        r = client.patch(r.headers["location"], content=data, headers={
            "Tus-Resumable": "1.0.0", "Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"})
        assert r.status_code == status, r.text
    assert [f["name"] for f in client.get("/api/files?category=files").json()] == ["f.txt"]
