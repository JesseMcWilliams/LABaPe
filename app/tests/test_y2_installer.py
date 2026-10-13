"""Batch Y2a (design §29.2): the installer reuses its answer file, offers each service,
derives the sign-in mode, skips the command-line setup on web-driven KVM hosts; Prepare
takes its networks from the catalog."""
import importlib.util
import sys
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
sys.path.insert(0, str(DEPLOY))
if "pwd" not in sys.modules:                 # the installer runs on Linux; these tests also run on Windows
    try:
        import pwd  # noqa: F401
    except ImportError:
        import types
        sys.modules["pwd"] = types.ModuleType("pwd")

from labape_deploy import answers as A  # noqa: E402
from labape_deploy import steps as S  # noqa: E402

from labape.db import session_factory  # noqa: E402
from labape.engine import hostadmin  # noqa: E402
from labape.models import HostNetwork, Job, KvmHost, Network  # noqa: E402

from test_api import _admin_session, _host  # noqa: E402


def _installer():
    spec = importlib.util.spec_from_file_location("install_labape", DEPLOY / "install-labape.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_services_override_parks_what_wasnt_chosen():
    assert S.services_override({}) is None                                  # everything on: no file
    text = S.services_override({"InstallSyslog": False, "InstallConjur": False, "InstallRemoteAccess": True})
    assert text.startswith(S.OVERRIDE_HEADER)
    for svc in ("labape-syslog-db", "labape-syslog", "labape-conjur", "labape-conjur-db", "labape-conjur-tls"):
        assert f'  {svc}:\n    profiles: ["off"]\n' in text
    assert "labape-guacd" not in text and "labape-openbao" not in text      # OpenBao always runs


def test_defaults_follow_profile_and_old_answer_files():
    a = A.Resolver(interactive=False)
    a.add({"Profile": "kvm"})
    assert a.need("CliCheckout") is False and a.need("InstallCli") is True
    inst = _installer()
    assert inst.cli_config(a) is False
    b = A.Resolver(interactive=False)
    b.add({"Profile": "full"})
    assert inst.cli_config(b) is True                                         # the stack copies the vault
    old = A.Resolver(interactive=False)
    old.add({"AuthMode": "existing"})                                         # a pre-Y2 answer file
    assert old.need("InstallAuthentik") is False
    assert A.Resolver(interactive=False).need("InstallAuthentik") is True
    assert A.ANSWERS["TemplatesToBuild"].ask is False                         # question 129
    assert A.ANSWERS["SyslogBind"].validate("10.0.0.5") is None and A.ANSWERS["SyslogBind"].validate("x")


def test_conditional_questions():
    inst = _installer()
    need = inst.CONDITIONAL_NEEDS
    a = A.Resolver(interactive=False)
    a.add({"InstallAuthentik": True, "InstallConjur": False, "InstallSyslog": False})
    assert need["Stack.Auth"](a) == [] and need["Stack.Stores"](a) == [] and need["Stack.Services"](a) == []
    b = A.Resolver(interactive=False)
    b.add({"InstallAuthentik": False, "AuthMode": "existing", "InstallConjur": True, "InstallSyslog": True})
    assert need["Stack.Auth"](b) == ["AuthMode", "OidcIssuer", "OidcClientId"]
    assert need["Stack.Stores"](b) == ["PlatformStore"] and need["Stack.Services"](b) == ["SyslogBind"]
    c = A.Resolver(interactive=False)
    c.add({"InstallAuthentik": False})
    assert c.need("AuthMode") == "test-authentik"                             # a throwaway for the first sign-in


def test_summary_marks_new_answers(monkeypatch):
    inst = _installer()
    a = A.Resolver(interactive=True)
    a.add({"Profile": "full", "Networks": [{"Name": "lab"}]})
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    a.need("InstallSyslog")
    text = inst.summary(a)
    assert "* InstallSyslog" in text and "  Profile" in text and "lab" in text
    assert a.prompted == ["InstallSyslog"]


def _net(db, name="lab-vlan48"):
    n = Network(name=name, cidr="172.21.48.0/22", gateway="", dns_servers=["172.21.48.1"],
                addressing=["static", "dhcp"], static_pools=["172.21.50.1-172.21.50.254"],
                dhcp_ranges=["172.21.48.16-172.21.49.254"], reserved=[], enabled=True)
    db.add(n)
    db.commit()
    return n.id


def test_prepare_takes_networks_from_the_catalog(client, monkeypatch):
    _admin_session(client)
    _host(client)
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        h.access_status = "ready"
        db.commit()
        nid, nid2 = _net(db), _net(db, "lab-vlan52")
    body = {"KvmHostName": "kvm2", "NetworkChoices": [
        {"network_id": nid, "Bridge": "br48", "Nic": "eno2"},
        {"network_id": nid2, "Bridge": "br52", "Nic": "eno3", "HostAddress": "172.21.52.10/22"}]}
    r = client.post("/api/hosts/1/install", json=body)
    assert r.status_code == 200, r.text
    with session_factory()() as db:
        ans = db.get(Job, r.json()["job_id"]).params["answers"]
    assert ans["DefaultNetwork"] == "lab-vlan48" and ans["CliCheckout"] is False
    n0 = ans["Networks"][0]
    assert (n0["Gateway"], n0["Bridge"], n0["HostAddress"], n0["StaticPools"]) == (
        "172.21.48.1", "br48", "dhcp", ["172.21.50.1-172.21.50.254"])
    assert ans["_NetworkIds"] == {"lab-vlan48": nid, "lab-vlan52": nid2}
    for bad, field in (({"KvmHostName": "kvm2"}, "NetworkChoices"),
                       ({"KvmHostName": "kvm2", "NetworkChoices": [{"network_id": 999, "Bridge": "b", "Nic": "e"}]},
                        "NetworkChoices"),
                       ({**body, "DefaultNetwork": "nope"}, "DefaultNetwork")):
        with session_factory()() as db:                  # one install job at a time: clear the queued one
            for j in db.query(Job):
                j.state = "succeeded"
            db.commit()
        r = client.post("/api/hosts/1/install", json=bad)
        assert r.status_code == 422 and r.json()["errors"][0]["field"] == field

    # After a successful install the host carries them (the first is its default).
    with session_factory()() as db:
        h = db.get(KvmHost, 1)
        db.query(HostNetwork).filter(HostNetwork.kvm_host_id == 1).delete()
        db.commit()
        assert hostadmin.attach_networks(db, h, ans) == ["lab-vlan48", "lab-vlan52"]
        rows = {hn.bridge: hn.is_default for hn in db.query(HostNetwork).filter(HostNetwork.kvm_host_id == 1)}
        assert rows == {"br48": True, "br52": False}
        assert hostadmin.attach_networks(db, h, ans) == []                       # already there


def test_stores_script_honours_conjur_off():
    text = (DEPLOY.parent / "container" / "stores" / "stores.sh").read_text(encoding="utf-8")
    assert 'STORE_SERVICES=(labape-openbao)' in text and '[ "$CONJUR_ON" = no ] || configure_conjur' in text
    assert 'dc up -d "${STORE_SERVICES[@]}"' in text and 'if sys.argv[5] != "no":' in text
