"""D185 (question 185): save a new environment as a draft, edit it, deploy it."""
from labape.db import session_factory
from labape.models import Environment, IpAllocation

from test_api import _admin_session, _host

G = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template", "template": "rocky9-base"}


def _draft(client, name="draft1", **kw):
    return client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": [G], "draft": True, **kw})


def test_a_draft_is_kept_without_a_job_or_addresses(client):
    _admin_session(client)
    _host(client)
    r = _draft(client)
    assert r.status_code == 201, r.text
    e = r.json()["environment"]
    assert r.json()["job_id"] is None and e["status"] == "new" and e["is_owner"] is True
    with session_factory()() as db:
        assert db.query(IpAllocation).filter_by(environment_id=e["id"]).count() == 0
    assert _draft(client).status_code == 409                       # the name is taken
    assert "environment.draft" in [a["action"] for a in client.get("/api/admin/audit").json()]


def test_a_draft_takes_no_quota_and_holds_no_vm_names(client):
    _admin_session(client)
    _host(client)
    assert _draft(client).status_code == 201
    # the same group (VM names web1) in a real environment is not blocked by the draft
    r = client.post("/api/environments", json={"name": "real1", "kvm_host_id": 1, "host_groups": [G]})
    assert r.status_code == 201, r.text


def test_editing_a_draft_and_deploying_it(client):
    _admin_session(client)
    _host(client)
    e = _draft(client).json()["environment"]
    r = client.put(f"/api/environments/{e['id']}/draft", json={"name": "draft2", "kvm_host_id": 1,
                                                               "host_groups": [{**G, "name": "App", "count": 2}]})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "draft2" and r.json()["spec"]["host_groups"][0]["name"] == "App"
    r = client.post(f"/api/environments/{e['id']}/deploy")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/jobs/{r.json()['job_id']}").json()["state"] == "queued"
    assert client.get(f"/api/environments/{e['id']}").json()["status"] == "deploying"
    # once deployed it is no longer a draft
    r = client.put(f"/api/environments/{e['id']}/draft", json={"name": "draft3", "kvm_host_id": 1, "host_groups": [G]})
    assert r.status_code == 409
