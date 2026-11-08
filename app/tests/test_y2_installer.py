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


def _raid_ctx(tmp_path, monkeypatch, conf_text, replace=False, arrays=True, sendmail=False):
    mdstat = tmp_path / "mdstat"
    mdstat.write_text("Personalities : [raid10]\nmd10 : active raid10 sdb1[0] sdc1[2]\n      5860 blocks\n" if arrays
                      else "Personalities :\nunused devices: <none>\n")
    conf = tmp_path / "mdadm.conf"
    if conf_text is not None:
        conf.write_text(conf_text)
    prog = tmp_path / "labape-raid-event"
    monkeypatch.setattr(S, "MDSTAT", mdstat)
    monkeypatch.setattr(S, "MDADM_CONFS", [conf])
    monkeypatch.setattr(S, "RAID_EVENT_PROGRAM", prog)
    sm = tmp_path / "sendmail"
    if sendmail:
        sm.write_text("")
    monkeypatch.setattr(S, "SENDMAILS", [sm])
    ran = []

    class R:
        dry_run = False

        def which(self, name):
            return "/bin/x" if name == "mdadm" else None

        def write(self, path, content, mode=0o644, owner=None):
            if path.is_file() and path.read_text() == content:
                return False
            path.write_text(content)
            return True

        def run(self, cmd, **kw):
            ran.append(cmd)

    a = A.Resolver(interactive=False)
    a.add({"ReplaceRaidProgram": replace})
    return S.Ctx(r=R(), a=a, repo=tmp_path, deploy_root=tmp_path), conf, prog, ran


def test_raid_alerts_add_a_program_line_and_leave_mailaddr(tmp_path, monkeypatch):
    c, conf, prog, ran = _raid_ctx(tmp_path, monkeypatch, "HOMEHOST <system>\nMAILADDR root\nARRAY /dev/md/10 UUID=x\n")
    status, msg = S.prereq_raid_alerts(c)
    assert status == S.DONE and "md10" in msg and "syslog only" in msg
    text = conf.read_text()
    assert text.count("MAILADDR") == 1 and f"PROGRAM {prog}" in text and text.count("PROGRAM") == 1
    assert "logger -t labape-raid" in prog.read_text()
    assert ["systemctl", "try-restart", "mdmonitor"] in ran
    ran.clear()
    assert S.prereq_raid_alerts(c) == (S.DONE, "already configured") and not ran      # a second run changes nothing


def test_raid_alerts_ask_before_replacing_another_program(tmp_path, monkeypatch):
    c, conf, prog, _ = _raid_ctx(tmp_path, monkeypatch, "MAILADDR ops@example.test\nPROGRAM /usr/local/bin/mine\n")
    status, msg = S.prereq_raid_alerts(c)
    assert "kept the existing 'PROGRAM /usr/local/bin/mine'" in msg and not prog.exists()
    assert "mine" in conf.read_text()
    c, conf, prog, _ = _raid_ctx(tmp_path, monkeypatch, "MAILADDR ops@example.test\nPROGRAM /usr/local/bin/mine\n", replace=True)
    S.prereq_raid_alerts(c)
    text = conf.read_text()
    assert "mine" not in text and f"PROGRAM {prog}" in text and "MAILADDR ops@example.test" in text
    assert A.ANSWERS["ReplaceRaidProgram"].default is False                           # unattended runs keep the entry


def test_raid_alerts_skip_a_host_without_arrays(tmp_path, monkeypatch):
    c, conf, prog, ran = _raid_ctx(tmp_path, monkeypatch, None, arrays=False)
    assert S.prereq_raid_alerts(c)[0] == S.NA and not ran and not conf.exists()
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Prereq.RaidAlerts") < names.index("Net.Validate")


def test_raid_alerts_add_mailaddr_when_there_is_none(tmp_path, monkeypatch):
    c, conf, _prog, _ = _raid_ctx(tmp_path, monkeypatch, "ARRAY /dev/md/10 UUID=x\n", sendmail=True)
    status, msg = S.prereq_raid_alerts(c)
    assert "MAILADDR root" in conf.read_text() and "mail is also sent" in msg


