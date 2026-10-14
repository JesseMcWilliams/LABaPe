"""The install steps. Each takes the run context and returns (status, message),
or raises StepError. Every step checks what's already there first, so
re-running is safe."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import secrets
import shutil
import string
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .answers import Resolver
from .util import Runner, StepError

DONE = "Completed"
NA = "NotApplicable"

APT_PACKAGES = [
    "qemu-system-x86", "qemu-utils", "libvirt-daemon-system", "libvirt-clients", "virtinst", "xorriso",
    "python3-yaml", "python3-venv", "python3-libvirt", "git", "curl", "unzip", "openssl", "iputils-ping",
    "network-manager", "perl", "smartmontools", "lm-sensors",
]
# A web-only install (--profile web): the container image carries the engine's tools.
WEB_PACKAGES = ["python3-yaml", "python3-venv", "git", "curl", "unzip", "openssl", "iputils-ping", "perl"]
# RHEL family (Rocky, Alma; question 128), kvm profile: the same tools under their dnf names, plus
# SELinux's labelling tool and needs-restarting. ansible-core needs Python 3.12 (Rocky 9's own is 3.9).
RPM_PACKAGES = [
    "qemu-kvm", "qemu-img", "libvirt", "libvirt-client", "virt-install", "xorriso", "python3-pyyaml",
    "python3-libvirt", "git", "curl", "unzip", "openssl", "iputils", "NetworkManager", "perl", "smartmontools",
    "lm_sensors", "policycoreutils-python-utils", "dnf-utils",
]


def os_release(path: str = "/etc/os-release") -> dict:
    """/etc/os-release as a dict (platform.freedesktop_os_release needs Python 3.10; RHEL 9 has 3.9)."""
    out = {}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def os_family(info: dict | None = None) -> str:
    """debian, rhel, or '' (unsupported)."""
    info = os_release() if info is None else info
    ids = {info.get("ID", "")} | set(info.get("ID_LIKE", "").split())
    if "debian" in ids:
        return "debian"
    if ids & {"rhel", "centos", "fedora", "rocky", "almalinux"}:
        return "rhel"
    return ""


def cpu_arch() -> str:
    """amd64 or arm64, as release downloads name them (a Raspberry Pi is arm64; design §29.6)."""
    m = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def rpm_packages(info: dict | None = None) -> list[str]:
    info = os_release() if info is None else info
    major = info.get("VERSION_ID", "").split(".")[0]
    return RPM_PACKAGES + (["python3.12", "python3.12-pip"] if major == "9" else [])


def kvm_side(a) -> bool:
    """Profiles that make this machine a KVM host (full, kvm)."""
    return a.need("Profile") in ("full", "kvm")
VENV = Path("/opt/labape/venv")
COLLECTIONS = ["ansible.windows:==3.8.0", "microsoft.ad:==1.12.1", "community.general:==13.4.0",
               "chocolatey.chocolatey:==1.6.0"]
TEMPLATE_POOL = "labape-templates"


@dataclass
class Ctx:
    r: Runner
    a: Resolver
    repo: Path
    deploy_root: Path
    force: bool = False
    # Install from a full-stack backup (design §33): the archive, its key, and a drill's
    # maintenance mode.
    backup: Path | None = None
    backup_key: Path | None = None
    restore_drill: bool = False

    @property
    def user(self) -> str:
        return self.a.need("ServiceUser")

    @property
    def compose(self) -> list[str]:
        return ["docker", "compose"] if self.a.need("ContainerRuntime") == "docker" else ["podman", "compose"]


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


def _installed(c: Ctx, pkg: str) -> bool:
    if os_family() == "rhel":
        # A missing package prints "package X is not installed" on stdout, so compare the name.
        return c.r.out(["rpm", "-q", "--qf", "%{NAME}", pkg]) == pkg
    # Exact match: "unknown ok not-installed" also ends with "installed".
    return c.r.out(["dpkg-query", "-W", "-f=${Status}", pkg]) == "install ok installed"


def prereq_packages(c: Ctx):
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
    note = ""
    if c.r.out(["getenforce"]) in ("Enforcing", "Permissive"):
        # SELinux (RHEL family): QEMU may only use files labelled for it: disks and templates
        # virt_image_t, installation media virt_content_t (read-only).
        for key, label in (("VmStoragePath", "virt_image_t"), ("TemplateStoragePath", "virt_image_t"),
                           ("IsoPath", "virt_content_t")):
            spec = f"{str(c.a.need(key)).rstrip('/')}(/.*)?"
            if c.r.run(["semanage", "fcontext", "-a", "-t", label, spec], check=False).returncode != 0:
                c.r.run(["semanage", "fcontext", "-m", "-t", label, spec])      # already defined: update it
            c.r.run(["restorecon", "-R", c.a.need(key)])
        note = " (SELinux labels set)"
    return DONE, ", ".join(str(c.a.need(k)) for k in ("VmStoragePath", "TemplateStoragePath", "IsoPath")) + note


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


def _catalog(c: Ctx) -> tuple[dict, str]:
    nets = {}
    for n in c.a.need("Networks"):
        nets[n["Name"]] = {
            "cidr": n["Cidr"], "gateway": n.get("Gateway", ""), "dns_servers": n.get("DnsServers", []),
            "bridge": n["Bridge"], "addressing": n.get("Addressing", ["static"]),
            "static_pools": n.get("StaticPools", []), "dhcp_ranges": n.get("DhcpRanges", []),
            "reserved": n.get("Reserved", []),
        }
    return nets, c.a.need("DefaultNetwork")


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


# --- web interface ----------------------------------------------------------------

def stack_runtime(c: Ctx):
    rt = c.a.need("ContainerRuntime")
    # Podman: `podman compose` drives the Docker Compose v2 binary against
    # Podman's API socket. podman-compose (the Python one) left compose.yaml's
    # ${VAR:-default} values unexpanded in the shared settings block.
    pkgs = ["docker.io", "docker-compose", "docker-buildx"] if rt == "docker" else ["podman", "docker-compose"]
    missing = [p for p in pkgs if not _installed(c, p)]
    if rt == "docker" and missing and "version" in c.r.out(["docker", "compose", "version"]).lower():
        # Docker installed some other way (Docker's own docker-ce packages, e.g. on a Raspberry Pi):
        # Debian's docker.io would conflict with it, and compose v2 is all the stack needs.
        missing = []
    if missing:
        c.r.run(["apt-get", "install", "-y", *missing], env={"DEBIAN_FRONTEND": "noninteractive"})
    if rt == "docker":
        c.r.run(["systemctl", "enable", "--now", "docker"])
        c.r.run(["usermod", "-aG", "docker", c.user])
    else:
        c.r.run(["systemctl", "enable", "--now", "podman.socket"])
    return DONE, f"{rt} ready" + (f" (installed {', '.join(missing)})" if missing else "")


def _set_env(c: Ctx, env_file: Path, values: dict) -> None:
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    for k, v in values.items():
        new = f"{k}={v}"
        for i, line in enumerate(lines):
            if line.startswith(f"{k}="):
                lines[i] = new
                break
        else:
            lines.append(new)
    c.r.write(env_file, "\n".join(lines) + "\n", mode=0o600, owner=c.user)


def _get_env(env_file: Path, key: str) -> str:
    """A KEY=value from container/.env, or '' when it's missing."""
    if not env_file.is_file():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""


