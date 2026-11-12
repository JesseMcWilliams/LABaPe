"""The install steps. Each takes the run context and returns (status, message),
or raises StepError. Every step checks what's already there first, so
re-running is safe. The shared pieces live in steps_base.py and the web interface steps in steps_stack.py;
this module re-exports them, so `labape_deploy.steps` is still the one place the installer and the tests look."""
from __future__ import annotations

from .steps_base import *  # noqa: F401,F403
from .steps_base import _catalog, _installed  # noqa: F401
from .steps_stack import *  # noqa: F401,F403
from .steps_stack import _get_env, _set_env, _wait_health  # noqa: F401

# --- prerequisites ------------------------------------------------------------

def prereq_os(c: Ctx):
    if os.geteuid() != 0:
        if not c.r.dry_run:
            raise StepError("run the installer as root (sudo)")
        print("note: not root; a dry run can still show the plan")
    info = os_release()
    fam = os_family(info)
    if not fam:
        raise StepError(f"Debian (or a derivative) or the RHEL family (Rocky, Alma 9+) is required; "
                        f"this is {info.get('PRETTY_NAME', 'unknown')}")
    if fam == "rhel":
        if c.a.need("Profile") != "kvm":
            raise StepError("on the RHEL family only the kvm profile is supported so far: run the web stack on Debian")
        if int((info.get("VERSION_ID", "0").split(".")[0] or 0)) < 9:
            raise StepError(f"RHEL-family hosts need version 9 or later; this is {info.get('PRETTY_NAME')}")
    if kvm_side(c.a) and not Path("/dev/kvm").exists():
        raise StepError("/dev/kvm is missing: enable virtualization (VT-x/AMD-V) in firmware")
    return DONE, info.get("PRETTY_NAME", "")


def prereq_user(c: Ctx):
    user = c.user
    if not c.r.out(["id", "-u", user]):
        c.r.run(["useradd", "--create-home", "--shell", "/bin/bash", user])
    return DONE, f"service user {user}"




SENSORS_DONE = Path("/var/lib/labape/sensors-detected")


def detect_sensors(c: Ctx) -> str:
    """lm-sensors' own probe, once (question 173): it loads the drivers for the chips it finds, so
    `sensors` shows temperatures without anyone answering its questions."""
    if SENSORS_DONE.exists() or not shutil.which("sensors-detect"):
        return ""
    c.r.run(["sensors-detect", "--auto"], check=False)
    c.r.write(SENSORS_DONE, "lm-sensors' sensors-detect --auto ran (LABaPe Prereq.Packages)\n")
    return "; sensors detected"


def prereq_packages(c: Ctx):
    status, note = _prereq_install(c)
    return status, note + detect_sensors(c)


def _prereq_install(c: Ctx):
    if os_family() == "rhel":
        missing = [p for p in rpm_packages() if not _installed(c, p)]
        if not missing:
            return DONE, "all present"
        c.r.run(["dnf", "install", "-y", "-q", *missing])
        return DONE, f"installed {', '.join(missing)}"
    missing = [p for p in (APT_PACKAGES if kvm_side(c.a) else WEB_PACKAGES) if not _installed(c, p)]
    if not missing:
        return DONE, "all present"
    resolv = RESOLV
    keep_dns = "network-manager" in missing and resolv.is_file() and not resolv.is_symlink()
    saved = resolv.read_text(encoding="utf-8", errors="replace") if keep_dns else ""
    if keep_dns:
        # Written before the package starts NetworkManager: see NM_DNS_NONE.
        c.r.write(Path("/etc/NetworkManager/conf.d/90-labape-dns.conf"), NM_DNS_NONE)
    c.r.run(["apt-get", "update"], env={"DEBIAN_FRONTEND": "noninteractive"})
    c.r.run(["apt-get", "install", "-y", "--no-install-recommends", *missing], env={"DEBIAN_FRONTEND": "noninteractive"})
    if keep_dns and "nameserver" in saved and "nameserver" not in resolv.read_text(encoding="utf-8", errors="replace"):
        c.r.write(resolv, saved)
    return DONE, f"installed {', '.join(missing)}"


# A host set up with ifupdown or cloud-init (e.g. a Debian cloud image) gets its DNS servers from
# them. NetworkManager, installed for the VM bridges, would rewrite /etc/resolv.conf from the
# connections it manages, which don't include the management NIC, and leave it with no nameserver
# (seen on a nested KVM host: pip then found no packages). Kept for any host that had no
# NetworkManager before.
RESOLV = Path("/etc/resolv.conf")
NM_DNS_NONE = """# LABaPe (Prereq.Packages): this host's DNS settings came from before NetworkManager was
# installed for the VM bridges; NetworkManager leaves /etc/resolv.conf alone.
[main]
dns=none
"""


MODULAR = ("qemu", "network", "nodedev", "nwfilter", "secret", "storage", "interface", "proxy")


