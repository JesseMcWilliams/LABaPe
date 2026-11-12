"""Preparing a job's engine directory and the commands it runs (split out of runner.py, which keeps the claiming, running and finishing of jobs)."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session
from ..config import get_settings
from ..db import session_factory
from ..models import CatalogItem, Environment, HostNetwork, IpAllocation, Job, KvmHost, Network
from ..catalog import expand
from ..files import iso_paths, roots
from ..naming import instance_names
from .. import domain_member, linux_desktop, mail_server, openbao_vms, timezones
from . import engine_secrets, hostio
from . import planpreview
from ..security import get_secret
from .vmaccess import env_password_overrides

PROFILE = "labape-ui"              # tofu/environments/<PROFILE>.tfvars, written per job

BACKEND_OVERRIDE = """\
# Written by the LABaPe job runner: OpenTofu state lives in PostgreSQL.
# The connection string comes from PG_CONN_STR in the job's environment.
terraform {
  backend "pg" {}
}
"""


# --- preparing the engine directory -----------------------------------------

def _hcl(value) -> str:
    """JSON values are valid HCL expressions (objects may use ':')."""
    return json.dumps(value)


def network_catalog(db: Session, host: KvmHost) -> tuple[dict, str] | None:
    """The catalog networks this host carries, in environment.yml's shape
    (design §20; scripts/lib/labape_networks.py), or None if the host has
    no attachments yet (then the base environment.yml's own networks apply)."""
    rows = db.execute(select(HostNetwork, Network).join(Network, HostNetwork.network_id == Network.id)
                      .where(HostNetwork.kvm_host_id == host.id).order_by(Network.name)).all()
    if not rows:
        return None
    nets = {}
    for hn, n in rows:
        nets[n.name] = {
            "cidr": n.cidr, "gateway": n.gateway, "dns_servers": list(n.dns_servers or []), "bridge": hn.bridge,
            "addressing": list(n.addressing or []), "static_pools": list(hn.static_pool or n.static_pools or []),
            "dhcp_ranges": list(n.dhcp_ranges or []), "reserved": list(n.reserved or []),
        }
    default = next((n.name for hn, n in rows if hn.is_default), rows[0][1].name)
    return nets, default


CATALOG_KEYS = ("services", "applications", "packages")
# Start order is the power job's business (design §28.3), not OpenTofu's either; nor which host
# a group is on (design §45: each host's part only gets its own groups).
NOT_FOR_TOFU = CATALOG_KEYS + ("start_order", "start_wait", "start_delay_seconds", "kvm_host_id")


def write_catalog_inputs(db: Session, env: Environment, work: Path) -> None:
    """Catalog choices for Ansible (design §21.4): every published
    application merged into package_catalog.yml, each VM's services and
    extra applications as inventory host_vars, and software_store_path
    pointed at the installers location (local installer paths are file
    names there)."""
    inv = work / "ansible" / "inventory"
    hv = inv / "host_vars"
    shutil.rmtree(hv, ignore_errors=True)
    hv.mkdir(parents=True, exist_ok=True)
    for g in (env.spec or {}).get("host_groups", []):
        chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []),
                        "windows" if str(g.get("os", "")).startswith("windows") else "linux")
        if not chosen["services"] and not chosen["applications"]:
            continue
        for vm in instance_names(g):
            (hv / f"{vm}.yml").write_text(yaml.safe_dump({
                "labape_services": chosen["services"],
                "labape_extra_packages": sorted(chosen["applications"]),
            }, sort_keys=False), encoding="utf-8")
    catalog_file = work / "ansible" / "package_catalog.yml"
    base = yaml.safe_load(catalog_file.read_text(encoding="utf-8")) if catalog_file.is_file() else {}
    packages = dict((base or {}).get("packages") or {})
    for item in db.scalars(select(CatalogItem).where(CatalogItem.kind == "application",
                                                     CatalogItem.status == "published")):
        packages[item.name] = resolve_update_installers(db, item.definition)
    catalog_file.write_text("# Written by the LABaPe job runner: the repository catalog plus the web UI's\n"
                            "# published applications (design §21.4).\n"
                            + yaml.safe_dump({"packages": packages}, sort_keys=False), encoding="utf-8")
    gv = inv / "group_vars" / "all"
    gv.mkdir(parents=True, exist_ok=True)
    (gv / "labape.yml").write_text(yaml.safe_dump({"software_store_path": roots(db)["installers"]}),
                                   encoding="utf-8")