def stack_setup(c: Ctx):
    cdir = c.repo / "container"
    tls = c.a.need("Tls")
    caddy_tls = "internal" if tls == "labape-ca" else tls      # the Caddyfile below ignores it for labape-ca
    c.r.run(["bash", str(cdir / "setup.sh"), "--hostname", c.a.need("WebHostname"), "--tls", caddy_tls],
            as_user=True, cwd=cdir)
    _set_env(c, cdir / ".env", {"LABAPE_HOSTNAME": c.a.need("WebHostname"), "LABAPE_TLS": caddy_tls,
                                "LABAPE_CADDYFILE": "Caddyfile.labape-ca" if tls == "labape-ca" else "Caddyfile",
                                "LABAPE_DATA_ROOT": "/" + Path(c.a.need("VmStoragePath")).parts[1]})
    home = c.r.home()
    copies = [
        (c.repo / "tofu" / "environment.yml", cdir / "config" / "environment.yml", 0o644),
        (c.repo / "ansible" / "software-manifest.yml", cdir / "config" / "software-manifest.yml", 0o644),
        (c.repo / "ansible" / "directory-manifest.yml", cdir / "config" / "directory-manifest.yml", 0o644),
        (c.repo / "secrets.vault.yml", cdir / "engine-secrets" / "secrets.vault.yml", 0o600),
        (home / ".labape-vault-pass", cdir / "engine-secrets" / "vault-pass", 0o600),
        (home / ".ssh" / "labape_bootstrap", cdir / "engine-secrets" / "ssh" / "labape_bootstrap", 0o600),
        (home / ".ssh" / "labape_bootstrap.pub", cdir / "engine-secrets" / "ssh" / "labape_bootstrap.pub", 0o644),
    ]
    if (cdir / "stores" / "secrets" / "platform_store_active").is_file():
        # The engine secrets live in the platform store now (design §26): a re-run (e.g. a changed
        # host name) mustn't put copies back on disk.
        copies = copies[:3]
    copied = 0
    for src, dst, mode in copies:
        if src.is_file():
            c.r.run(["install", "-D", "-m", oct(mode)[2:], "-o", c.user, "-g", c.user, str(src), str(dst)])
            copied += 1
    return DONE, f"container/.env and secrets in place; {copied} config/secret files copied"


