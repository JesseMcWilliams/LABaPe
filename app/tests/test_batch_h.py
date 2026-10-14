"""Batch H: detail endpoints, who sees which hosts and networks, branding."""
from labape.security import Principal, current_principal

from test_api import _admin_session, _host


def _as(client, *roles):
    client.app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=99, username="someone", display_name="", roles=set(roles))


def test_host_and_network_visibility(client):
    _admin_session(client)
    _host(client)
    r = client.post("/api/hosts", json={"name": "kvm2", "enabled": False})
    assert r.status_code == 201
    r = client.post("/api/networks", json={"name": "ops-only", "cidr": "10.9.0.0/24", "gateway": "10.9.0.1",
                                           "addressing": ["static"], "static_pools": ["10.9.0.10-10.9.0.50"],
                                           "allowed_roles": ["admin"]})
    assert r.status_code == 201, r.text
    n = r.json()
    assert client.get("/api/hosts/1").json()["name"] == "kvm1"
    assert client.get(f"/api/networks/{n['id']}").json()["name"] == "ops-only"
    _as(client, "deployer")
    assert [h["name"] for h in client.get("/api/hosts").json()] == ["kvm1"]       # not the disabled one
    assert client.get("/api/hosts/2").status_code == 404
    assert client.get(f"/api/networks/{n['id']}").status_code == 404              # not allowed to use it
    assert client.put("/api/hosts/1", json={"name": "x"}).status_code == 403
    _as(client, "viewer")
    assert client.get("/api/hosts").json() == []                                  # no environments
    client.app.dependency_overrides.clear()


def test_detail_endpoints_keep_literal_routes(client):
    _admin_session(client)
    r = client.post("/api/catalog", json={"kind": "application", "name": "drafty", "display_name": "Drafty",
                                          "definition": {"apt": "drafty"}})
    item = r.json()["id"]
    assert client.get(f"/api/catalog/{item}").json()["name"] == "drafty"
    assert client.get("/api/catalog/export").status_code == 200
    assert client.get("/api/catalog/requests").status_code == 200
    assert client.get("/api/files/roots").status_code == 200
    assert client.get("/api/files/999").status_code == 404
    assert client.get("/api/templates/999").status_code == 404
    _as(client, "viewer")
    assert client.get(f"/api/catalog/{item}").status_code == 404                  # drafts are admin-only
    client.app.dependency_overrides.clear()


def test_branding(client):
    assert client.get("/api/branding").json() == {"app_name": "LABaPe", "logo": None}   # public
    _admin_session(client)
    s = client.get("/api/admin/settings").json()["branding"]
    assert client.put("/api/admin/settings/branding", json={**s, "app_name": "<b>"}).status_code == 422
    assert client.put("/api/admin/settings/branding", json={**s, "app_name": "Team Lab"}).status_code == 200
    from labape import appsettings
    appsettings._cache.clear()
    assert client.get("/api/branding").json()["app_name"] == "Team Lab"