def resolve_update_installers(db: Session, definition: dict) -> dict:
    """P1: an installer with `source: update` becomes the newest file Catalog → Updates downloaded for
    its source (a local installer); with none downloaded yet, the installer is left out and the
    package manager entry (Chocolatey, dnf, apt) installs instead."""
    from ..models import StoredFile, UpdateSource
    d = dict(definition or {})
    for plat in ("windows", "linux"):
        inst = d.get(plat)
        if not isinstance(inst, dict) or inst.get("source") != "update":
            continue
        src = db.scalar(select(UpdateSource).where(UpdateSource.name == inst.get("update_source", "")))
        newest = None
        for h in (src.history or []) if src else []:
            f = db.get(StoredFile, h.get("file_id") or 0)
            if f is not None and f.present:
                newest = f
                break
        if newest is None:
            d.pop(plat)
            continue
        d[plat] = {**{k: v for k, v in inst.items() if k != "update_source"}, "source": "local", "path": newest.name}
    return d


def profile_host_groups(db: Session, env: Environment, only: list[dict] | None = None) -> list[dict]:
    """The spec's host groups (or `only` these), with each static group's allocated addresses."""
    allocs = {a.vm_name: a.address for a in db.scalars(select(IpAllocation)
                                                       .where(IpAllocation.environment_id == env.id))}
    groups = []
    for g in (env.spec or {}).get("host_groups", []) if only is None else only:
        g = {k: v for k, v in g.items() if k not in NOT_FOR_TOFU}  # Ansible's and the power job's, not OpenTofu's
        if g.get("addressing", "static") == "static":
            names = instance_names(g)
            if all(n in allocs for n in names):
                g["addresses"] = [allocs[n] for n in names]
        groups.append(g)
    return groups


def _prepare_base(db: Session, host: KvmHost, work: Path) -> None:
    """The engine snapshot plus this instance's inputs: environment.yml
    (host paths, network catalog, file-manager ISOs), manifests, vault and
    the PostgreSQL state backend."""
    s = get_settings()
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(s.engine_dir, work, dirs_exist_ok=True, symlinks=True)

    # environment.yml: the instance-wide base, with this host's storage paths.
    base = s.config_dir / "environment.yml"
    if not base.is_file():
        raise RuntimeError(f"{base} is missing (copy tofu/environment.example.yml there and edit it)")
    env_yml = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    env_yml["vm_storage_path"] = host.vm_storage_path
    env_yml["template_storage_path"] = host.template_storage_path
    catalog = network_catalog(db, host)
    if catalog is not None:
        env_yml.pop("network", None)
        env_yml["networks"], env_yml["default_network"] = catalog
    # ISOs from the file manager win over the base file's paths (design §9).
    env_yml["os_iso_paths"] = {**(env_yml.get("os_iso_paths") or {}), **iso_paths(db)}
    (work / "environment.yml").write_text(yaml.safe_dump(env_yml, sort_keys=False), encoding="utf-8")

    for name, dest in (("software-manifest.yml", work / "ansible" / "software-manifest.yml"),
                       ("directory-manifest.yml", work / "ansible" / "directory-manifest.yml")):
        src = s.config_dir / name
        if src.is_file():
            shutil.copy2(src, dest)
    # The job's own copies of the vault and its password: a rotation must not
    # change them under a running job. From the secret store once the engine
    # secrets are imported (design §26); before that, from the mounted files
    # (§24, R2), where a copy taken mid-swap doesn't open, so take it again.
    if engine_secrets.in_catalog(db):
        engine_secrets.write_vault(db, work)
        (work / "tofu" / "backends" / "libvirt" / "labape_backend_override.tf").write_text(BACKEND_OVERRIDE,
                                                                                           encoding="utf-8")
        return
    vault, pw = s.secrets_dir / "secrets.vault.yml", s.secrets_dir / "vault-pass"
    for _ in range(5):
        if vault.is_file():
            shutil.copy2(vault, work / "secrets.vault.yml")
        if pw.is_file():
            shutil.copyfile(pw, work / ".vault-pass")
            os.chmod(work / ".vault-pass", 0o600)
        if not (vault.is_file() and pw.is_file() and shutil.which("ansible-vault")) or subprocess.run(
                ["ansible-vault", "view", "--vault-password-file", str(work / ".vault-pass"),
                 str(work / "secrets.vault.yml")], capture_output=True, timeout=60, check=False).returncode == 0:
            break
        time.sleep(2)

    (work / "tofu" / "backends" / "libvirt" / "labape_backend_override.tf").write_text(BACKEND_OVERRIDE,
                                                                                       encoding="utf-8")


