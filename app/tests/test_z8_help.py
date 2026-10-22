"""The Help page serves the user guides (backlog row 24)."""
from labape.config import get_settings

from test_api import _admin_session


def test_the_guides_are_listed_and_read(client, tmp_path, monkeypatch):
    docs = tmp_path / "help"
    docs.mkdir()
    (docs / "Web-Interface-Guide.md").write_text("# Using\n\n## 1. Start\n\nHello.\n", encoding="utf-8")
    (docs / "Web-Interface-Admin.md").write_text("# Admin\n", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "help_dir", docs)
    assert client.get("/api/help").status_code in (401, 403)                     # signed in only
    _admin_session(client)
    assert [d["id"] for d in client.get("/api/help").json()] == ["guide", "admin"]          # no command-line file here
    r = client.get("/api/help/guide")
    assert r.status_code == 200 and "## 1. Start" in r.json()["markdown"]
    assert client.get("/api/help/cli").status_code == 404 and client.get("/api/help/../x").status_code == 404


def test_the_checkout_serves_its_own_user_docs(client, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "help_dir", tmp_path / "absent")
    _admin_session(client)
    ids = [d["id"] for d in client.get("/api/help").json()]
    assert {"guide", "admin", "cli"} <= set(ids)