def prereq_libvirt(c: Ctx):
    if "virtqemud.socket" in c.r.out(["systemctl", "list-unit-files", "virtqemud.socket"]):
        # RHEL 9+ (and newer Debian builds that ship them): libvirt's modular daemons, started by their
        # sockets. virtproxyd serves qemu+ssh clients that use the classic libvirt-sock.
        units = [f"virt{d}d{s}.socket" for d in MODULAR for s in ("", "-ro", "-admin")]
        have = c.r.out(["systemctl", "list-unit-files", "--no-legend", *units])
        c.r.run(["systemctl", "enable", "--now", *[u for u in units if u in have]])
        how = "modular daemons"
    else:
        c.r.run(["systemctl", "enable", "--now", "libvirtd"])
        how = "libvirtd"
    c.r.run(["usermod", "-aG", "libvirt,kvm", c.user])
    return DONE, f"{how}; {c.user} in libvirt, kvm"


SLEEP_TARGETS = ["sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target",
                 "suspend-then-hibernate.target"]
NO_SLEEP_LOGIND = """# LABaPe: this host runs VMs or the web stack and must never sleep. The sleep
# targets are masked too (Prereq.NoSleep), which also stops a desktop's idle
# suspend (e.g. the GDM login screen suspends after 15 minutes on AC).
[Login]
IdleAction=ignore
HandleSuspendKey=ignore
HandleHibernateKey=ignore
HandleLidSwitch=ignore
HandleLidSwitchExternalPower=ignore
HandleLidSwitchDocked=ignore
"""


def prereq_no_sleep(c: Ctx):
    changed = c.r.write(Path("/etc/systemd/logind.conf.d/90-labape-no-sleep.conf"), NO_SLEEP_LOGIND)
    if changed:
        c.r.run(["systemctl", "kill", "-s", "HUP", "systemd-logind"], check=False)   # re-read, keep sessions
    unmasked = [t for t in SLEEP_TARGETS
                if c.r.out(["systemctl", "show", "-p", "LoadState", "--value", t]) != "masked"]
    if unmasked:
        c.r.run(["systemctl", "mask", *unmasked])
    if not changed and not unmasked:
        return DONE, "already configured"
    return DONE, f"masked {', '.join(unmasked)}" if unmasked else "logind configured"


# A degraded software RAID is reported by `mdadm --monitor` by mail, and a host without a mail
# program drops the message (OZ's /data ran degraded for days, 2026-11-06). A PROGRAM line is run
# besides the mail, so the event also lands in syslog, which the stack's collector and the
# Syslog tab show.
SENDMAILS = [Path("/usr/sbin/sendmail"), Path("/usr/lib/sendmail"), Path("/usr/bin/sendmail")]
MSMTPRC = Path("/etc/msmtprc")
MSMTP_ALIASES = Path("/etc/msmtp-aliases")
MSMTPRC_MARK = "# LABaPe (Prereq.AlertMail)"


def has_sendmail() -> bool:
    return any(p.exists() for p in SENDMAILS)


def split_host_port(v: str) -> tuple[str, int]:
    host, _, port = str(v).strip().rpartition(":")
    return (host, int(port)) if host and port.isdigit() else (str(v).strip(), 1025)


def prereq_alert_mail(c: Ctx):
    """A mail program for the host's own mail (mdadm, smartd, cron), pointed at the stack's Mailpit, so
    a failing disk is read in the web interface's mail page. OZ had none: mdadm's alert went nowhere."""
    v = c.a.need("AlertMailHost")
    if not v:
        return NA, "no mail server given (AlertMailHost)"
    host, port = split_host_port(v)
    ours = MSMTPRC.is_file() and MSMTPRC.read_text(encoding="utf-8", errors="replace").startswith(MSMTPRC_MARK)
    if has_sendmail() and not ours:
        return DONE, "kept the mail program already here (it sends where it is set to; point it at Mailpit by hand)"
    if not has_sendmail():
        if os_family() == "rhel":
            c.r.run(["dnf", "install", "-y", "-q", "msmtp"], check=False)           # EPEL on Rocky and Alma
        else:
            c.r.run(["apt-get", "install", "-y", "--no-install-recommends", "msmtp-mta"],
                    env={"DEBIAN_FRONTEND": "noninteractive"}, check=False)
        if not has_sendmail() and not c.r.dry_run:
            return DONE, "no mail program could be installed (RHEL family: enable EPEL for msmtp); alerts go to syslog only"
    name = socket.gethostname()
    # Mailpit refuses a recipient without a domain, and mdadm and smartd write to plain "root": the aliases
    # file gives every local name a full address.
    c.r.write(MSMTP_ALIASES, f"{MSMTPRC_MARK}: every local recipient (root, ...) gets this address.\ndefault: root@{name}\n")
    c.r.write(MSMTPRC, f"""{MSMTPRC_MARK}: hand this host's mail to the stack's Mailpit.
defaults
auth off
tls off
syslog on
aliases {MSMTP_ALIASES}
account default
host {host}
port {port}
from root@{name}
""")
    return DONE, f"local mail goes to {host}:{port}"


