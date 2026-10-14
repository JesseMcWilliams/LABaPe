"""S2: the Secrets page's sections and what an environment secret belongs to."""
from labape.api.secrets import classify
from labape.models import Environment


def _env():
    return Environment(id=4, name="lab4", status="deployed", kvm_host_id=1, created_by="t", spec={
        "host_groups": [{"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
                        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"], "count": 2},
                        {"name": "web", "os": "rocky9", "roles": ["linux_server"]}],
        "accounts": [{"name": "tester", "kind": "domain"}, {"name": "svc", "kind": "local", "hosts": ["linux_server"]}]})


def test_sections_and_environment_details():
    envs = {4: _env()}
    assert classify("platform/engine/ssh_key", envs) == {"section": "stack", "type": "key"}
    assert classify("platform/engine/vault_password", envs)["type"] == "password"
    assert classify("stores/openbao/secret_id", envs)["type"] == "key"
    w = classify("environment/4/windows_admin", envs)
    assert w["section"] == "environments" and w["environment"] == "lab4" and w["vms"] == ["app1", "app2"]
    assert w["scope"] == "local" and w["type"] == "password"
    assert classify("environment/4/domain_admin", envs)["vms"] == ["dc1"]
    assert classify("environment/4/account/tester", envs)["scope"] == "domain"
    svc = classify("environment/4/account/svc", envs)
    assert svc["scope"] == "local" and svc["vms"] == ["web1"]
    assert classify("environment/4/credentials", envs)["type"] == "document"
    assert classify("environment/4/rotation-candidate/windows_admin", envs)["attempt"] is True
    gone = classify("environment/9/inventory", envs)
    assert gone["environment"] == "#9 (gone)" and gone["vms"] == []


def test_a_destroy_clears_every_password_of_the_environment(db_reset):
    from labape.db import session_factory
    from labape.engine.vmaccess import clear_env_passwords
    from labape.security import get_secret, put_secret
    with session_factory()() as db:
        for name in ("environment/7/windows_admin", "environment/7/account/tester",
                     "environment/7/rotation-candidate/account-tester", "environment/8/account/tester"):
            put_secret(db, name, "pw")
        clear_env_passwords(db, 7)
        assert [get_secret(db, n) for n in ("environment/7/windows_admin", "environment/7/account/tester",
                                             "environment/7/rotation-candidate/account-tester")] == ["", "", ""]
        assert get_secret(db, "environment/8/account/tester") == "pw"          # another environment's stays


def test_the_recovery_accounts_sections():
    envs = {4: _env()}
    assert classify("environment/4/domain_admin2", envs)["vms"] == ["dc1"]
    assert classify("environment/4/domain_admin2", envs)["scope"] == "domain"
    w2 = classify("environment/4/windows_admin2", envs)
    assert w2["scope"] == "local" and w2["vms"] == ["app1", "app2"]