# Optional services (question 140): answer -> the compose services it adds. OpenBao is always there.
OPTIONAL_SERVICES = {
    "InstallConjur": ("labape-conjur-db", "labape-conjur", "labape-conjur-tls"),
    "InstallSyslog": ("labape-syslog-db", "labape-syslog"),
    "InstallRemoteAccess": ("labape-guacd",),
}
OVERRIDE_HEADER = "# Written by deploy/install-labape.py (Stack.Services). Don't edit: re-run the installer.\n"


def services_override(answers: dict, kvm_device: bool = True, drill_host: str = "") -> str | None:
    """compose.override.yaml parking the services not chosen in a profile nobody enables (Compose
    merges the file by itself, so the update agent and every `compose up` honour it), and, on a
    host without /dev/kvm (a web stack on a Raspberry Pi), the worker without that device: Docker
    won't start a container whose device is missing. A restore drill (design §33) gives the CA
    `drill_host` -> this host: the web address still points at the real stack in DNS, so the CA's
    ACME check of Caddy would go there. None: nothing to override."""
    off = [svc for key, svcs in OPTIONAL_SERVICES.items() if not answers.get(key, True) for svc in svcs]
    if not off and kvm_device and not drill_host:
        return None
    # !override: Compose merges a service's `profiles` with the override's, so the stores' own
    # "stores" profile would still start a parked Conjur.
    body = "".join(f"  {svc}:\n    profiles: !override [\"off\"]\n" for svc in off)
    if not kvm_device:
        body += "  labape-worker:\n    devices: !reset []     # no /dev/kvm here: template builds run on the KVM hosts\n"
    if drill_host:
        body += f"  labape-ca:\n    extra_hosts: [\"{drill_host}:host-gateway\"]     # restore drill: ACME checks reach this copy\n"
    note = f"# Services not installed: {', '.join(off)}\n" if off else ""
    return f"{OVERRIDE_HEADER}{note}services:\n{body}"


def stack_services(c: Ctx):
    cdir = c.repo / "container"
    override = cdir / "compose.override.yaml"
    if c.a.need("InstallConjur") and cpu_arch() == "arm64":
        raise StepError("Conjur has no arm64 image: answer InstallConjur=false on this machine (OpenBao is installed)")
    text = services_override(c.a.answers, kvm_device=Path("/dev/kvm").exists(),
                             drill_host=c.a.need("WebHostname") if c.restore_drill else "")
    if text is None:
        if override.is_file() and override.read_text(encoding="utf-8").startswith(OVERRIDE_HEADER):
            c.r.run(["rm", "-f", str(override)])
    else:
        if override.is_file() and not override.read_text(encoding="utf-8").startswith(OVERRIDE_HEADER):
            raise StepError(f"{override} exists and wasn't written by the installer; merge it by hand")
        c.r.write(override, text, mode=0o644, owner=c.user)
    env = {"LABAPE_CONJUR": "yes" if c.a.need("InstallConjur") else "no"}
    if c.a.need("InstallSyslog"):
        env["LABAPE_SYSLOG_BIND"] = c.a.need("SyslogBind")
    _set_env(c, cdir / ".env", env)
    chosen = [k.removeprefix("Install") for k in OPTIONAL_SERVICES if c.a.answers.get(k, True)]
    return DONE, f"optional services: {', '.join(chosen) or 'none'}" + ("" if text is None else " (others parked)")