SMARTD_CONFS = [Path("/etc/smartd.conf"), Path("/etc/smartmontools/smartd.conf")]      # Debian, RHEL


def prereq_smart_alerts(c: Ctx):
    """smartd warns of a failing disk (pending sectors, ATA errors) by running `mail`, and Debian's
    runner script needs /usr/bin/mail, which msmtp-mta doesn't provide: on OZ every warning failed with
    'Your system does not have /usr/bin/mail' (2026-11-02). Install the `mail` command on top of the
    mail program Prereq.AlertMail set up, so smartd's mail lands in the stack's Mailpit too."""
    if not c.r.which("smartctl"):
        return NA, "smartmontools isn't installed"
    if not has_sendmail():
        return DONE, "no mail program, so smartd's warnings stay in syslog"
    changed = False
    if not c.r.which("mail"):
        pkg = ["dnf", "install", "-y", "-q", "mailx"] if os_family() == "rhel" else \
              ["apt-get", "install", "-y", "--no-install-recommends", "bsd-mailx"]
        c.r.run(pkg, env=None if os_family() == "rhel" else {"DEBIAN_FRONTEND": "noninteractive"}, check=False)
        if not c.r.which("mail") and not c.r.dry_run:
            return DONE, "the mail command couldn't be installed (RHEL family: enable EPEL?); smartd's warnings stay in syslog"
        changed = True
    conf = next((p for p in SMARTD_CONFS if p.is_file()), None)
    text = conf.read_text(encoding="utf-8", errors="replace") if conf else ""
    active = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if conf and active and not any(" -m " in f" {ln} " for ln in active):
        # Not edited: the lines are the admin's. Without -m, smartd sends no mail at all.
        return DONE, f"{conf} has no '-m root' on its lines, so smartd sends no mail (add it by hand)"
    c.r.run(["systemctl", "enable", "--now", "smartd"], check=False)
    if changed:
        c.r.run(["systemctl", "try-restart", "smartd"], check=False)
        # A test message, so the first line in Mailpit shows the path works.
        c.r.run(f"echo 'smartd mail from {socket.gethostname()} is set up (LABaPe Prereq.SmartAlerts).' | "
                f"mail -s 'LABaPe: smartd mail test' root", check=False)
    return DONE, "smartd's warnings are mailed to root (the stack's Mailpit)" if changed else "already configured"


# plan S1: smartd runs a short self-test every Sunday at 02:00 and a long one on the 1st at 03:00. Debian ships
# DEVICESCAN with no schedule, so no drive was ever tested (OZ's failed drive: none in about 40,000 hours).
SMART_SCHEDULE = "-s (S/../../7/02|L/../01/./03)"


def prereq_smart_tests(c: Ctx):
    if not c.r.which("smartctl"):
        return NA, "smartmontools isn't installed"
    conf = next((p for p in SMARTD_CONFS if p.is_file()), None)
    if conf is None:
        return DONE, "no smartd.conf here, so no schedule was set"
    lines = conf.read_text(encoding="utf-8", errors="replace").splitlines()
    scan = [i for i, ln in enumerate(lines) if ln.split()[:1] == ["DEVICESCAN"]]
    if not scan:
        return DONE, f"{conf} has no DEVICESCAN line: add '-s ...' to your drives' lines by hand"
    if all(" -s " in f" {lines[i]} " for i in scan):
        return DONE, "already scheduled"
    for i in scan:
        if " -s " not in f" {lines[i]} ":
            lines[i] = f"{lines[i].rstrip()} {SMART_SCHEDULE}"
    lines.insert(scan[0], "# LABaPe (Prereq.SmartTests): weekly short and monthly long self-tests on every drive")
    c.r.write(conf, "\n".join(lines) + "\n")
    c.r.run(["systemctl", "try-restart", "smartd"], check=False)
    return DONE, "weekly short and monthly long self-tests scheduled"


MDSTAT = Path("/proc/mdstat")
MDADM_CONFS = [Path("/etc/mdadm/mdadm.conf"), Path("/etc/mdadm.conf")]      # Debian, RHEL
RAID_EVENT_PROGRAM = Path("/usr/local/sbin/labape-raid-event")
RAID_EVENT_SCRIPT = """#!/bin/sh
# LABaPe (Prereq.RaidAlerts): mdadm --monitor runs this for every array event with the event,
# the array and, for some events, the member. It writes the event to syslog.
event="$1"; array="$2"; member="$3"
case "$event" in
    Fail|FailSpare|DegradedArray|DeviceDisappeared|SparesMissing) prio=daemon.crit ;;
    Rebuild*|SpareActive|NewArray|TestMessage|MoveSpare) prio=daemon.notice ;;
    *) prio=daemon.warning ;;
esac
exec logger -t labape-raid -p "$prio" "mdadm: $event on $array${member:+ ($member)}"
"""


