"""A1: a second admin next to each Administrator; each of a pair sets the other's password again."""
import io

from labape.db import session_factory
from labape.engine import rotation, secret_health, vmaccess
from labape.models import Environment, KvmHost, SecretPolicy
from labape.security import get_secret, put_secret

INV = {"all": {"vars": {"netbios_name": "LAB", "domain_name": "lab.test"},
               "hosts": {"dc1": {"ansible_connection": "winrm", "ansible_password": "dcpw"},
                         "app1": {"ansible_connection": "winrm", "ansible_password": "adminpw"}},
               "children": {"domain_controller": {"hosts": {"dc1": None}}}}}


def _env(db):
    db.merge(KvmHost(id=1, name="kvm1", libvirt_uri="qemu:///system", vm_storage_path="/v", template_storage_path="/t",
                     bridge="br1", concurrency_limit=2, enabled=True))
    e = Environment(name="pairlab", kvm_host_id=1, created_by="t", status="deployed", spec={"host_groups": [
        {"name": "dc", "os": "windows_server_2022", "roles": ["domain_controller"]},
        {"name": "app", "os": "windows_server_2022", "roles": ["windows_server"]}]})
    db.add(e)
    db.commit()
    return e


def test_passwords_before_the_deploy_and_the_rotators(db_reset):
    with session_factory()() as db:
        e = _env(db)
        assert vmaccess.ensure_recovery_passwords(db, e) == ["domain_admin2"]     # members' are per VM (A2)
        first = get_secret(db, f"environment/{e.id}/domain_admin2")
        assert len(first) == 20 and vmaccess.ensure_recovery_passwords(db, e) == []      # kept
        assert vmaccess.env_password_overrides(db, e.id)["labape_recovery_domain_password"] == first
        assert "domain_admin2" in rotation.ENV_KINDS and secret_health.HEALTH["domain_admin"].recoverable


def test_recovery_user_names():
    assert vmaccess.recovery_user(INV, "domain_admin2") == "labape-admin2"        # Basic auth, on a DC
    assert vmaccess.recovery_user(INV, "windows_admin2") == "labape-admin2"


def test_the_pair_resets_the_lost_one(db_reset, monkeypatch):
    calls = []

    def fake(inv, pattern, module, args, extra=None, **kw):
        calls.append((pattern, module, args.get("name"), {h: (v.get("ansible_user"), v.get("ansible_password"))
                                                            for h, v in inv["all"]["hosts"].items() if h in pattern}))
        out = vmaccess.Outcome({h: "ok" for h in pattern.split(":")})
        out.errors = {}
        return out
    monkeypatch.setattr(vmaccess, "_ansible", fake)
    monkeypatch.setattr(vmaccess, "_inventory", lambda db, env: INV)
    with session_factory()() as db:
        e = _env(db)
        put_secret(db, f"environment/{e.id}/domain_admin", "dcpw")
        put_secret(db, f"environment/{e.id}/domain_admin2", "pw2")
        log = io.StringIO()
        vmaccess.reset_with_pair(db, e, "domain_admin", log)                 # admin2 sets Administrator's
        pattern, module, name, creds = calls[-1]
        assert (pattern, module, name) == ("dc1", "microsoft.ad.user", "Administrator")
        assert creds["dc1"] == ("labape-admin2", "pw2")
        vmaccess.reset_with_pair(db, e, "domain_admin2", log)                # Administrator sets admin2's
        pattern, module, name, creds = calls[-1]
        assert name == "labape-admin2" and creds["dc1"] == (None, "dcpw")
        p = SecretPolicy(name="env.x.windows_admin2", kind="windows_admin2", label="x", interval_hours=168,
                         params={"environment_id": e.id})
        db.add(p)
        db.commit()
        put_secret(db, f"environment/{e.id}/windows_admin2", "lpw2")
        assert secret_health.HEALTH["windows_admin2"].check(db, p) == "1 host(s) accept the stored password"
        assert calls[-1][3]["app1"] == ("labape-admin2", "lpw2")
