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
        assert f'  {svc}:\n    profiles: !override ["off"]\n' in text
    assert "labape-guacd:" + chr(10) + "    profiles" not in text and "labape-openbao:" + chr(10) + "    profiles" not in text   # OpenBao always runs


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
    assert need["Stack.Auth"](a) == ["AuthentikAdminUser"]                    # your own login; the email only once a user is named
    a.add({"AuthentikAdminUser": "jesse"})
    assert need["Stack.Auth"](a) == ["AuthentikAdminUser", "AuthentikAdminEmail"]
    assert need["Stack.Stores"](a) == [] and need["Stack.Services"](a) == []
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


def test_os_family_and_rhel_packages(tmp_path):
    rocky9 = {"ID": "rocky", "ID_LIKE": "rhel centos fedora", "VERSION_ID": "9.6", "PRETTY_NAME": "Rocky Linux 9.6"}
    assert S.os_family(rocky9) == "rhel"
    assert S.os_family({"ID": "almalinux", "ID_LIKE": "rhel centos fedora", "VERSION_ID": "10.0"}) == "rhel"
    assert S.os_family({"ID": "ubuntu", "ID_LIKE": "debian"}) == "debian"
    assert S.os_family({"ID": "arch"}) == ""
    pk9, pk10 = S.rpm_packages(rocky9), S.rpm_packages({**rocky9, "VERSION_ID": "10.0"})
    assert "python3.12" in pk9 and "python3.12" not in pk10              # ansible-core needs 3.12+
    assert {"qemu-kvm", "libvirt", "virt-install", "policycoreutils-python-utils", "smartmontools"} <= set(pk10)
    f = tmp_path / "os-release"
    f.write_text('NAME="Rocky Linux"\nID="rocky"\nID_LIKE="rhel centos fedora"\nVERSION_ID="9.6"\n# c\n')
    assert S.os_release(str(f))["ID_LIKE"] == "rhel centos fedora" and S.os_release(str(tmp_path / "x")) == {}


def test_installer_code_parses_as_python_3_9():
    import ast
    for p in [DEPLOY / "install-labape.py", *(DEPLOY / "labape_deploy").glob("*.py")]:
        src = p.read_text(encoding="utf-8")
        ast.parse(src, feature_version=(3, 9))                               # RHEL 9's python3
        assert "freedesktop_os_release(" not in src.replace("platform.freedesktop_os_release needs", "")


def test_arm_stack_host_override_and_arch(monkeypatch):
    text = S.services_override({}, kvm_device=False)
    assert "labape-worker:\n    devices: !reset []" in text and "profiles" not in text
    both = S.services_override({"InstallConjur": False}, kvm_device=False)
    assert 'labape-conjur:\n    profiles: !override ["off"]' in both and "devices: !reset []" in both
    assert S.services_override({}, kvm_device=True) is None
    import platform as _platform
    monkeypatch.setattr(_platform, "machine", lambda: "aarch64")
    assert S.cpu_arch() == "arm64"
    assert A.Resolver(interactive=False).need("InstallConjur") is False      # Conjur is amd64 only
    monkeypatch.setattr(_platform, "machine", lambda: "x86_64")
    assert S.cpu_arch() == "amd64" and A.Resolver(interactive=False).need("InstallConjur") is True


def test_no_sleep_step_masks_what_is_unmasked(tmp_path, monkeypatch):
    ran, wrote = [], {}

    class R:
        def write(self, path, content, mode=0o644, owner=None):
            changed = wrote.get(str(path)) != content
            wrote[str(path)] = content
            return changed

        def run(self, cmd, **kw):
            ran.append(cmd)

        def out(self, cmd, **kw):
            return "masked" if cmd[-1] in ("sleep.target", "hibernate.target") else "loaded"

    c = S.Ctx(r=R(), a=None, repo=Path("/opt/LABaPe"), deploy_root=Path("/opt/LABaPe/deploy"))
    status, msg = S.prereq_no_sleep(c)
    assert status == S.DONE and msg.startswith("masked suspend.target, hybrid-sleep")
    assert ["systemctl", "mask", "suspend.target", "hybrid-sleep.target", "suspend-then-hibernate.target"] in ran
    assert "IdleAction=ignore" in next(iter(wrote.values()))
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Prereq.NoSleep") < names.index("Net.Validate")


def test_installing_networkmanager_keeps_the_dns_servers(tmp_path, monkeypatch):
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("nameserver 172.21.48.1\n")
    monkeypatch.setattr(S, "RESOLV", resolv)
    monkeypatch.setattr(S, "os_family", lambda info=None: "debian")
    wrote = {}

    class R:
        def write(self, path, content, mode=0o644, owner=None):
            wrote[str(path)] = content
            if path == resolv:
                resolv.write_text(content)
            return True

        def run(self, cmd, **kw):
            if cmd[:2] == ["apt-get", "install"]:
                resolv.write_text("# Generated by NetworkManager\n")      # what the package did on nkvm1

        def out(self, cmd, **kw):
            return "" if "network-manager" in cmd[-1] else "install ok installed"

    class A:
        def need(self, name):
            return {"Profile": "kvm"}[name]

    c = S.Ctx(r=R(), a=A(), repo=Path("/opt/LABaPe"), deploy_root=Path("/opt/LABaPe/deploy"))
    S.prereq_packages(c)
    assert any(k.replace("\\", "/").endswith("conf.d/90-labape-dns.conf") and "dns=none" in v for k, v in wrote.items())
    assert resolv.read_text() == "nameserver 172.21.48.1\n"


