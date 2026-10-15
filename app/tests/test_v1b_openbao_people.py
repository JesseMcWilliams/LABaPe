"""V1b (design §49.2): LABaPe keeps each environment's policy and owners group in OpenBao."""
from pathlib import Path

from labape import openbao_people as OP
from labape import stores
from labape.db import session_factory
from labape.models import Environment, Grant, RoleBinding, Secret

ROOT = Path(__file__).resolve().parents[2]


class FakeBao:
    """The OpenBao API calls the sync makes, in memory."""

    def __init__(self):
        self.policies: dict[str, str] = {"labape-env-old": "x", "default": "y"}
        self.entities: dict[str, dict] = {"entity_seen": {"id": "e-seen", "name": "entity_seen"}}
        self.aliases: dict[tuple[str, str], str] = {("entity", "uma"): "e-seen"}   # uma signed in before
        self.groups: dict[str, dict] = {"labape-env-old": {"id": "g-old", "policies": ["labape-env-old"]}}
        self.data = {"env/lab5/user/made-in-bao": "v", "env/lab5/vm/app1/key": "v"}
        self.calls: list[tuple[str, str]] = []

    def list(self, prefix):
        return sorted(k for k in self.data if k.startswith(prefix + "/"))

    def api(self, method, path, body=None, *, missing_ok=False):
        self.calls.append((method, path))
        if path == "sys/auth":
            return {"data": {"oidc/": {"accessor": "auth_oidc_1"}, "approle/": {"accessor": "a"}}}
        if path == "sys/policies/acl" and method == "LIST":
            return {"data": {"keys": sorted(self.policies)}}
        if path.startswith("identity/entity/name/"):
            name = path.rsplit("/", 1)[1]
            if method == "POST":
                self.entities.setdefault(name, {"id": f"e-{name}", "name": name})
            return {"data": self.entities[name]}
        if path == "identity/entity-alias":
            self.aliases[("entity", body["name"])] = body["canonical_id"]
            return {}
        if path == "identity/lookup/entity":
            eid = self.aliases.get(("entity", body["alias_name"]))
            return {"data": {"id": eid}} if eid else {}
        if path == "identity/group/name" and method == "LIST":
            return {"data": {"keys": sorted(self.groups)}}
        if path.startswith("identity/group/name/"):
            name = path.rsplit("/", 1)[1]
            if method == "POST":
                assert "policies" not in body               # stores.sh's, never LABaPe's
                self.groups.setdefault(name, {"id": f"g-{name}"}).update(body)
            return {"data": self.groups[name]}
        if path == "identity/group-alias":
            self.aliases[("group", body["name"])] = body["canonical_id"]
            return {}
        if path == "identity/lookup/group":
            gid = self.aliases.get(("group", body["alias_name"]))
            return {"data": {"id": gid}} if gid else {}
        raise AssertionError(f"unexpected {method} {path}")


def test_sync_keeps_the_owners_and_asks_for_the_policies(db_reset, monkeypatch):
    bao = FakeBao()
    asked = []
    monkeypatch.setattr(stores, "open_store", lambda db, name: bao)
    monkeypatch.setattr(OP, "enabled", lambda db: True)
    monkeypatch.setattr(OP.update, "agent_installed", lambda: True)
    monkeypatch.setattr(OP.update, "busy", lambda: False)
    monkeypatch.setattr(OP.update, "write_request", lambda action, target, actor, **kw: asked.append((action, kw)))
    with session_factory()() as db:
        e = Environment(name="lab5", kvm_host_id=1, created_by="admin", status="deployed",
                        spec={"host_groups": [{"name": "app", "os": "windows_server_2022", "roles": ["windows_server"]}]})
        other = Environment(name="lab6", kvm_host_id=1, created_by="admin", status="deployed", spec={"host_groups": []})
        db.add_all([e, other])
        db.commit()
        db.add_all([Secret(name=f"environment/{e.id}/windows_admin", value="", store="openbao"),
                    Secret(name=f"environment/{e.id}/user/deleted-in-bao", value="", store="openbao"),
                    Secret(name=f"environment/{other.id}/windows_admin", value="x", store="builtin"),   # not in OpenBao
                    Grant(object_type="environment", object_id=e.id, principal_type="user", principal="olive", level="owner"),
                    Grant(object_type="environment", object_id=e.id, principal_type="user", principal="uma", level="owner"),
                    Grant(object_type="environment", object_id=e.id, principal_type="group", principal="lab-team", level="owner"),
                    Grant(object_type="environment", object_id=e.id, principal_type="user", principal="viv", level="user"),
                    RoleBinding(principal_type="user", principal="root-ann", role="admin")])
        db.commit()
        out = OP.sync(db)
        env_id = e.id
        # The policies are stores.sh's: LABaPe asks the agent, naming exactly the environments in OpenBao
        # (also when a policy exists but its group doesn't carry it).
        assert asked == [("openbao_people", {"envs": "lab5"})]
        assert any("removing: labape-env-old" in line for line in out)
        # Owners: olive gets a new entity and alias, uma keeps the one OpenBao made, the group maps by name; viv isn't an owner.
        g = bao.groups["labape-env-lab5"]
        assert g["type"] == "internal"
        assert sorted(g["member_entity_ids"]) == ["e-labape-user-olive", "e-seen"]
        assert g["member_group_ids"] == ["g-labape-group-lab-team"]
        assert bao.groups["labape-group-lab-team"]["type"] == "external"
        assert bao.aliases[("group", "lab-team")] == "g-labape-group-lab-team"
        assert ("entity", "viv") not in bao.aliases
        # Admins: their members too.
        assert bao.groups["labape-admins"]["member_entity_ids"] == ["e-labape-user-root-ann"]
        # Secrets stored in OpenBao directly join the catalog; one deleted there leaves it.
        assert db.get(Secret, f"environment/{env_id}/user/made-in-bao") is not None
        assert db.get(Secret, f"environment/{env_id}/uservm/app1/key") is not None
        assert db.get(Secret, f"environment/{env_id}/user/deleted-in-bao") is None
        # Once the agent has written them: nothing to ask, nothing new to make.
        bao.policies = {"labape-env-lab5": "x", "labape-env-admins": "y", "default": "z"}
        bao.groups["labape-env-lab5"]["policies"] = ["labape-env-lab5"]
        bao.groups["labape-admins"]["policies"] = ["labape-env-admins"]
        before = len(bao.entities)
        OP.sync(db)
        assert len(bao.entities) == before and len(asked) == 1


def test_labape_keeps_members_but_never_policies():
    hcl = (ROOT / "container" / "stores" / "openbao" / "labape.hcl").read_text(encoding="utf-8")
    rules = [line for line in hcl[hcl.index("People's access"):].splitlines() if not line.startswith("#")]
    people = " ".join(rules)
    assert '"policies"' not in people                                 # can't put a policy on a group
    assert "sys/policies/acl/" not in people                          # nor write one
    assert 'path "identity/entity/name/labape-user-*"' in people
    assert "sys/auth/" not in people.replace('path "sys/auth"', "")    # reads the sign-in methods, can't add one
    stores_sh = (ROOT / "container" / "stores" / "stores.sh").read_text(encoding="utf-8")
    assert 'path "labape/data/env/$1/user/*"     { capabilities = ["create", "read", "update", "delete"] }' in stores_sh
    assert "^[a-z][a-z0-9-]{2,39}$" in stores_sh                      # the agent checks each name


def test_off_without_people_access(db_reset):
    with session_factory()() as db:
        assert OP.enabled(db) is False
        assert OP.sync(db) == [] and OP.schedule(db) == []
