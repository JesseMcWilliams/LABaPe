"""U1 (design §50.7, question 170): Catalog → Updates."""
import datetime as dt
import io

import httpx
import pytest

from labape import appsettings
from labape.db import session_factory
from labape.engine import package_updates as pu
from labape.models import Job, StoredFile, UpdateSource

from test_api import _admin_session

DOTNET = {"releases": [{"release-version": "8.0.21",
                        "runtime": {"version": "8.0.21", "files": [
                            {"name": "dotnet-runtime-win-x64.exe", "rid": "win-x64", "url": "https://dl.example/dotnet-runtime-8.0.21-win-x64.exe", "hash": "AB" * 64},
                            {"name": "dotnet-runtime-linux-x64.tar.gz", "rid": "linux-x64", "url": "https://dl.example/dotnet-runtime-8.0.21-linux-x64.tar.gz", "hash": "cd" * 64}]},
                        "aspnetcore-runtime": {"version": "8.0.21", "files": [
                            {"name": "dotnet-hosting-win.exe", "rid": "", "url": "https://dl.example/dotnet-hosting-8.0.21-win.exe", "hash": "ef" * 64}]},
                        "sdk": {"version": "8.0.415", "files": [
                            {"name": "dotnet-sdk-win-x64.exe", "rid": "win-x64", "url": "https://dl.example/dotnet-sdk-8.0.415-win-x64.exe", "hash": "12" * 64}]}}]}