def _mail_ctx(tmp_path, monkeypatch, host, sendmail=False, rhc=False):
    rc = tmp_path / "msmtprc"
    sm = tmp_path / "sendmail"
    if sendmail:
        sm.write_text("")
    monkeypatch.setattr(S, "MSMTPRC", rc)
    monkeypatch.setattr(S, "MSMTP_ALIASES", tmp_path / "msmtp-aliases")
    monkeypatch.setattr(S, "SENDMAILS", [sm])
    monkeypatch.setattr(S, "os_family", lambda info=None: "rhel" if rhc else "debian")
    monkeypatch.setattr(S.socket, "gethostname", lambda: "kvm9")
    ran = []

    class R:
        dry_run = False

        def run(self, cmd, **kw):
            ran.append(cmd)
            if cmd[0] == "apt-get":                       # the package brings the sendmail command
                sm.write_text("")

        def write(self, path, content, mode=0o644, owner=None):
            path.write_text(content)
            return True

    a = A.Resolver(interactive=False)
    a.add({"AlertMailHost": host})
    return S.Ctx(r=R(), a=a, repo=tmp_path, deploy_root=tmp_path), rc, ran


def test_alert_mail_installs_msmtp_and_points_it_at_mailpit(tmp_path, monkeypatch):
    c, rc, ran = _mail_ctx(tmp_path, monkeypatch, "172.21.20.11:1025")
    status, msg = S.prereq_alert_mail(c)
    assert ran[0][:3] == ["apt-get", "install", "-y"] and "msmtp-mta" in ran[0]
    text = rc.read_text()
    assert text.startswith(S.MSMTPRC_MARK) and "host 172.21.20.11\nport 1025\n" in text and "from root@kvm9" in text
    assert f"aliases {tmp_path / 'msmtp-aliases'}" in text and "default: root@kvm9" in (tmp_path / "msmtp-aliases").read_text()
    assert "172.21.20.11:1025" in msg
    assert S.split_host_port("stack.example") == ("stack.example", 1025)


def test_alert_mail_leaves_another_mail_program_alone(tmp_path, monkeypatch):
    c, rc, ran = _mail_ctx(tmp_path, monkeypatch, "172.21.20.11:1025", sendmail=True)
    status, msg = S.prereq_alert_mail(c)
    assert "kept the mail program" in msg and not ran and not rc.exists()
    rc.write_text(S.MSMTPRC_MARK + ": old\n")                # our own file from an earlier run: rewritten
    S.prereq_alert_mail(c)
    assert "host 172.21.20.11" in rc.read_text() and not ran


def test_alert_mail_needs_a_server_and_survives_rhel_without_epel(tmp_path, monkeypatch):
    c, rc, ran = _mail_ctx(tmp_path, monkeypatch, "")
    assert S.prereq_alert_mail(c)[0] == S.NA and not ran
    c, rc, ran = _mail_ctx(tmp_path, monkeypatch, "10.0.0.5:1025", rhc=True)
    status, msg = S.prereq_alert_mail(c)
    assert ran == [["dnf", "install", "-y", "-q", "msmtp"]] and "syslog only" in msg and not rc.exists()
    assert A.ANSWERS["AlertMailHost"].validate("10.0.0.5:1025") is None and A.ANSWERS["AlertMailHost"].validate("a b")
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Prereq.AlertMail") < names.index("Prereq.RaidAlerts")
    assert A.Resolver(interactive=False).need("MailBind") is not None