def md_arrays(mdstat: Path | None = None) -> list[str]:
    try:
        text = (mdstat or MDSTAT).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [ln.split()[0] for ln in text.splitlines() if ln.startswith("md") and " : " in ln]


def prereq_raid_alerts(c: Ctx):
    if not c.r.which("mdadm") or not md_arrays():
        return NA, "no software RAID arrays"
    conf = next((p for p in MDADM_CONFS if p.is_file()), MDADM_CONFS[0])
    lines = conf.read_text(encoding="utf-8", errors="replace").splitlines() if conf.is_file() else []
    ours = f"PROGRAM {RAID_EVENT_PROGRAM}"
    others = [ln.strip() for ln in lines if ln.split()[:1] == ["PROGRAM"] and ln.strip() != ours]
    if others:
        # mdadm runs one PROGRAM; a MAILADDR next to it is no conflict and stays as it is.
        print(f"{conf} already has: {others[0]}")
        if not c.a.need("ReplaceRaidProgram"):
            return DONE, f"kept the existing '{others[0]}' (answer ReplaceRaidProgram=true to use LABaPe's)"
    changed = c.r.write(RAID_EVENT_PROGRAM, RAID_EVENT_SCRIPT, mode=0o755)
    kept = [ln for ln in lines if ln.strip() not in others]
    if ours not in kept:
        kept += ["", "# LABaPe (Prereq.RaidAlerts): array events also go to syslog", ours]
    if not any(ln.split()[:1] == ["MAILADDR"] for ln in kept):
        kept += ["MAILADDR root"]
    changed |= c.r.write(conf, "\n".join(kept).rstrip("\n") + "\n")
    if changed:
        c.r.run(["systemctl", "try-restart", "mdmonitor"], check=False)       # re-read the file
    mail = "mail is also sent" if has_sendmail() else "no mail program: syslog only"
    return DONE, f"{', '.join(md_arrays())} reported to syslog ({mail})" if changed else "already configured"


SCTERC_RULE = Path("/etc/udev/rules.d/90-labape-scterc.rules")
SCTERC_RULE_TEXT = """# LABaPe (Prereq.RaidDiskTimeouts): a SATA disk in a RAID array gives up on a bad sector after 7 seconds
# (the setting is lost at power-off), so md can rewrite it from the mirror instead of waiting out the disk's
# own retries (OZ's failing disk stalled md for 30+ seconds, 2026-11-02). Disks without support ignore it.
ACTION=="add|change", SUBSYSTEM=="block", KERNEL=="sd[a-z]*", ENV{DEVTYPE}=="disk", ENV{ID_BUS}=="ata", RUN+="%s -q errorsonly -l scterc,70,70 /dev/%%k"
"""


def prereq_raid_disk_timeouts(c: Ctx):
    smartctl = c.r.which("smartctl")
    if not smartctl or not md_arrays():
        return NA, "no software RAID arrays (or no smartctl)"
    changed = c.r.write(SCTERC_RULE, SCTERC_RULE_TEXT % smartctl)
    if not changed:
        return DONE, "already configured"
    c.r.run(["udevadm", "control", "--reload"], check=False)
    c.r.run(["udevadm", "trigger", "--subsystem-match=block", "--action=change"], check=False)
    return DONE, "disks in arrays give up on a bad sector after 7 seconds (udev rule)"


SUDOERS_DIR = Path("/etc/sudoers.d")
DIAG_SUDO_TEXT = """# LABaPe: the service user may read disk and array health without a password (smartctl, mdadm
# --detail and --examine). Read-only; nothing here can change a disk or an array.
%(user)s ALL=(root) NOPASSWD: %(smartctl)s *, %(mdadm)s --detail *, %(mdadm)s --examine *
"""


def prereq_diag_sudo(c: Ctx):
    smartctl, mdadm = c.r.which("smartctl"), c.r.which("mdadm")
    if not smartctl and not mdadm:
        return NA, "no smartctl or mdadm"
    user = c.user
    cmds = {"user": user, "smartctl": smartctl or "/bin/false", "mdadm": mdadm or "/bin/false"}
    final = SUDOERS_DIR / f"labape-diag-{user}"
    text = DIAG_SUDO_TEXT % cmds
    if final.is_file() and final.read_text(encoding="utf-8") == text:
        return DONE, "already configured"
    tmp = SUDOERS_DIR / f"labape-diag-{user}.tmp"       # sudo ignores names with a dot until it is checked
    c.r.write(tmp, text, mode=0o440)
    checked = c.r.run(["visudo", "-cf", str(tmp)], check=False)
    if getattr(checked, "returncode", 0) != 0 and not c.r.dry_run:
        tmp.unlink(missing_ok=True)
        raise StepError(f"the sudoers rule for {user} did not pass visudo; nothing was installed")
    c.r.run(["mv", str(tmp), str(final)])
    return DONE, f"{user} may run smartctl and mdadm --detail/--examine with sudo, no password"