def test_web_tls_step_only_with_the_platform_ca(tmp_path):
    ran = []

    class R:
        def run(self, cmd, **kw):
            ran.append((cmd, kw.get("as_user")))

        def out(self, cmd, **kw):
            return "1001"

    class A:
        def __init__(self, tls):
            self.tls = tls

        def need(self, name):
            return {"Tls": self.tls, "ServiceUser": "labape"}[name]

    c = S.Ctx(r=R(), a=A("internal"), repo=Path("/opt/LABaPe"), deploy_root=Path("/opt/LABaPe/deploy"))
    assert S.stack_web_tls(c)[0] == S.NA and ran == []
    c.a = A("labape-ca")
    assert S.stack_web_tls(c)[0] == S.DONE
    cmd, as_user = ran[-1]
    assert cmd[1].replace("\\", "/").endswith("container/caddy/web-tls.sh") and cmd[2] == "setup" and as_user
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Stack.Up") < names.index("Stack.WebTls")


def test_the_authentik_admin_password_is_asked_once_and_never_saved(monkeypatch):
    a = A.Resolver(interactive=True)
    a.add({"AuthentikAdminUser": "jesse"})
    answers = iter(["short", "ThisIsMy_FAKE_Password6!", "different", "ThisIsMy_FAKE_Password6!", "ThisIsMy_FAKE_Password6!"])
    monkeypatch.setattr(S.getpass, "getpass", lambda prompt="": next(answers))
    S.ask_authentik_admin_password(a)
    assert a.secret_inputs == {"AuthentikAdminPassword": "ThisIsMy_FAKE_Password6!"}
    assert "AuthentikAdminPassword" not in a.answers and "AuthentikAdminPassword" not in A.ANSWERS
    S.ask_authentik_admin_password(a)                                         # already have it: not asked again
    unattended = A.Resolver(interactive=False)
    unattended.add({"AuthentikAdminUser": "jesse"})
    monkeypatch.delenv("LABAPE_AUTHENTIK_ADMIN_PASSWORD", raising=False)
    S.ask_authentik_admin_password(unattended)
    assert unattended.secret_inputs == {}                                     # no terminal, no variable: skipped with a note
    monkeypatch.setenv("LABAPE_AUTHENTIK_ADMIN_PASSWORD", "ThisIsMy_FAKE_Password6!")
    S.ask_authentik_admin_password(unattended)
    assert unattended.secret_inputs["AuthentikAdminPassword"] == "ThisIsMy_FAKE_Password6!"
    none = A.Resolver(interactive=True)
    none.add({"AuthentikAdminUser": ""})
    S.ask_authentik_admin_password(none)
    assert none.secret_inputs == {}


def test_the_authentik_admin_user_is_checked():
    v = A.ANSWERS["AuthentikAdminUser"].validate
    assert v("jesse") is None and v("") is None
    for bad in ("akadmin", "Jesse", "j", "1abc", "a b"):
        assert v(bad)


def test_akadmins_first_password_is_stored_after_the_stack_is_up(tmp_path):
    """Stack.AuthentikAdmin (§54.3): after Stack.Up, akadmin's password goes into the store on stdin and its
    line in tools/authentik-test/.env is blanked; a different stored password is never replaced."""
    import subprocess
    env = tmp_path / "tools" / "authentik-test" / ".env"
    ran, rc = [], {"code": 0}

    class R:
        dry_run = False

        def run(self, cmd, **kw):
            ran.append((cmd, kw.get("input")))
            return subprocess.CompletedProcess(cmd, rc["code"], "", "")

        def write(self, path, content, mode=0o644, owner=None):
            Path(path).write_text(content, encoding="utf-8")
            return True

    class A:
        def need(self, name):
            return {"ContainerRuntime": "docker"}[name]

    c = S.Ctx(r=R(), a=A(), repo=tmp_path, deploy_root=tmp_path / "deploy")
    assert S.stack_authentik_admin(c)[0] == S.NA and ran == []                # no Authentik installed with LABaPe
    env.parent.mkdir(parents=True)
    env.write_text("PG_PASS=x\nAUTHENTIK_BOOTSTRAP_PASSWORD=ThisIsMy_FAKE_Password6!\nLABAPE_HOSTNAME=h\n")
    rc["code"] = 3
    status, msg = S.stack_authentik_admin(c)
    assert status == S.DONE and "already stored" in msg and "ThisIsMy_FAKE_Password6!" in env.read_text()
    rc["code"] = 0
    assert S.stack_authentik_admin(c)[0] == S.DONE
    cmd, given = ran[-1]
    assert cmd[-2:] == ["authentik-admin-import", "--keep-existing"] and given == "ThisIsMy_FAKE_Password6!\n"
    assert not any("ThisIsMy_FAKE_Password6!" in part for part in cmd)       # never on a command line
    assert env.read_text() == "PG_PASS=x\nAUTHENTIK_BOOTSTRAP_PASSWORD=\nLABAPE_HOSTNAME=h\n"
    ran.clear()
    assert S.stack_authentik_admin(c)[0] == S.DONE and ran == []              # nothing left to store
    c.backup = tmp_path / "backup.tar"
    assert S.stack_authentik_admin(c)[0] == S.NA
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Stack.Up") < names.index("Stack.AuthentikAdmin") < names.index("Stack.Restore")