def stack_auth(c: Ctx):
    cdir = c.repo / "container"
    # Authentik installed with LABaPe is its sign-in provider; otherwise the chosen mode, by
    # default a throwaway Authentik for the first sign-in (question 130).
    mode = "authentik" if c.a.need("InstallAuthentik") else c.a.need("AuthMode")
    if mode == "none":
        return NA, "no sign-in provider; use `labape breakglass enable` for admin access"
    if mode in ("authentik", "test-authentik"):
        # As root: Podman runs rootful here, like the LABaPe stack.
        c.r.run(["bash", str(c.repo / "tools" / "authentik-test" / "deploy.sh"), "--labape-hostname",
                 c.a.need("WebHostname"), "--public-host", c.a.need("WebHostname"), "--configure-labape"],
                env={"COMPOSE": " ".join(c.compose)})
        return DONE, ("Authentik installed with LABaPe and wired in (tools/authentik-test)" if mode == "authentik"
                      else "throwaway Authentik deployed and wired in for the first sign-in (tools/authentik-test)")
    _set_env(c, cdir / ".env", {"LABAPE_OIDC_ISSUER": c.a.need("OidcIssuer"),
                                "LABAPE_OIDC_CLIENT_ID": c.a.need("OidcClientId")})
    # Once the bootstrap secrets are in the platform store (design §26.3), the stack reads
    # them from LABAPE_SECRETS_DIR; stores/set-secret.sh writes there and pushes to the store.
    moved = _get_env(cdir / ".env", "LABAPE_SECRETS_DIR")
    secret_file = Path(moved or cdir / "secrets") / "oidc_client_secret"
    if not secret_file.is_file() or not secret_file.read_text().strip():
        if c.r.dry_run:
            print(f"[dry-run] would prompt for the OIDC client secret and write {secret_file}")
        else:
            import getpass
            secret = getpass.getpass("OIDC client secret (not saved in the answers file): ").strip()
            if not secret:
                raise StepError("the OIDC client secret is required for AuthMode=existing")
            c.r.run(["bash", str(cdir / "stores" / "set-secret.sh"), "oidc_client_secret"], as_user=True,
                    input=secret)
    return DONE, f"OIDC issuer {c.a.need('OidcIssuer')}"


def repo_version(repo: Path) -> str:
    """`git describe --tags` of the checkout (e.g. v0.2.0-3-gabc1234), or "dev"."""
    try:
        # As root on the service user's checkout: git refuses it as "dubious ownership" otherwise.
        out = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), "describe", "--tags", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=15, check=False)
    except OSError:
        return "dev"
    return out.stdout.strip() or "dev"


def stack_build(c: Ctx):
    version = repo_version(c.repo)
    c.r.run(c.compose + ["build", "--build-arg", f"LABAPE_VERSION={version}"], cwd=c.repo / "container")
    return DONE, f"image built ({version})"


def _wait_health(c: Ctx, seconds: int = 180) -> bool:
    url = f"https://{c.a.need('WebHostname')}/api/health"
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if '"ok":true' in c.r.out(["curl", "-sk", "--max-time", "5", url]):
            return True
        time.sleep(5)
    return False


def stack_up(c: Ctx):
    c.r.run(c.compose + ["up", "-d"], cwd=c.repo / "container")
    if c.r.dry_run:
        return DONE, "dry run"
    if not _wait_health(c):
        raise StepError("the web interface didn't answer /api/health within 3 minutes; "
                        "check `docker compose logs` in container/")
    return DONE, f"https://{c.a.need('WebHostname')}/ is up"


def stack_stores(c: Ctx):
    """The stack's own OpenBao and Conjur (design §26): certificates from the
    platform CA, initialize and unseal, policies and logins, LABaPe's store
    settings, and the user units that unseal OpenBao at boot. Safe to re-run,
    including on an install from before the stores existed."""
    uid = c.r.out(["id", "-u", c.user]) or "1000"
    conjur = bool(c.a.need("InstallConjur"))
    platform = c.a.need("PlatformStore") if conjur else "openbao"
    _set_env(c, c.repo / "container" / ".env", {"LABAPE_PLATFORM_STORE": platform,
                                                "LABAPE_CONJUR": "yes" if conjur else "no"})
    c.r.run(["loginctl", "enable-linger", c.user])     # the unseal unit is a user unit
    c.r.run(["bash", str(c.repo / "container" / "stores" / "stores.sh"), "setup"], as_user=True,
            cwd=c.repo / "container", env={"XDG_RUNTIME_DIR": f"/run/user/{uid}"})
    return DONE, ("OpenBao and Conjur" if conjur else "OpenBao") + " running in the stack, unsealed and connected to LABaPe"