def prepare(db: Session, env: Environment, host: KvmHost, only: list[dict] | None = None) -> Path:
    """The engine workspace for `host`'s part of the environment: the home host's is `engine`, another
    host's `engine-h<id>` (design §45); `only` limits OpenTofu's profile to that part's groups."""
    s = get_settings()
    work = s.data_dir / "environments" / env.name / ("engine" if host.id == env.kvm_host_id else f"engine-h{host.id}")
    _prepare_base(db, host, work)

    # Profile: the environment's host groups plus per-host settings. A
    # -var-file beats TF_VAR_*, so libvirt_uri here overrides the vault's.
    # Static addresses are the environment's allocations (design §20.4);
    # bridge_device only matters for a host without network attachments.
    spec = env.spec or {}
    profile = work / "tofu" / "environments" / f"{PROFILE}.tfvars"
    lines = [
        "# Written by the LABaPe job runner from the environment's spec.",
        f"libvirt_uri = {_hcl('qemu:///system' if hostio.is_remote(host.libvirt_uri) else host.libvirt_uri)}",
        f"bridge_device = {_hcl(host.bridge)}",
    ]
    if spec.get("static_ip_offset_start") is not None:  # environments created before allocations
        lines.append(f"static_ip_offset_start = {_hcl(spec['static_ip_offset_start'])}")
    lines.append(f"host_groups = {_hcl(profile_host_groups(db, env, only))}")
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_catalog_inputs(db, env, work)

    # The Linux admin account this environment was created with (Settings > VM accounts).
    env_file = work / "environment.yml"
    env_yml = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    env_yml["linux_admin_user"] = spec.get("linux_admin_user") or "labape"
    # The environment's time zone (design §52): Ansible sets it; environments from before it have none.
    if spec.get("timezone"):
        env_yml["timezone"] = spec["timezone"]
        env_yml["windows_timezone"] = timezones.windows_name(spec["timezone"])
    env_file.write_text(yaml.safe_dump(env_yml, sort_keys=False), encoding="utf-8")
    # The environment's own accounts (design §42) join the directory manifest, their passwords the vault.
    from .. import env_accounts
    env_accounts.merge_manifest(work / "ansible" / "directory-manifest.yml", env, str(env_yml.get("domain_name") or ""))
    # Passwords rotated for this environment (design §24, R4) replace the vault's.
    # The recovery pairs' second admins (A1) get passwords before the deploy that creates them.
    from .vmaccess import ensure_recovery_passwords
    ensure_recovery_passwords(db, env)
    rotated = {**env_password_overrides(db, env.id), **env_accounts.vault_overrides(db, env)}
    if rotated:
        overlay_vault(work, rotated)

    # Each member VM's own local passwords (A2), in its host variables.
    from .. import vm_passwords
    vm_passwords.write_host_vars(db, env, work)
    linux_desktop.write_host_vars(db, env, work)          # E3: xrdp / VNC per VM
    domain_member.write_host_vars(env, work)              # D184: standalone groups stay out of the domain
    mail_server.write_host_vars(db, env, work)            # M1: Mailpit's UI password and relay
    openbao_vms.write_host_vars(db, env, work)            # V2: the VMs' labape-secret helper
    return work