# --- host networking ------------------------------------------------------------

BRIDGE_NF = """# LABaPe: libvirt VMs are bridged onto the LAN. Docker loads br_netfilter,
# which sends bridged frames through iptables, where Docker's FORWARD DROP
# policy drops them. Bridged traffic is not filtered on this host.
net.bridge.bridge-nf-call-iptables = 0
net.bridge.bridge-nf-call-ip6tables = 0
net.bridge.bridge-nf-call-arptables = 0
"""


def net_bridge_netfilter(c: Ctx):
    changed = c.r.write(Path("/etc/sysctl.d/90-labape-bridge-nf.conf"), BRIDGE_NF)
    changed |= c.r.write(Path("/etc/modules-load.d/labape-br_netfilter.conf"),
                         "# LABaPe: load before systemd-sysctl so 90-labape-bridge-nf.conf applies\nbr_netfilter\n")
    c.r.run(["modprobe", "br_netfilter"])
    c.r.run(["sysctl", "--system"], capture=True)
    return DONE, "configured" if changed else "already configured"


def _default_route_dev(c: Ctx) -> str:
    out = c.r.out(["ip", "-4", "route", "show", "default"])
    parts = out.split()
    return parts[parts.index("dev") + 1] if "dev" in parts else ""


def net_bridges(c: Ctx):
    nets = c.a.need("Networks")
    done = []
    for n in nets:
        br, nic = n["Bridge"], n.get("Nic", "")
        if c.r.out(["ip", "link", "show", br]):
            done.append(f"{br} exists")
            continue
        if not nic:
            raise StepError(f"network {n['Name']}: bridge {br} doesn't exist and no NIC was given to build it on")
        if nic == _default_route_dev(c):
            raise StepError(f"{nic} carries this host's default route; bridging it here could cut off management. "
                            "Create that bridge by hand, then re-run.")
        if not c.r.out(["systemctl", "is-active", "NetworkManager"]) == "active":
            raise StepError(f"NetworkManager isn't running; create bridge {br} on {nic} by hand, then re-run")
        ipv4 = (["ipv4.method", "auto", "ipv4.never-default", "yes"] if n.get("HostAddress", "dhcp") == "dhcp"
                else ["ipv4.method", "disabled"])
        c.r.run(["nmcli", "con", "add", "type", "bridge", "ifname", br, "con-name", br, "bridge.stp", "no",
                 *ipv4, "ipv6.method", "ignore"])
        c.r.run(["nmcli", "con", "add", "type", "bridge-slave", "ifname", nic, "master", br, "con-name", f"{br}-port"])
        old = c.r.out(["nmcli", "-g", "GENERAL.CONNECTION", "device", "show", nic])
        if old and old != f"{br}-port":
            c.r.run(["nmcli", "con", "mod", old, "connection.autoconnect", "no"])
            c.r.run(["nmcli", "con", "down", old], check=False)
        # The port too: NetworkManager doesn't bring it up with the bridge (autoconnect-slaves is off by
        # default), and an automatic "Wired connection" may still hold the NIC.
        c.r.run(["nmcli", "con", "up", f"{br}-port"], check=False)
        c.r.run(["nmcli", "con", "up", br])
        done.append(f"{br} created on {nic}")
    return DONE, "; ".join(done)


# --- storage ------------------------------------------------------------------

def storage_dirs(c: Ctx):
    for key in ("VmStoragePath", "TemplateStoragePath", "IsoPath"):
        path = Path(c.a.need(key))
        if not path.is_dir():
            c.r.run(["mkdir", "-p", str(path)])
    # Jobs write VM disks and templates as the service user (OpenTofu, promote, refresh):
    # those two folders are theirs. The ISO folder stays as it is (read by jobs only).
    for key in ("VmStoragePath", "TemplateStoragePath"):
        c.r.run(["chown", f"{c.user}:{c.user}", c.a.need(key)])
        c.r.run(["chmod", "0775", c.a.need(key)])
    # F1: this host's copies of LABaPe's files, beside the VM folder by default; jobs write them.
    files = str(c.a.need("FilesPath")).rstrip("/")
    for d in (files, f"{files}/ISOs", f"{files}/Installers", f"{files}/Other"):
        c.r.run(["install", "-d", "-m", "0775", "-o", c.user, "-g", c.user, d])
    note = ""
    if c.r.out(["getenforce"]) in ("Enforcing", "Permissive"):
        # SELinux (RHEL family): QEMU may only use files labelled for it: disks and templates
        # virt_image_t, installation media virt_content_t (read-only).
        for key, label in (("VmStoragePath", "virt_image_t"), ("TemplateStoragePath", "virt_image_t"),
                           ("IsoPath", "virt_content_t"), ("FilesPath", "virt_content_t")):
            spec = f"{str(c.a.need(key)).rstrip('/')}{'/ISOs' if key == 'FilesPath' else ''}(/.*)?"
            if c.r.run(["semanage", "fcontext", "-a", "-t", label, spec], check=False).returncode != 0:
                c.r.run(["semanage", "fcontext", "-m", "-t", label, spec])      # already defined: update it
            c.r.run(["restorecon", "-R", c.a.need(key) if key != "FilesPath" else f"{files}/ISOs"])
        note = " (SELinux labels set)"
    return DONE, ", ".join(str(c.a.need(k)) for k in ("VmStoragePath", "TemplateStoragePath", "IsoPath", "FilesPath")) + note