def handler(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    if "release-metadata/8.0/releases.json" in u:
        return httpx.Response(200, json=DOTNET)
    if u == "https://api.github.com/repos/keepassxreboot/keepassxc/releases/latest":
        return httpx.Response(200, json={"tag_name": "2.7.10", "assets": [
            {"name": "KeePassXC-2.7.10-Win64.msi", "browser_download_url": "https://gh.example/KeePassXC-2.7.10-Win64.msi",
             "digest": "sha256:" + "a1" * 32},
            {"name": "KeePassXC-2.7.10-src.tar.xz", "browser_download_url": "https://gh.example/src"}]})
    if u == "https://dl.google.com/chrome.msi":
        return httpx.Response(200, headers={"etag": '"v141"'})
    if u == "https://community.chocolatey.org/api/v2/package/putty":
        return httpx.Response(302, headers={"location": "https://packages.chocolatey.org/putty.0.83.0.nupkg"})
    if u == "https://packages.chocolatey.org/putty.0.83.0.nupkg":
        return httpx.Response(200)
    if u == "https://www.example.org/putty/latest.html":
        return httpx.Response(200, text='<a href="https://the.earth.li/~sgtatham/putty/latest/w64/putty-64bit-0.83-installer.msi">MSI</a>')
    return httpx.Response(404)


def client():
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def src(kind, **params):
    return UpdateSource(name=f"t-{kind}", display_name="T", kind=kind, params=params, targets=["windows"], keep=2,
                        enabled=True, history=[])


def test_resolvers(db_reset):
    with session_factory()() as db, client() as c:
        f = pu.resolve(db, src("dotnet", channel="8.0", product="sdk", rid="win-x64"), c)
        assert (f["version"], f["filename"], f["hash_type"], f["expected"]) == \
            ("8.0.415", "dotnet-sdk-8.0.415-win-x64.exe", "sha512", "12" * 64)
        assert pu.resolve(db, src("dotnet", channel="8.0", product="hosting", rid="win-x64"), c)["filename"] == \
            "dotnet-hosting-8.0.21-win.exe"
        assert pu.resolve(db, src("dotnet", channel="8.0", product="runtime", rid="linux-x64"), c)["expected"] == "cd" * 64
        g = pu.resolve(db, src("github", repo="keepassxreboot/keepassxc", asset="KeePassXC-*-Win64.msi"), c)
        assert (g["version"], g["filename"], g["expected"]) == ("2.7.10", "KeePassXC-2.7.10-Win64.msi", "a1" * 32)
        u = pu.resolve(db, src("url", url="https://dl.google.com/chrome.msi"), c)
        assert u["version"].startswith("d") and u["filename"] == "chrome.msi"
        assert pu.stored_name(src("url"), u) == f"chrome-{u['version']}.msi"          # the version added
        assert pu.resolve(db, src("chocolatey", package="putty"), c)["version"] == "0.83.0"
        page = pu.resolve(db, src("page", page="https://www.example.org/putty/latest.html",
                                  link_regex=r'href="([^"]*putty-64bit-([0-9.]+)-installer\.msi)"'), c)
        assert (page["version"], page["filename"]) == ("0.83", "putty-64bit-0.83-installer.msi")
        with pytest.raises(pu.UpdateError, match="no asset"):
            pu.resolve(db, src("github", repo="keepassxreboot/keepassxc", asset="*.dmg"), c)


def test_check_downloads_new_versions_and_keeps_n(db_reset, monkeypatch):
    removed = []

    class Store:
        def remove(self, path):
            removed.append(path)

    from labape import filestore
    monkeypatch.setattr(filestore, "current", lambda db: Store())
    versions = iter(["1.0", "1.0", "2.0", "3.0"])
    monkeypatch.setattr(pu, "resolve", lambda db, s, c=None: pu.Found(version=(v := next(versions)), url=f"https://x/app-{v}.msi",
                                                                      filename=f"app-{v}.msi", hash_type="sha256", expected=""))
    got = []

    def fetch(db, url, category, name, log, **kw):
        got.append((url, category, name, kw["targets"]))
        f = StoredFile(category=category, name=name, path=f"/data/installers/{name}", size=1, sha256=name, targets=kw["targets"])
        db.add(f)
        db.commit()
        return f

    with session_factory()() as db:
        s = src("url", url="https://x/app.msi")
        db.add(s)
        db.commit()
        log = io.StringIO()
        assert "downloaded 1.0" in pu.check(db, s, log, fetch=fetch)
        assert "already have it" in pu.check(db, s, log, fetch=fetch)
        pu.check(db, s, log, fetch=fetch)
        out = pu.check(db, s, log, fetch=fetch)
        assert "removed 1.0" in out and removed == ["/data/installers/app-1.0.msi"]
        assert [h["version"] for h in s.history] == ["3.0", "2.0"] and s.last_version == "3.0"
        assert got[0] == ("https://x/app-1.0.msi", "installers", "app-1.0.msi", ["windows"])


def test_schedule_and_due(db_reset):
    with session_factory()() as db:
        appsettings.put(db, "package_updates", appsettings.SECTIONS["package_updates"](enabled=True, day="sun", hour=3))
        sunday = dt.datetime(2026, 10, 18, 4, 0, tzinfo=dt.timezone.utc)
        assert pu.due(db, sunday)
        db.add(Job(type="files.update_check", params={"scheduled": True}, requested_by="system", created_at=sunday))
        db.commit()
        assert not pu.due(db, sunday + dt.timedelta(hours=1))
        assert pu.due(db, sunday + dt.timedelta(days=7))
        appsettings.put(db, "package_updates", appsettings.SECTIONS["package_updates"](enabled=False))
        assert not pu.due(db, sunday + dt.timedelta(days=7))


def test_the_api_and_the_seed(client):
    _admin_session(client)
    r = client.get("/api/package-updates").json()
    names = {s["name"] for s in r["sources"]}
    assert {"dotnet-8-runtime-win", "dotnet-10-sdk-win", "dotnet-10-hosting-win", "chrome-win", "keepassxc-win",
            "putty-win"} <= names
    assert r["schedule"]["day"] == "sun" and "page" in r["kinds"]
    bad = client.post("/api/package-updates", json={"name": "x1", "kind": "github", "params": {"repo": "a/b"}})
    assert bad.status_code == 422 and "asset" in bad.text
    assert client.post("/api/package-updates", json={"name": "x2", "kind": "url", "params": {"url": "http://plain"}}).status_code == 422
    new = client.post("/api/package-updates", json={"name": "notepadpp-win", "kind": "github", "keep": 2,
                                                    "params": {"repo": "notepad-plus-plus/notepad-plus-plus",
                                                               "asset": "npp.*.Installer.x64.exe"}})
    assert new.status_code == 201, new.text
    sid = new.json()["id"]
    assert client.put(f"/api/package-updates/{sid}", json={**new.json(), "enabled": False}).json()["enabled"] is False
    r = client.post("/api/package-updates/check", json={"source_id": sid})
    assert r.status_code == 200 and client.post("/api/package-updates/check", json={}).status_code == 409
    assert client.delete(f"/api/package-updates/{sid}").status_code == 200