def _smart_ctx(tmp_path, monkeypatch, conf_text, have=("smartctl",), sendmail=True, rhc=False):
    sm = tmp_path / "sendmail"
    if sendmail:
        sm.write_text("")
    conf = tmp_path / "smartd.conf"
    if conf_text is not None:
        conf.write_text(conf_text)
    monkeypatch.setattr(S, "SENDMAILS", [sm])
    monkeypatch.setattr(S, "SMARTD_CONFS", [conf])
    monkeypatch.setattr(S, "os_family", lambda info=None: "rhel" if rhc else "debian")
    monkeypatch.setattr(S.socket, "gethostname", lambda: "kvm9")
    present, ran = set(have), []

    class R:
        dry_run = False

        def which(self, name):
            return "/bin/x" if name in present else None

        def write(self, path, content, mode=0o644, owner=None):
            path.write_text(content)
            return True

        def run(self, cmd, **kw):
            ran.append(cmd)
            if isinstance(cmd, list) and cmd[0] in ("apt-get", "dnf"):
                present.add("mail")

    return S.Ctx(r=R(), a=A.Resolver(interactive=False), repo=tmp_path, deploy_root=tmp_path), ran


def test_smart_alerts_install_the_mail_command_for_smartd(tmp_path, monkeypatch):
    c, ran = _smart_ctx(tmp_path, monkeypatch, "DEVICESCAN -a -m root -M exec /usr/share/smartmontools/smartd-runner\n")
    status, msg = S.prereq_smart_alerts(c)
    assert status == S.DONE and "mailed to root" in msg
    assert ran[0][:2] == ["apt-get", "install"] and "bsd-mailx" in ran[0]
    assert ["systemctl", "try-restart", "smartd"] in ran and any("smartd mail test" in str(r) for r in ran)
    ran.clear()
    assert S.prereq_smart_alerts(c) == (S.DONE, "already configured") and not any("mail test" in str(r) for r in ran)
    c, ran = _smart_ctx(tmp_path, monkeypatch, "DEVICESCAN -a\n", have=("smartctl", "mail"))
    assert "no '-m root'" in S.prereq_smart_alerts(c)[1]
    c, ran = _smart_ctx(tmp_path, monkeypatch, None, rhc=True)
    S.prereq_smart_alerts(c)
    assert ran[0] == ["dnf", "install", "-y", "-q", "mailx"]


def test_smart_alerts_skip_without_smartctl_or_a_mail_program(tmp_path, monkeypatch):
    c, ran = _smart_ctx(tmp_path, monkeypatch, None, have=())
    assert S.prereq_smart_alerts(c)[0] == S.NA and not ran
    (tmp_path / "sendmail").unlink(missing_ok=True)
    c, ran = _smart_ctx(tmp_path, monkeypatch, None, sendmail=False)
    assert "syslog" in S.prereq_smart_alerts(c)[1] and not ran
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Prereq.AlertMail") < names.index("Prereq.SmartAlerts") < names.index("Net.Validate")


def test_raid_disk_timeouts_write_a_udev_rule_only_with_arrays(tmp_path, monkeypatch):
    c, conf, prog, ran = _raid_ctx(tmp_path, monkeypatch, None)
    rule = tmp_path / "scterc.rules"
    monkeypatch.setattr(S, "SCTERC_RULE", rule)
    c.r.which = lambda name: "/usr/sbin/smartctl" if name == "smartctl" else None
    assert S.prereq_raid_disk_timeouts(c)[0] == S.DONE
    assert "/usr/sbin/smartctl -q errorsonly -l scterc,70,70 /dev/%k" in rule.read_text()
    assert ["udevadm", "trigger", "--subsystem-match=block", "--action=change"] in ran
    ran.clear()
    assert S.prereq_raid_disk_timeouts(c) == (S.DONE, "already configured") and not ran
    c, *_ = _raid_ctx(tmp_path, monkeypatch, None, arrays=False)
    c.r.which = lambda name: "/usr/sbin/smartctl"
    assert S.prereq_raid_disk_timeouts(c)[0] == S.NA