def storage_template_pool(c: Ctx):
    path = c.a.need("TemplateStoragePath")
    v = ["virsh", "--connect", "qemu:///system"]
    if not c.r.out(v + ["pool-info", TEMPLATE_POOL]):
        c.r.run(v + ["pool-define-as", TEMPLATE_POOL, "dir", "--target", path])
        c.r.run(v + ["pool-build", TEMPLATE_POOL], check=False)
    if "running" not in c.r.out(v + ["pool-info", TEMPLATE_POOL]):
        c.r.run(v + ["pool-start", TEMPLATE_POOL])
    c.r.run(v + ["pool-autostart", TEMPLATE_POOL])
    return DONE, f"{TEMPLATE_POOL} -> {path}"


# --- toolchain ------------------------------------------------------------------

def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


def _install_zip_binary(c: Ctx, name: str, version: str, base: str, zip_name: str, sums_name: str):
    have = c.r.out([f"/usr/local/bin/{name}", "version"])
    if version in have and not c.force:
        return DONE, f"{name} {version} already installed"
    if c.r.dry_run:
        print(f"[dry-run] download and verify {base}/{zip_name}, install /usr/local/bin/{name}")
        return DONE, "dry run"
    with tempfile.TemporaryDirectory() as tmp:
        z, s = Path(tmp) / zip_name, Path(tmp) / sums_name
        _download(f"{base}/{zip_name}", z)
        _download(f"{base}/{sums_name}", s)
        want = next((line.split()[0] for line in s.read_text().splitlines() if line.endswith(f" {zip_name}")), None)
        got = hashlib.sha256(z.read_bytes()).hexdigest()
        if want != got:
            raise StepError(f"{zip_name}: checksum mismatch (published {want}, got {got})")
        with zipfile.ZipFile(z) as zf:
            zf.extract(name, tmp)
        shutil.move(str(Path(tmp) / name), f"/usr/local/bin/{name}")
        os.chmod(f"/usr/local/bin/{name}", 0o755)
    return DONE, f"{name} {version} installed (checksum verified)"


def tools_opentofu(c: Ctx):
    v = c.a.need("TofuVersion")
    return _install_zip_binary(c, "tofu", v, f"https://github.com/opentofu/opentofu/releases/download/v{v}",
                               f"tofu_{v}_linux_{cpu_arch()}.zip", f"tofu_{v}_SHA256SUMS")


def tools_packer(c: Ctx):
    v = c.a.need("PackerVersion")
    status = _install_zip_binary(c, "packer", v, f"https://releases.hashicorp.com/packer/{v}",
                                 f"packer_{v}_linux_{cpu_arch()}.zip", f"packer_{v}_SHA256SUMS")
    for plugin in ("github.com/hashicorp/qemu", "github.com/hashicorp/ansible"):
        c.r.run(["packer", "plugins", "install", plugin], as_user=True, check=False)
    return status


def tools_ansible(c: Ctx):
    v = c.a.need("AnsibleCoreVersion")
    spec, want = f"ansible-core=={v}", f"core {v}"
    py = shutil.which("python3.12") if os_family() == "rhel" and shutil.which("python3.12") else "python3"
    if c.r.out([py, "-c", "import sys; print(int(sys.version_info >= (3, 12)))"]) == "0":
        # ansible-core 2.20+ needs Python 3.12; Debian 12 (bookworm, e.g. a Raspberry Pi web stack host)
        # has 3.11. Here it's only for the vault (jobs run Ansible on the KVM hosts or in the image).
        spec, want = "ansible-core>=2.19,<2.20", "core 2.19"
    have = c.r.out([str(VENV / "bin" / "ansible"), "--version"])
    if want not in have or c.force:
        if not (VENV / "bin" / "pip").exists():
            # A venv made without python3-venv has python but no pip: start over.
            c.r.run(["rm", "-rf", str(VENV)])
            # ansible-core needs Python 3.12 or later: RHEL 9's own python3 is 3.9, so use python3.12 there.
            c.r.run([py, "-m", "venv", "--system-site-packages", str(VENV)])
        c.r.run([str(VENV / "bin" / "pip"), "install", "--quiet", spec, "pywinrm", "pyyaml"])
    for tool in ("ansible", "ansible-playbook", "ansible-vault", "ansible-galaxy", "ansible-inventory", "ansible-config"):
        link = Path("/usr/local/bin") / tool
        if not link.exists():
            c.r.run(["ln", "-sf", str(VENV / "bin" / tool), str(link)])
    # Galaxy is flaky (cache corruption, download timeouts): no cache, three attempts.
    for attempt in (1, 2, 3):
        res = c.r.run([str(VENV / "bin" / "ansible-galaxy"), "collection", "install", "--no-cache", "--timeout", "120",
                       "-p", "/usr/share/ansible/collections", *COLLECTIONS], capture=True, check=attempt == 3)
        if res.returncode == 0:
            break
        time.sleep(attempt * 15)
    return DONE, f"{spec.replace('==', ' ')} + collections in {VENV}"


