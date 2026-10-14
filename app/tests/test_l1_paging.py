"""L1 (Planning_Lists-Secrets-Settings.md): the user's page size and the Jobs list's pages."""
from labape.db import session_factory
from labape.models import Job, User

from test_api import _admin_session


def test_page_size_is_kept_per_user(client):
    with session_factory()() as db:
        db.add(User(provider="oidc", subject="s-ann", username="ann"))
        db.commit()
        uid = db.query(User).filter_by(username="ann").one().id
    from labape.security import Principal, current_principal
    client.app.dependency_overrides[current_principal] = lambda: Principal(user_id=uid, username="ann", display_name="Ann",
                                                                        roles={"viewer"})
    assert client.get("/api/auth/me/preferences").json() == {"page_size": 50}          # the default
    assert client.put("/api/auth/me/preferences", json={"page_size": 120}).json()["page_size"] == 120
    assert client.get("/api/auth/me/preferences").json()["page_size"] == 120
    assert client.put("/api/auth/me/preferences", json={"page_size": 2}).status_code == 422
    assert client.put("/api/auth/me/preferences", json={"page_size": 5000}).status_code == 422


def test_jobs_page_and_total(client):
    _admin_session(client)
    with session_factory()() as db:
        db.add_all([Job(type="files.download", requested_by="admin", state="succeeded") for _ in range(7)])
        db.commit()
    r = client.get("/api/jobs?limit=3&offset=0")
    assert r.headers["x-total-count"] == "7" and len(r.json()) == 3
    first = [j["id"] for j in r.json()]
    second = [j["id"] for j in client.get("/api/jobs?limit=3&offset=3").json()]
    assert first == sorted(first, reverse=True) and max(second) < min(first)
    assert len(client.get("/api/jobs?limit=3&offset=6").json()) == 1
    assert client.get("/api/jobs?limit=3&state=failed").headers["x-total-count"] == "0"
