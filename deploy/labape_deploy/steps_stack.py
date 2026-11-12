"""Install steps for the web interface: the container stack, its services, TLS, restore, bootstrap (design §28-§29).
Split out of steps.py, which keeps the prerequisites, host networking, storage, toolchain and engine configuration."""
from __future__ import annotations

from .steps_base import *  # noqa: F401,F403 — constants, helpers and the run context
from .steps_base import _catalog, _installed  # noqa: F401


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
# Services whose output container/compose.yaml sends to the stack syslog collector (design §53.2): all but the collector.
LOGGED_SERVICES = ("postgres", "labape-mailpit", "labape-ca", "labape-api", "labape-worker", "labape-guacd", "caddy",
                   "labape-openbao", "labape-conjur-db", "labape-conjur", "labape-conjur-tls")
OVERRIDE_HEADER = "# Written by deploy/install-labape.py (Stack.Services). Don't edit: re-run the installer.\n"


def services_override(answers: dict, kvm_device: bool = True, drill_host: str = "", runtime: str = "docker") -> str | None:
    """compose.override.yaml parking the services not chosen in a profile nobody enables (Compose
    merges the file by itself, so the update agent and every `compose up` honour it), and, on a
    host without /dev/kvm (a web stack on a Raspberry Pi), the worker without that device: Docker
    won't start a container whose device is missing. A restore drill (design §33) gives the CA
    `drill_host` -> this host: the web address still points at the real stack in DNS, so the CA's
    ACME check of Caddy would go there. The containers' logs go to the stack syslog through Docker's
    syslog driver; where that is unavailable (Podman: journald) or there is no collector (json-file)
    the services log locally instead. None: nothing to override."""
    off = [svc for key, svcs in OPTIONAL_SERVICES.items() if not answers.get(key, True) for svc in svcs]
    local = "journald" if runtime == "podman" else ("json-file" if not answers.get("InstallSyslog", True) else "")
    if not off and kvm_device and not drill_host and not local:
        return None
    # One block per service. !override: Compose merges a service's `profiles` with the override's, so the
    # stores' own "stores" profile would still start a parked Conjur; `logging` would merge options too.
    blocks: dict[str, str] = {}
    for svc in off:
        blocks[svc] = blocks.get(svc, "") + "    profiles: !override [\"off\"]\n"
    if local:
        for svc in LOGGED_SERVICES:
            blocks[svc] = blocks.get(svc, "") + f"    logging: !override\n      driver: {local}\n"
    if not kvm_device:
        blocks["labape-worker"] = blocks.get("labape-worker", "") + "    devices: !reset []     # no /dev/kvm here: template builds run on the KVM hosts\n"
    if drill_host:
        blocks["labape-ca"] = blocks.get("labape-ca", "") + f"    extra_hosts: [\"{drill_host}:host-gateway\"]     # restore drill: ACME checks reach this copy\n"
    body = "".join(f"  {svc}:\n{text}" for svc, text in blocks.items())
    note = f"# Services not installed: {', '.join(off)}\n" if off else ""
    return f"{OVERRIDE_HEADER}{note}services:\n{body}"


def stack_services(c: Ctx):
    cdir = c.repo / "container"
    override = cdir / "compose.override.yaml"
    if c.a.need("InstallConjur") and cpu_arch() == "arm64":
        raise StepError("Conjur has no arm64 image: answer InstallConjur=false on this machine (OpenBao is installed)")
    text = services_override(c.a.answers, kvm_device=Path("/dev/kvm").exists(),
                             drill_host=c.a.need("WebHostname") if c.restore_drill else "",
                             runtime=c.a.need("ContainerRuntime"))
    if text is None:
        if override.is_file() and override.read_text(encoding="utf-8").startswith(OVERRIDE_HEADER):
            c.r.run(["rm", "-f", str(override)])
    else:
        if override.is_file() and not override.read_text(encoding="utf-8").startswith(OVERRIDE_HEADER):
            raise StepError(f"{override} exists and wasn't written by the installer; merge it by hand")
        c.r.write(override, text, mode=0o644, owner=c.user)
    env = {"LABAPE_CONJUR": "yes" if c.a.need("InstallConjur") else "no", "LABAPE_MAIL_BIND": c.a.need("MailBind")}
    if c.a.need("InstallSyslog"):
        env["LABAPE_SYSLOG_BIND"] = c.a.need("SyslogBind")
    _set_env(c, cdir / ".env", env)
    chosen = [k.removeprefix("Install") for k in OPTIONAL_SERVICES if c.a.answers.get(k, True)]
    return DONE, f"optional services: {', '.join(chosen) or 'none'}" + ("" if text is None else " (others parked)")