# --- engine configuration --------------------------------------------------------

def _password(length: int = 24) -> str:
    """Meets Windows complexity; avoids characters that need escaping in
    YAML, XML (unattend) or a shell."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!%-_+="]
    chars = [secrets.choice(p) for p in pools]
    alphabet = "".join(pools)
    chars += [secrets.choice(alphabet) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def config_secrets(c: Ctx):
    vault = c.repo / "secrets.vault.yml"
    home = c.r.home()
    pass_file = home / ".labape-vault-pass"
    key = home / ".ssh" / "labape_bootstrap"
    if vault.is_file() and not c.force:
        return DONE, f"{vault} exists (kept)"
    if not pass_file.is_file():
        c.r.write(pass_file, secrets.token_urlsafe(32) + "\n", mode=0o600, owner=c.user)
    if not key.is_file():
        c.r.run(["mkdir", "-p", str(key.parent)], as_user=True)
        c.r.run(["ssh-keygen", "-q", "-t", "ed25519", "-f", str(key), "-N", "", "-C", "labape-bootstrap"], as_user=True)
    text = (c.repo / "secrets.vault.example.yml").read_text(encoding="utf-8")
    values = {
        "libvirt_uri": "qemu:///system",
        "ansible_ssh_private_key_path": str(key),
        "windows_bootstrap_admin_password": _password(),
        "domain_admin_password": _password(),
        "dsrm_password": _password(),
        "default_user_password": _password(),
    }
    out = []
    for line in text.splitlines():
        k = line.split(":", 1)[0]
        if k in values and not line.startswith((" ", "#")):
            line = f"{k}: {json.dumps(values[k])}"
        out.append(line)
    c.r.write(vault, "\n".join(out) + "\n", mode=0o600, owner=c.user)
    c.r.run(["ansible-vault", "encrypt", str(vault), "--vault-password-file", str(pass_file)], as_user=True)
    return DONE, f"{vault} written and encrypted (generated passwords live only in the vault)"




# How to recognize each OS's ISO in IsoPath (case-insensitive; the
# last match in name order wins, so newer point releases are preferred).
ISO_PATTERNS = {
    "rocky9": ["*rocky-9*minimal*.iso", "*rocky-9*.iso"],
    "windows_server_2019": ["*server*2019*.iso"],
    "windows_server_2022": ["*server*2022*.iso"],
    "windows_server_2025": ["*server*2025*.iso"],
    "windows_11": ["*windows*11*.iso"],
    "windows_10": ["*windows*10*.iso"],
    "ubuntu_lts": ["*ubuntu-24.04*live-server*.iso"],
    "ubuntu_26": ["*ubuntu-26.04*live-server*.iso"],
    "debian_latest": ["*debian-13*netinst*.iso"],
}


def find_isos(iso_dir: Path, example: dict) -> dict:
    import fnmatch
    files = sorted(p.name for p in iso_dir.iterdir() if p.is_file()) if iso_dir.is_dir() else []
    out = {}
    for key, default in example.items():
        match = None
        for pat in ISO_PATTERNS.get(key, []):
            hits = [f for f in files if fnmatch.fnmatch(f.lower(), pat)]
            if hits:
                match = hits[-1]
                break
        out[key] = str(iso_dir / (match or Path(default).name))
    return out


def config_environment(c: Ctx):
    import yaml  # python3-yaml, installed by Prereq.Packages
    path = c.repo / "tofu" / "environment.yml"
    if path.is_file() and not c.force:
        return DONE, f"{path} exists (kept; --force rewrites it)"
    example = yaml.safe_load((c.repo / "tofu" / "environment.example.yml").read_text(encoding="utf-8"))
    iso = c.a.need("IsoPath").rstrip("/")
    networks, default = _catalog(c)
    env = {
        "domain_name": c.a.need("DomainName"),
        "netbios_name": c.a.need("NetbiosName"),
        "image_source_default": "iso_direct",
        "network_mode": "bridged",
        "management_source": c.a.need("ManagementSource"),
        "default_network": default,
        "networks": networks,
        "software_store_path": "./software-store",
        "vm_storage_path": c.a.need("VmStoragePath"),
        "template_storage_path": c.a.need("TemplateStoragePath"),
        "os_iso_paths": find_isos(Path(iso), example.get("os_iso_paths", {})),
    }
    header = ("# Written by deploy/install-labape.py from the answers file (see tofu/environment.example.yml\n"
              "# for what each setting means). Edit freely; re-running the installer keeps it unless --force.\n")
    c.r.write(path, header + yaml.safe_dump(env, sort_keys=False), owner=c.user)
    missing = [k for k, v in env["os_iso_paths"].items() if not Path(v).is_file()]
    note = f"; ISOs not found yet: {', '.join(missing)}" if missing else ""
    return DONE, f"{path} written{note}"


def config_manifests(c: Ctx):
    done = []
    for name in ("software-manifest", "directory-manifest"):
        dst = c.repo / "ansible" / f"{name}.yml"
        src = c.repo / "ansible" / f"{name}.example.yml"
        if not dst.is_file() and src.is_file():
            c.r.run(["install", "-m", "0644", "-o", c.user, "-g", c.user, str(src), str(dst)])
            done.append(name)
    return DONE, f"copied {', '.join(done)} from the examples" if done else "already present"


# --- templates and checks ----------------------------------------------------------

def templates_build(c: Ctx):
    wanted = c.a.need("TemplatesToBuild")
    if not wanted:
        return NA, "none requested"
    built = []
    for item in wanted:
        os_key, _, rest = item.partition(":")
        name, _, flags = rest.partition(":")
        if (Path(c.a.need("TemplateStoragePath")) / f"{name}.qcow2").exists():
            built.append(f"{name} (exists)")
            continue
        cmd = ["bash", str(c.repo / "scripts" / "build-template.sh"), os_key, name] + (["--core"] if flags == "core" else [])
        c.r.run(cmd, as_user=True, cwd=c.repo)
        built.append(name)
    return DONE, ", ".join(built)


def smoke_cli(c: Ctx):
    if c.r.dry_run:
        return DONE, "skipped in a dry run (nothing was installed)"
    checks = {"libvirt": ["virsh", "--connect", "qemu:///system", "list", "--all"],
              "ansible": ["ansible", "--version"]}
    if c.a.answers.get("InstallCli", True):
        checks["tofu"] = ["tofu", "version"]
    for label, cmd in checks.items():
        if not c.r.out(cmd, as_user=True):
            raise StepError(f"{label} check failed as {c.user}: {' '.join(cmd)} "
                            "(a new group membership needs a fresh login)")
    env_yml = c.repo / "tofu" / "environment.yml"
    if not env_yml.is_file():                # a web-driven KVM host: its jobs bring their own (question 126)
        return DONE, f"{', '.join(checks)} OK (no command-line configuration on this host)"
    with tempfile.TemporaryDirectory() as tmp:
        shutil.chown(tmp, c.user, c.user)  # the render below runs as the service user
        c.r.run(["python3", str(c.repo / "scripts" / "lib" / "render_environment_tfvars.py"),
                 str(env_yml), tmp], as_user=True, capture=True, mutating=False)
    return DONE, f"{', '.join(checks)} and environment.yml OK"


def smoke_web(c: Ctx):
    if c.r.dry_run:
        return DONE, "skipped in a dry run (nothing was installed)"
    if not _wait_health(c, 30):
        raise StepError("the web interface isn't answering /api/health")
    status = c.r.out(c.compose + ["exec", "-T", "labape-api", "labape", "auth", "status"], cwd=c.repo / "container")
    unhealthy = [line for line in status.splitlines() if "UNHEALTHY" in line]
    if unhealthy:
        raise StepError("sign-in provider unhealthy: " + "; ".join(unhealthy))
    return DONE, f"https://{c.a.need('WebHostname')}/ healthy; providers: " + \
        ", ".join(line.split()[0] for line in status.splitlines() if line.strip())


def network_validate(c: Ctx):
    """Check the network answers before anything uses them."""
    nets = c.a.need("Networks")
    if not nets:
        raise StepError("at least one VM network is needed (Networks)")
    names = [n["Name"] for n in nets]
    if c.a.need("DefaultNetwork") not in names:
        raise StepError(f"DefaultNetwork {c.a.need('DefaultNetwork')!r} isn't one of {names}")
    for n in nets:
        net = ipaddress.IPv4Network(n["Cidr"], strict=False)
        if "static" in n.get("Addressing", ["static"]) and not n.get("StaticPools"):
            raise StepError(f"network {n['Name']}: static addressing needs StaticPools")
        for r in n.get("StaticPools", []) + n.get("DhcpRanges", []) + n.get("Reserved", []):
            for ip in str(r).split("-"):
                if ipaddress.IPv4Address(ip.strip()) not in net:
                    raise StepError(f"network {n['Name']}: {r} isn't inside {net}")
    return DONE, f"{len(nets)} network(s): {', '.join(names)}"