def test_diag_sudo_grants_read_only_disk_tools(tmp_path, monkeypatch):
    c, conf, prog, ran = _raid_ctx(tmp_path, monkeypatch, None)
    monkeypatch.setattr(S, "SUDOERS_DIR", tmp_path)
    monkeypatch.setattr(c.a, "need", lambda n: "labuser" if n == "ServiceUser" else "full")
    c.r.which = lambda name: f"/usr/sbin/{name}" if name in ("smartctl", "mdadm") else None
    status, msg = S.prereq_diag_sudo(c)
    assert status == S.DONE and "labuser" in msg
    assert ["visudo", "-cf", str(tmp_path / "labape-diag-labuser.tmp")] in ran
    assert ["mv", str(tmp_path / "labape-diag-labuser.tmp"), str(tmp_path / "labape-diag-labuser")] in ran
    text = (tmp_path / "labape-diag-labuser.tmp").read_text()
    assert "labuser ALL=(root) NOPASSWD: /usr/sbin/smartctl *, /usr/sbin/mdadm --detail *" in text
    (tmp_path / "labape-diag-labuser").write_text(text)
    ran.clear()
    assert S.prereq_diag_sudo(c) == (S.DONE, "already configured") and not ran
    c.r.which = lambda name: None
    assert S.prereq_diag_sudo(c)[0] == S.NA


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


def test_the_run_sets_its_own_umask_and_roots_home(monkeypatch):
    from labape_deploy import util
    seen = {}
    monkeypatch.setattr(util.os, "umask", lambda m: seen.setdefault("umask", m))
    env = {"HOME": "/home/caller"}
    util.settle_process(env, euid=0)
    assert seen["umask"] == 0o022 and env["HOME"] != "/home/caller"
    env = {"HOME": "/home/caller"}
    util.settle_process(env, euid=1000)                      # not root: HOME stays
    assert env["HOME"] == "/home/caller"


def test_repo_version_runs_git_as_the_checkouts_owner(monkeypatch, tmp_path):
    seen = {}

    class Done:
        stdout = "v0.5.4-10-g98f9cfb\n"

    def fake_run(cmd, **kw):
        seen.update(kw)
        return Done()

    class St:
        st_uid, st_gid = 1001, 1002

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    monkeypatch.setattr(S.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(S.pwd, "getpwuid", lambda uid: type("P", (), {"pw_dir": "/home/svc"})(), raising=False)
    monkeypatch.setattr(type(tmp_path), "stat", lambda self, **kw: St())
    assert S.repo_version(tmp_path) == "v0.5.4-10-g98f9cfb"
    assert seen["user"] == 1001 and seen["group"] == 1002 and seen["env"]["HOME"] == "/home/svc"


def test_smart_tests_are_scheduled_on_devicescan_once(tmp_path, monkeypatch):
    c, ran = _smart_ctx(tmp_path, monkeypatch, "# comment\nDEVICESCAN -d removable -n standby -m root -M exec /usr/share/smartmontools/smartd-runner\n")
    conf = tmp_path / "smartd.conf"
    status, msg = S.prereq_smart_tests(c)
    text = conf.read_text()
    assert status == S.DONE and "scheduled" in msg
    assert "smartd-runner -s (S/../../7/02|L/../01/./03)" in text and text.count("-s (") == 1 and "Prereq.SmartTests" in text
    assert ["systemctl", "try-restart", "smartd"] in ran
    ran.clear()
    assert S.prereq_smart_tests(c) == (S.DONE, "already scheduled") and not ran       # a second run changes nothing
    c, ran = _smart_ctx(tmp_path, monkeypatch, "/dev/sda -a -s L/../../1/03 -m root\n")
    assert "no DEVICESCAN" in S.prereq_smart_tests(c)[1] and (tmp_path / "smartd.conf").read_text().startswith("/dev/sda")
    c, ran = _smart_ctx(tmp_path, monkeypatch, "DEVICESCAN -a -s L/../../1/03\n")
    assert S.prereq_smart_tests(c) == (S.DONE, "already scheduled")                  # the admin's own schedule stays
    c, ran = _smart_ctx(tmp_path, monkeypatch, None, have=())
    assert S.prereq_smart_tests(c)[0] == S.NA
    names = [s[0] for s in _installer().STEPS]
    assert names.index("Prereq.SmartAlerts") < names.index("Prereq.SmartTests") < names.index("Net.Validate")