def ask_authentik_admin_password(a) -> None:
    """The password for the Authentik administrator login the installer creates (AuthentikAdminUser), asked once
    before the steps run: from LABAPE_AUTHENTIK_ADMIN_PASSWORD for an unattended run, else at the terminal (twice,
    hidden). Kept in memory only (`a.secret_inputs`), never in the answers file or the log."""
    if not a.answers.get("AuthentikAdminUser") or a.secret_inputs.get("AuthentikAdminPassword"):
        return
    pw = os.environ.get("LABAPE_AUTHENTIK_ADMIN_PASSWORD", "")
    if not pw and a.interactive:
        while True:
            pw = getpass.getpass(f"Password for the Authentik login {a.answers['AuthentikAdminUser']} (12 or more characters): ")
            if len(pw) < 12:
                print("  at least 12 characters")
                continue
            if pw != getpass.getpass("Again: "):
                print("  the two differ")
                continue
            break
    if len(pw) < 12:
        print(f"note: no password for the Authentik login {a.answers['AuthentikAdminUser']} (set LABAPE_AUTHENTIK_ADMIN_PASSWORD "
              "for an unattended run): create it later with tools/authentik-test/create-admin.sh; until then akadmin is the only "
              "administrator.")
        return
    a.secret_inputs["AuthentikAdminPassword"] = pw


def stack_auth(c: Ctx):
    cdir = c.repo / "container"
    # Authentik installed with LABaPe is its sign-in provider; otherwise the chosen mode, by
    # default a throwaway Authentik for the first sign-in (question 130).
    mode = "authentik" if c.a.need("InstallAuthentik") else c.a.need("AuthMode")
    if mode == "none":
        return NA, "no sign-in provider; use `labape breakglass enable` for admin access"
    if mode in ("authentik", "test-authentik"):
        # As root: Podman runs rootful here, like the LABaPe stack.
        admin = c.a.need("AuthentikAdminUser")
        password = c.a.secret_inputs.get("AuthentikAdminPassword", "") if admin else ""
        cmd = ["bash", str(c.repo / "tools" / "authentik-test" / "deploy.sh"), "--labape-hostname",
               c.a.need("WebHostname"), "--public-host", c.a.need("WebHostname"), "--configure-labape"]
        if admin:
            cmd += ["--admin-user", admin, "--admin-email", c.a.need("AuthentikAdminEmail")]
        # The password goes in on standard input (never on a command line or in the answers file).
        c.r.run(cmd, env={"COMPOSE": " ".join(c.compose)}, input=(password + "\n") if password else "")
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
        # As the checkout's owner: `--dirty` refreshes and rewrites .git/index, which as root left it
        # owned by root. safe.directory covers a root run on a checkout git calls "dubious ownership".
        st = repo.stat()
        as_owner = {}
        if hasattr(os, "geteuid") and os.geteuid() == 0 and st.st_uid != 0:
            try:
                home = pwd.getpwuid(st.st_uid).pw_dir
            except KeyError:
                home = "/"
            as_owner = {"user": st.st_uid, "group": st.st_gid, "env": {**os.environ, "HOME": home}}
        out = subprocess.run(["git", "-c", f"safe.directory={repo}", "-C", str(repo), "describe", "--tags", "--always",
                              "--dirty"], capture_output=True, text=True, timeout=15, check=False, **as_owner)
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


def stack_authentik_admin(c: Ctx):
    """akadmin's first password into the secret store, then blank its plain copy in tools/authentik-test/.env
    (design §54.3). Stack.Auth tries the same, but the stack isn't up yet on a fresh install. Never replaces a
    stored password (it may have been rotated); a backup's install brings its own."""
    if c.backup is not None:
        return NA, "installing from a backup: its Authentik and secret store come with it"
    env = c.repo / "tools" / "authentik-test" / ".env"
    if not env.is_file():
        return NA, "no Authentik installed with LABaPe"
    password = _get_env(env, "AUTHENTIK_BOOTSTRAP_PASSWORD")
    if not password:
        return DONE, "akadmin's password is in the secret store (no plain copy left)"
    if c.r.dry_run:
        print(f"[dry-run] would store akadmin's password in the secret store and blank its line in {env}")
        return DONE, "dry run"
    # The password goes in on standard input (never on a command line).
    res = c.r.run(c.compose + ["exec", "-T", "labape-api", "labape", "authentik-admin-import", "--keep-existing"],
                  cwd=c.repo / "container", input=password + "\n", check=False, capture=True)
    if res.returncode == 3:
        return DONE, (f"a different akadmin password is already stored, so the copy in {env} was left; check it "
                      "(Admin -> Secrets -> Authentik administrator -> Check) and remove the line by hand")
    if res.returncode != 0:
        raise StepError("couldn't store akadmin's password: " + (res.stderr or res.stdout or "").strip()[-400:])
    lines = env.read_text(encoding="utf-8").splitlines()
    lines = ["AUTHENTIK_BOOTSTRAP_PASSWORD=" if ln.startswith("AUTHENTIK_BOOTSTRAP_PASSWORD=") else ln for ln in lines]
    c.r.write(env, "\n".join(lines) + "\n", mode=0o600)
    return DONE, "akadmin's password is in the secret store (Admin -> Secrets -> Authentik administrator); .env copy blanked"


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