def job_env(host: KvmHost | None = None) -> dict[str, str]:
    s = get_settings()
    home = s.data_dir / "home"
    env = {k: v for k, v in os.environ.items() if not k.startswith("LABAPE_")}
    env.update({
        "HOME": str(home),
        "LABAPE_VAULT_PASS_FILE": str(s.secrets_dir / "vault-pass"),
        "PG_CONN_STR": s.tofu_pg_conn,
        "LABAPE_SSH_PRIVATE_KEY_PATH": ssh_key_path(),
        "ANSIBLE_FORCE_COLOR": "0",
        "TF_IN_AUTOMATION": "1",
        "PYTHONUNBUFFERED": "1",
    })
    if host is not None:
        env["LIBVIRT_URI"] = host.libvirt_uri   # DHCP discovery (discover_dhcp_ips.py)
    return env


def ssh_key_path() -> str:
    """The job HOME's key pair: the first private key with a matching .pub.

    The vault's ansible_ssh_private_key_path names a path on the CLI host,
    so the runner passes this one to the scripts instead.
    """
    ssh = get_settings().data_dir / "home" / ".ssh"
    for f in sorted(ssh.glob("*")) if ssh.is_dir() else []:
        if f.name == hostio.KEY_NAME:      # the worker's key for remote KVM hosts, not for VMs
            continue
        if f.suffix != ".pub" and f.with_name(f.name + ".pub").is_file():
            return str(f)
    return ""


def overlay_vault(work: Path, overrides: dict) -> None:
    """Rewrite the job's vault copy with this environment's own passwords."""
    vault, pw = work / "secrets.vault.yml", work / ".vault-pass"
    plain = subprocess.run(["ansible-vault", "view", "--vault-password-file", str(pw), str(vault)],
                           capture_output=True, text=True, check=True, timeout=60).stdout
    data = yaml.safe_load(plain) or {}
    data.update({k: v for k, v in overrides.items() if v})
    tmp = work / ".vault-plain.yml"
    tmp.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    os.chmod(tmp, 0o600)
    try:
        subprocess.run(["ansible-vault", "encrypt", "--vault-password-file", str(pw), "--output", str(vault), str(tmp)],
                       capture_output=True, check=True, timeout=60)
    finally:
        tmp.unlink(missing_ok=True)


def sync_home() -> None:
    """Copy SSH keys from the secrets mount into the job HOME (OpenSSH wants 0600 files it owns).
    Runs before every job, so a rotated key (design §24, R2) applies without a restart;
    previous keys (ssh/history/) come along for VMs that missed a rotation. Once the
    engine secrets are in the secret store (design §26), they come from there."""
    s = get_settings()
    ssh_src = s.secrets_dir / "ssh"
    ssh_dst = s.data_dir / "home" / ".ssh"
    with session_factory()() as db:
        if engine_secrets.in_catalog(db):
            engine_secrets.write_ssh(db, ssh_dst)
            return
    ssh_dst.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dst, 0o700)
    for sub in ("", "history"):
        src, dst_dir = ssh_src / sub if sub else ssh_src, ssh_dst / sub if sub else ssh_dst
        if not src.is_dir():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(dst_dir, 0o700)
        for f in src.iterdir():
            if f.is_file():
                dst = dst_dir / f.name
                shutil.copyfile(f, dst)
                os.chmod(dst, 0o644 if f.suffix in (".pub", ".json") else 0o600)