def stack_web_tls(c: Ctx):
    """The web interface's certificate with every name of the stack (design §48.4, question 158): the
    host name, FQDN and LAN address next to the web name, one certificate from the platform CA, and the
    daily renewal timer. Only with the platform CA (Tls labape-ca)."""
    if c.a.need("Tls") != "labape-ca":
        return NA, "the web certificate isn't from the platform CA"
    uid = c.r.out(["id", "-u", c.user]) or "1000"
    c.r.run(["loginctl", "enable-linger", c.user])     # the renewal timer is a user unit
    c.r.run(["bash", str(c.repo / "container" / "caddy" / "web-tls.sh"), "setup"], as_user=True,
            cwd=c.repo / "container", env={"XDG_RUNTIME_DIR": f"/run/user/{uid}"})
    return DONE, "one certificate for every name of the stack, renewed daily when due"


def update_agent(c: Ctx):
    """The host-side update agent (design §23.2): a user-level systemd path
    unit for the service user, kept running by lingering."""
    c.r.run(["loginctl", "enable-linger", c.user])
    # Docker makes a missing bind-mount folder as root on the stack's first start.
    c.r.run(["install", "-d", "-m", "0755", str(c.repo / "container" / "update")])
    c.r.run(["chown", f"{c.user}:{c.user}", str(c.repo / "container" / "update")])
    c.r.run(["bash", str(c.repo / "deploy" / "update-agent" / "install.sh"), "--repo", str(c.repo)], as_user=True,
            env={"XDG_RUNTIME_DIR": f"/run/user/{c.r.out(['id', '-u', c.user]) or '1000'}"})
    return DONE, "update agent watching container/update/request.json"


def stack_restore(c: Ctx):
    """--from-backup: the archive's state onto the stack just installed (deploy/stack-restore.sh):
    the same web address, CA, secrets, OpenBao, Authentik users and LABaPe data."""
    if c.backup is None:
        return NA, "not installing from a backup"
    cmd = ["bash", str(c.repo / "deploy" / "stack-restore.sh"), str(c.backup), "--key-file", str(c.backup_key),
           "--repo", str(c.repo), "--user", c.user, "--yes"]
    if c.restore_drill:
        cmd.append("--maintenance")
    c.r.run(cmd)
    return DONE, f"restored {c.backup.name}" + (" (maintenance mode on: a drill)" if c.restore_drill else "")


def remote_authorize_worker(c: Ctx):
    """kvm profile: let the web interface's worker in over SSH as the service user."""
    key = (c.a.need("WorkerPublicKey") or "").strip()
    if not key:
        return DONE, "no key given (add the worker's key to ~/.ssh/authorized_keys later, or re-run this step)"
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        raise StepError("WorkerPublicKey doesn't look like an SSH public key")
    auth = c.r.home() / ".ssh" / "authorized_keys"
    if auth.is_file() and key in auth.read_text(encoding="utf-8"):
        return DONE, "already authorized"
    c.r.run(["mkdir", "-p", str(auth.parent)], as_user=True)
    c.r.run(["chmod", "700", str(auth.parent)], as_user=True)
    existing = auth.read_text(encoding="utf-8") if auth.is_file() else ""
    c.r.write(auth, existing + ("" if not existing or existing.endswith("\n") else "\n") + key + "\n",
              mode=0o600, owner=c.user)
    return DONE, f"authorized for {c.user}"


def stack_bootstrap(c: Ctx):
    networks, default = _catalog(c)
    by_name = {n["Name"]: n for n in c.a.need("Networks")}
    data = {
        "networks": [{
            "name": name, "cidr": n["cidr"], "gateway": n["gateway"], "dns_servers": n["dns_servers"],
            "addressing": n["addressing"], "static_pools": n["static_pools"], "dhcp_ranges": n["dhcp_ranges"],
            "reserved": n["reserved"], "allowed_groups": by_name[name].get("AllowedGroups", []),
        } for name, n in networks.items()],
        "hosts": [{
            "name": c.a.need("KvmHostName"),
            "libvirt_uri": "qemu:///system" if c.a.need("Profile") == "full" else c.a.need("RemoteKvmUri"),
            "vm_storage_path": c.a.need("VmStoragePath"), "template_storage_path": c.a.need("TemplateStoragePath"),
            "networks": [{"network": name, "bridge": n["bridge"], "is_default": name == default}
                         for name, n in networks.items()],
        }],
        "role_bindings": [{"principal_type": "group", "principal": g, "role": "admin"}
                          for g in c.a.need("AdminGroups")],
        # The file manager's ISO location is where this install keeps them.
        "settings": {"files.roots": {"isos": c.a.need("IsoPath")}},
    }
    c.r.run(c.compose + ["exec", "-T", "labape-api", "labape", "bootstrap", "/dev/stdin"],
            cwd=c.repo / "container", input=json.dumps(data))
    return DONE, f"host {c.a.need('KvmHostName')} and {len(networks)} network(s) registered"


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
