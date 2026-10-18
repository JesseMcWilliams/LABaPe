"""Host groups: mixed-case names (compared without case) and one role per group."""
from test_api import _admin_session, _host

G = {"name": "Web", "os": "rocky9", "roles": ["linux_server"], "image_source": "packer_template", "template": "rocky9-base"}


def _create(client, name, groups):
    return client.post("/api/environments", json={"name": name, "kvm_host_id": 1, "host_groups": groups})


def test_a_group_name_may_mix_case_and_is_unique_without_case(client):
    _admin_session(client)
    _host(client)
    r = _create(client, "mixed1", [G])
    assert r.status_code == 201, r.text
    assert r.json()["environment"]["spec"]["host_groups"][0]["name"] == "Web"
    r = _create(client, "mixed2", [{**G, "name": "DB2"}, {**G, "name": "db2"}])
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.1.name"
    assert "without case" in r.json()["errors"][0]["message"]
    # the VM names (Web1 above) are host-wide and compared without case
    r = _create(client, "mixed3", [{**G, "name": "web"}])
    assert r.status_code == 422 and "already used" in r.json()["errors"][0]["message"]
    for bad in ("1web", "we-b", "ThirteenChars"):
        assert _create(client, "mixed4", [{**G, "name": bad}]).status_code == 422


def test_a_host_group_has_exactly_one_role(client):
    _admin_session(client)
    _host(client)
    r = _create(client, "roles1", [{**G, "name": "two", "roles": ["linux_server", "linux_workstation"]}])
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "host_groups.0.roles"
    assert _create(client, "roles2", [{**G, "name": "none", "roles": []}]).status_code == 422