def history_keys() -> list[str]:
    """Previous VM keys, newest first, offered to SSH after the current one."""
    d = get_settings().data_dir / "home" / ".ssh" / "history"
    keys = [f for f in d.iterdir() if f.is_file() and f.suffix not in (".pub", ".json")] if d.is_dir() else []
    return [str(k) for k in sorted(keys, key=lambda f: f.name, reverse=True)]


# --- running ---------------------------------------------------------------

_KEY_LINE = re.compile(r"^(\s*ansible_ssh_private_key_file:).*$", re.MULTILINE)


def current_key_in_inventory(inv: str) -> str:
    """A stored inventory names the SSH key path of the job that made it. On a remote host that is
    the job's own folder (`~/.labape/jobs/<id>/.labape-remote/ssh-key`), removed when the job ends,
    so a later job there reads the path the running job was given instead."""
    return _KEY_LINE.sub(lambda m: m.group(1) + " \"{{ lookup('env', 'LABAPE_SSH_PRIVATE_KEY_PATH') }}\"", inv)


def prepare_template_job(db: Session, job: Job, host: KvmHost) -> Path:
    """Template jobs run in their own engine copy (removed afterwards).
    Promote also needs the environment's inventory, which lives only in the
    secrets store once a deploy finishes."""
    work = get_settings().jobs_dir / str(job.id) / "engine"
    _prepare_base(db, host, work)
    if job.type == "template.promote":
        inv = get_secret(db, f"environment/{job.environment_id}/inventory") or ""
        if not inv:
            raise RuntimeError("the environment has no stored inventory; redeploy it first")
        if hostio.is_remote(host.libvirt_uri):
            inv = current_key_in_inventory(inv)
        target = work / "ansible" / "inventory" / "generated"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(inv, encoding="utf-8")
        target.chmod(0o600)
    return work


def command_for(job: Job, env: Environment | None, work: Path) -> list[str]:
    envfile = ["--env-file", str(work / "environment.yml")]
    p = job.params or {}
    if job.type == "template.build":
        return ["bash", str(work / "scripts" / "build-template.sh"), p["os_key"], p["name"], *envfile,
                *(["--core"] if p.get("core") else []),
                *(["--datacenter"] if p.get("datacenter") else []), *(["--disk-gb", str(p["disk_gb"])] if p.get("disk_gb") else [])]
    if job.type == "template.refresh":
        return ["bash", str(work / "scripts" / "refresh-template.sh"), "libvirt", p["template"], p["new_name"],
                "--os", p["os_key"], *envfile, "--addressing", p.get("addressing", "dhcp"),
                *(["--network", p["network"]] if p.get("network") else [])]
    if job.type == "template.promote":
        return ["bash", str(work / "scripts" / "promote-to-template.sh"), "libvirt", p["environment"], p["vm"],
                p["name"], *envfile, *(["--os", p["os_key"]] if p.get("os_key") else [])]
    assert env is not None
    common = ["libvirt", env.name, PROFILE, "--env-file", str(work / "environment.yml")]
    if job.type == "environment.deploy":
        return ["bash", str(work / "scripts" / "deploy.sh"), *common]
    if job.type == planpreview.JOB_TYPE:              # OpenTofu plans; nothing is applied (design §56.1)
        return ["bash", str(work / "scripts" / "deploy.sh"), *common, "--plan-only", str(work / planpreview.PLAN_FILE)]
    if job.type == "environment.destroy":
        return ["bash", str(work / "scripts" / "destroy.sh"), *common, "--yes", "--delete-workspace"]
    if job.type == "environment.refresh_addresses":
        return ["bash", str(work / "scripts" / "refresh-addresses.sh"), "libvirt", env.name,
                "--env-file", str(work / "environment.yml")]
    raise RuntimeError(f"unknown job type {job.type}")
