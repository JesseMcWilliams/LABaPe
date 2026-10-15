"""Batch O1 (design §28): Jobs filters, names and facets without leaking other users' jobs;
User errors filters and export."""
import datetime as dt

from labape.db import session_factory
from labape.models import Environment, Job, UserError
from labape.security import Principal, current_principal

from test_api import _admin_session, _host


def _as(client, username, *roles):
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username=username, display_name="", roles=set(roles))


def _env(client, name):
    g = {"name": "".join(c for c in name if c.isalnum())[:12], "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template",
         "template": "rocky9-base"}
    r = client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": [g]})
    assert r.status_code == 201, r.text
    return r.json()["environment"]["id"]


def test_job_filters_names_and_visibility(client):
    _admin_session(client)
    _host(client)
    alpha, beta = _env(client, "alpha-web"), _env(client, "beta-db")
    with session_factory()() as db:
        db.add_all([Job(type="environment.destroy", environment_id=alpha, requested_by="admin", state="failed"),
                    Job(type="environment.refresh", environment_id=beta, requested_by="someone", state="succeeded"),
                    Job(type="template.build", requested_by="other", state="succeeded")])
        db.get(Environment, beta).status = "destroyed"
        db.commit()
        alpha_job = db.query(Job).filter(Job.environment_id == alpha).order_by(Job.id).first().id

    jobs = client.get("/api/jobs?environment=ALPHA").json()
    assert jobs and {j["environment_name"] for j in jobs} == {"alpha-web"}
    assert [j["state"] for j in client.get("/api/jobs?environment=alpha&state=failed").json()] == ["failed"]
    assert {j["type"] for j in client.get("/api/jobs?type=environment.re").json()} == {"environment.refresh"}
    assert [j["requested_by"] for j in client.get("/api/jobs?requested_by=other").json()] == ["other"]
    future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).isoformat()
    assert client.get("/api/jobs", params={"since": future}).json() == []
    beta_job = client.get("/api/jobs?environment=beta").json()[0]
    assert beta_job["environment_status"] == "destroyed"
    facets = client.get("/api/jobs/facets").json()
    assert {"alpha-web", "beta-db"} <= set(facets["environments"]) and "template.build" in facets["types"]

    # A deployer with no grants sees only their own job; filters and facets can't reveal the rest.
    _as(client, "someone", "deployer")
    mine = client.get("/api/jobs").json()
    assert [j["requested_by"] for j in mine] == ["someone"] and mine[0]["environment_name"] == "beta-db"
    assert client.get("/api/jobs?environment=alpha").json() == []
    assert client.get("/api/jobs?requested_by=other").json() == []
    assert client.get(f"/api/jobs/{alpha_job}").status_code == 404
    facets = client.get("/api/jobs/facets").json()
    assert facets["environments"] == ["beta-db"] and facets["requested_by"] == ["someone"]
    client.app.dependency_overrides.clear()


def test_user_error_filters_and_export(client):
    _admin_session(client)
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)
    with session_factory()() as db:
        db.add_all([UserError(username="ann", method="POST", path="/api/environments", status=422,
                              message="Name taken", origin="server"),
                    UserError(username="bob", method="", path="/environments/new", status=0,
                              message="Fill in the template", origin="browser", at=old)])
        db.commit()
    rows = lambda q: client.get(f"/api/admin/errors?{q}").json()["errors"]   # noqa: E731
    assert [r["username"] for r in rows("path=ENVIRONMENTS&origin=server")] == ["ann"]
    assert [r["username"] for r in rows("message=template")] == ["bob"]
    assert [r["username"] for r in rows("status=422")] == ["ann"]
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    assert "bob" not in [r["username"] for r in client.get("/api/admin/errors", params={"since": since}).json()["errors"]]
    r = client.get("/api/admin/errors/export?username=bob")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert [e["username"] for e in r.json()] == ["bob"]
    assert client.get("/api/admin/audit?action=errors.export").json()
    _as(client, "someone", "deployer")
    assert client.get("/api/admin/errors").status_code == 403
    assert client.get("/api/admin/errors/export").status_code == 403
    client.app.dependency_overrides.clear()


def test_audit_and_errors_page(client):
    _admin_session(client)
    with session_factory()() as db:
        db.add_all([UserError(username=f"u{i}", method="POST", path="/api/x", status=422, message="m", origin="server")
                    for i in range(7)])
        db.commit()
    r = client.get("/api/admin/errors?limit=3&offset=2").json()
    assert r["total"] >= 7 and len(r["errors"]) == 3
    assert [e["id"] for e in r["errors"]] == sorted((e["id"] for e in r["errors"]), reverse=True)
    first = client.get("/api/admin/errors?limit=3&offset=0").json()["errors"]
    assert first[2]["id"] == r["errors"][0]["id"]
    for _ in range(3):
        client.get("/api/admin/errors/export")                      # each export is audited
    a = client.get("/api/admin/audit?limit=2&offset=1")
    assert int(a.headers["X-Total-Count"]) >= 3 and len(a.json()) == 2
