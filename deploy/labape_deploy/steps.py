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
    "network-manager", "perl",
]
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
    info = platform.freedesktop_os_release() if hasattr(platform, "freedesktop_os_release") else {}
    if info.get("ID") != "debian" and "debian" not in info.get("ID_LIKE", ""):
        raise StepError(f"Debian (or a derivative) is required; this is {info.get('PRETTY_NAME', 'unknown')}")
    if not Path("/dev/kvm").exists():
        raise StepError("/dev/kvm is missing: enable virtualization (VT-x/AMD-V) in firmware")
    return DONE, info.get("PRETTY_NAME", "")


def prereq_user(c: Ctx):
    user = c.user
    if not c.r.out(["id", "-u", user]):
        c.r.run(["useradd", "--create-home", "--shell", "/bin/bash", user])
    return DONE, f"service user {user}"


def prereq_packages(c: Ctx):
    missing = [p for p in APT_PACKAGES if not c.r.out(["dpkg-query", "-W", "-f=${Status}", p]).endswith("installed")]
    if not missing:
        return DONE, "all present"
    c.r.run(["apt-get", "update"], env={"DEBIAN_FRONTEND": "noninteractive"})
    c.r.run(["apt-get", "install", "-y", "--no-install-recommends", *missing], env={"DEBIAN_FRONTEND": "noninteractive"})
    return DONE, f"installed {', '.join(missing)}"


def prereq_libvirt(c: Ctx):
    c.r.run(["systemctl", "enable", "--now", "libvirtd"])
    c.r.run(["usermod", "-aG", "libvirt,kvm", c.user])
    return DONE, f"{c.user} in libvirt, kvm"


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
        c.r.run(["nmcli", "con", "up", br])
        done.append(f"{br} created on {nic}")
    return DONE, "; ".join(done)


# --- storage ------------------------------------------------------------------

def storage_dirs(c: Ctx):
    for key in ("VmStoragePath", "TemplateStoragePath", "IsoPath"):
        path = Path(c.a.need(key))
        if not path.is_dir():
            c.r.run(["mkdir", "-p", str(path)])
    return DONE, ", ".join(str(c.a.need(k)) for k in ("VmStoragePath", "TemplateStoragePath", "IsoPath"))


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
                               f"tofu_{v}_linux_amd64.zip", f"tofu_{v}_SHA256SUMS")


def tools_packer(c: Ctx):
    v = c.a.need("PackerVersion")
    status = _install_zip_binary(c, "packer", v, f"https://releases.hashicorp.com/packer/{v}",
                                 f"packer_{v}_linux_amd64.zip", f"packer_{v}_SHA256SUMS")
    for plugin in ("github.com/hashicorp/qemu", "github.com/hashicorp/ansible"):
        c.r.run(["packer", "plugins", "install", plugin], as_user=True, check=False)
    return status


def tools_ansible(c: Ctx):
    v = c.a.need("AnsibleCoreVersion")
    have = c.r.out([str(VENV / "bin" / "ansible"), "--version"])
    if f"core {v}" not in have or c.force:
        if not (VENV / "bin" / "python").exists():
            c.r.run(["python3", "-m", "venv", "--system-site-packages", str(VENV)])
        c.r.run([str(VENV / "bin" / "pip"), "install", "--quiet", f"ansible-core=={v}", "pywinrm", "pyyaml"])
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
    return DONE, f"ansible-core {v} + collections in {VENV}"


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
    pkgs = ["docker.io", "docker-compose", "docker-buildx"] if rt == "docker" else ["podman", "podman-compose"]
    missing = [p for p in pkgs if not c.r.out(["dpkg-query", "-W", "-f=${Status}", p]).endswith("installed")]
    if missing:
        c.r.run(["apt-get", "install", "-y", *missing], env={"DEBIAN_FRONTEND": "noninteractive"})
    if rt == "docker":
        c.r.run(["systemctl", "enable", "--now", "docker"])
        c.r.run(["usermod", "-aG", "docker", c.user])
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


def stack_setup(c: Ctx):
    cdir = c.repo / "container"
    c.r.run(["bash", str(cdir / "setup.sh"), "--hostname", c.a.need("WebHostname"), "--tls", c.a.need("Tls")],
            as_user=True, cwd=cdir)
    _set_env(c, cdir / ".env", {"LABAPE_HOSTNAME": c.a.need("WebHostname"), "LABAPE_TLS": c.a.need("Tls"),
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
    copied = 0
    for src, dst, mode in copies:
        if src.is_file():
            c.r.run(["install", "-D", "-m", oct(mode)[2:], "-o", c.user, "-g", c.user, str(src), str(dst)])
            copied += 1
    return DONE, f"container/.env and secrets in place; {copied} config/secret files copied"


def stack_auth(c: Ctx):
    mode = c.a.need("AuthMode")
    cdir = c.repo / "container"
    if mode == "none":
        return NA, "no sign-in provider; use `labape breakglass enable` for admin access"
    if mode == "test-authentik":
        c.r.run(["bash", str(c.repo / "tools" / "authentik-test" / "deploy.sh"), "--labape-hostname",
                 c.a.need("WebHostname"), "--configure-labape"], as_user=True)
        return DONE, "throwaway Authentik deployed and wired in (tools/authentik-test)"
    _set_env(c, cdir / ".env", {"LABAPE_OIDC_ISSUER": c.a.need("OidcIssuer"),
                                "LABAPE_OIDC_CLIENT_ID": c.a.need("OidcClientId")})
    secret_file = cdir / "secrets" / "oidc_client_secret"
    if not secret_file.is_file() or not secret_file.read_text().strip():
        if c.r.dry_run:
            print(f"[dry-run] would prompt for the OIDC client secret and write {secret_file}")
        else:
            import getpass
            secret = getpass.getpass("OIDC client secret (not saved in the answers file): ").strip()
            if not secret:
                raise StepError("the OIDC client secret is required for AuthMode=existing")
            c.r.write(secret_file, secret + "\n", mode=0o644, owner=c.user)
    return DONE, f"OIDC issuer {c.a.need('OidcIssuer')}"


def stack_build(c: Ctx):
    c.r.run(c.compose + ["build"], cwd=c.repo / "container")
    return DONE, "image built"


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
            "name": c.a.need("KvmHostName"), "libvirt_uri": "qemu:///system",
            "vm_storage_path": c.a.need("VmStoragePath"), "template_storage_path": c.a.need("TemplateStoragePath"),
            "networks": [{"network": name, "bridge": n["bridge"], "is_default": name == default}
                         for name, n in networks.items()],
        }],
        "role_bindings": [{"principal_type": "group", "principal": g, "role": "admin"}
                          for g in c.a.need("AdminGroups")],
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
    checks = {
        "libvirt": ["virsh", "--connect", "qemu:///system", "list", "--all"],
        "tofu": ["tofu", "version"],
        "ansible": ["ansible", "--version"],
    }
    for label, cmd in checks.items():
        if not c.r.out(cmd, as_user=True):
            raise StepError(f"{label} check failed as {c.user}: {' '.join(cmd)} "
                            "(a new group membership needs a fresh login)")
    with tempfile.TemporaryDirectory() as tmp:
        os.chmod(tmp, 0o755)
        c.r.run(["python3", str(c.repo / "scripts" / "lib" / "render_environment_tfvars.py"),
                 str(c.repo / "tofu" / "environment.yml"), tmp], as_user=True, capture=True, mutating=False)
    return DONE, "libvirt, tofu, ansible and environment.yml OK"


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
