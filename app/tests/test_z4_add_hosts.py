"""Batch E2 (design §63.1): add hosts to an existing environment."""
from labape.db import session_factory
from labape.models import Environment, IpAllocation, Job

from test_api import _admin_session, _host

G = {"name": "web", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template", "template": "rocky9-base"}


def _env(client, name="addenv", **g):
    _admin_session(client)
    _host(client)
    r = client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": [{**G, **g}]})
    assert r.status_code == 201, r.text
    return r.json()["environment"]["id"]


def _settle(env_id, status="deployed"):
    """The create queued a deploy; pretend it finished."""
    with session_factory()() as db:
        for j in db.query(Job).filter_by(environment_id=env_id):
            j.state = "succeeded"
        db.get(Environment, env_id).status = status
        db.commit()


def _spec(env_id):
    with session_factory()() as db:
        return db.get(Environment, env_id).spec["host_groups"]


def test_more_hosts_in_a_group_queue_a_deploy(client):
    eid = _env(client)
    assert client.post(f"/api/environments/{eid}/hosts", json={"group": "web", "add": 1}).status_code == 409   # still deploying
    _settle(eid)
    r = client.post(f"/api/environments/{eid}/hosts", json={"group": "WEB", "add": 2, "names": ["", "Extra"]})
    assert r.status_code == 200, r.text
    g = _spec(eid)[0]
    assert g["count"] == 3 and g["names"] == ["", "", "Extra"]
    with session_factory()() as db:
        job = db.get(Job, r.json()["job_id"])
        assert job.type == "environment.deploy" and db.get(Environment, eid).status == "deploying"
        assert db.query(IpAllocation).filter_by(environment_id=eid).count() == 3         # an address for each new VM
    assert client.post(f"/api/environments/{eid}/hosts", json={"group": "web", "add": 1}).status_code == 409   # busy again


def test_a_new_group_copies_an_existing_one(client):
    eid = _env(client, "addenv2", cpu_count=3)
    _settle(eid)
    r = client.post(f"/api/environments/{eid}/hosts", json={"like": "web", "name": "Cache", "add": 2})
    assert r.status_code == 200, r.text
    groups = _spec(eid)
    assert [g["name"] for g in groups] == ["web", "Cache"]
    assert groups[1]["count"] == 2 and groups[1]["cpu_count"] == 3 and groups[1]["os"] == "rocky9"
    assert groups[0]["count"] == 1                                               # the source is untouched


def test_the_same_checks_as_a_new_environment(client):
    eid = _env(client, "addenv3")
    _settle(eid)
    url = f"/api/environments/{eid}/hosts"
    assert client.post(url, json={"group": "nope", "add": 1}).status_code == 404
    assert client.post(url, json={"group": "web", "like": "web", "add": 1}).status_code == 422       # one or the other
    assert client.post(url, json={"add": 1}).status_code == 422
    assert client.post(url, json={"like": "web", "add": 1}).status_code == 422                       # a copy needs a name
    r = client.post(url, json={"like": "web", "name": "WEB", "add": 1})
    assert r.status_code == 422 and "without case" in r.json()["errors"][0]["message"]
    assert client.post(url, json={"group": "web", "add": 20}).status_code == 422                     # 21 in one group
    r = client.post(url, json={"group": "web", "add": 1, "names": ["bad name"]})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "names"
    r = client.post(url, json={"group": "web", "add": 2, "names": ["web1", ""]})                      # web1 is already a VM here
    assert r.status_code == 422 and "web1" in r.json()["errors"][0]["message"]
    assert _spec(eid)[0]["count"] == 1                                                                # nothing changed


def test_only_a_deployed_or_failed_environment(client):
    eid = _env(client, "addenv4")
    _settle(eid, "destroyed")
    r = client.post(f"/api/environments/{eid}/hosts", json={"group": "web", "add": 1})
    assert r.status_code == 409 and "deployed" in r.json()["detail"]
    _settle(eid, "failed")
    assert client.post(f"/api/environments/{eid}/hosts", json={"group": "web", "add": 1}).status_code == 200
