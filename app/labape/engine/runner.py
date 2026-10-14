"""Job worker (Claude_Docs/Planning_Web-Interface-Design.md §8).

A worker process runs up to LABAPE_WORKER_SLOTS jobs at once. Each slot
claims the oldest queued job whose KVM host is under its concurrency
limit, prepares the environment's engine directory, and runs the
matching script in its own process group with output going to the job's
log file. The API never runs scripts itself.

Engine directory: data_dir/environments/<name>/engine, refreshed from the
image's engine snapshot (LABAPE_ENGINE_DIR) before every job. It's per
environment, not per job, so the rendered answer files and .terraform
survive between deploy and destroy; only one job per environment runs at
a time (the API refuses a second), so environments never share one.
OpenTofu state lives in PostgreSQL (backend "pg"), not in that directory.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

import yaml
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_factory
from ..models import CatalogItem, Environment, HostNetwork, IpAllocation, Job, KvmHost, Network, utcnow
from ..catalog import expand
from ..files import iso_paths, roots
from ..naming import instance_names
from .files_sync import hash_in_background, scan as scan_files
from .hoststats import collect_all as collect_host_stats
from . import (downloads, engine_secrets, filedist, hostadmin, hostio, maintenance, power, rotation, snapshots,
               stackbackup, stackmon, template_versions)
from .. import maintenance_mode, syslogstack, version
from .templates import sync_all as sync_templates
from ..netpolicy import release
from ..security import audit, get_secret, put_secret
from .vmaccess import clear_env_passwords, env_password_overrides

log = logging.getLogger("labape.worker")

PROFILE = "labape-ui"              # tofu/environments/<PROFILE>.tfvars, written per job
HEARTBEAT_SECONDS = 10
STALE_AFTER = dt.timedelta(minutes=2)
CLAIM_LOCK = 0x1AB4E               # pg advisory lock serializing claims (per-host limits)
TERM_GRACE_SECONDS = 60            # tofu needs time to stop cleanly and release its state lock

BACKEND_OVERRIDE = """\
# Written by the LABaPe job runner: OpenTofu state lives in PostgreSQL.
# The connection string comes from PG_CONN_STR in the job's environment.
terraform {
  backend "pg" {}
}
"""


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# --- claiming ----------------------------------------------------------------

def claim(db: Session, worker: str) -> Job | None:
    """Claim the oldest runnable queued job, or return None."""
    pg = db.bind.dialect.name == "postgresql"
    if pg:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK})
    running, busy_envs = {}, set()
    for host_id, env_id in db.execute(select(Job.kvm_host_id, Job.environment_id).where(Job.state == "running")):
        running[host_id] = running.get(host_id, 0) + 1
        busy_envs.add(env_id)
    limits = {h.id: (h.concurrency_limit, h.enabled) for h in db.scalars(select(KvmHost))}
    q = select(Job).where(Job.state == "queued").order_by(Job.id)
    if pg:
        q = q.with_for_update(skip_locked=True)
    for job in db.scalars(q):
        # One job per environment at a time (e.g. a password rotation and a deploy).
        if job.environment_id is not None and job.environment_id in busy_envs:
            continue
        if job.kvm_host_id is not None:
            limit, enabled = limits.get(job.kvm_host_id, (0, False))
            # A host is prepared (host.install) before it's enabled for deploys (design §28.4).
            if (not enabled and job.type != "host.install") or running.get(job.kvm_host_id, 0) >= max(limit, 1):
                continue
        job.state = "running"
        job.worker = worker
        job.started_at = job.heartbeat_at = utcnow()
        job.log_path = str(get_settings().logs_dir / f"job-{job.id}.log")
        db.commit()
        return job
    db.commit()
    return None


def recover_stale(db: Session) -> None:
    """Fail jobs whose worker stopped heartbeating (crashed or restarted)."""
    cutoff = utcnow() - STALE_AFTER
    for job in db.scalars(select(Job).where(Job.state == "running")):
        hb = job.heartbeat_at
        if hb is not None and hb.tzinfo is None:
            hb = hb.replace(tzinfo=dt.timezone.utc)
        if hb is None or hb < cutoff:
            log.warning("job %s: worker %s stopped heartbeating; marking failed", job.id, job.worker)
            job.state = "failed"
            job.finished_at = utcnow()
            _append(job, "\nlabape: the worker running this job stopped; marked failed.\n")
            if job.environment_id:
                env = db.get(Environment, job.environment_id)
                if env is not None:
                    env.status = "failed"
    db.commit()


def beat(job_id: int) -> bool:
    """Keep a long job the worker runs itself (power, host install) from looking stale, and
    say whether it's been asked to cancel."""
    try:
        with session_factory()() as db:
            j = db.get(Job, job_id)
            if j is None:
                return False
            j.heartbeat_at = utcnow()
            db.commit()
            return bool(j.cancel_requested)
    except Exception as exc:  # noqa: BLE001 — best effort: a busy database mustn't fail the job
        log.debug("heartbeat for job %s failed: %s", job_id, exc)
        return False


def _append(job: Job, msg: str) -> None:
    if job.log_path:
        Path(job.log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(job.log_path, "a", encoding="utf-8") as fh:
            fh.write(msg)


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
# Start order is the power job's business (design §28.3), not OpenTofu's either.
NOT_FOR_TOFU = CATALOG_KEYS + ("start_order", "start_wait", "start_delay_seconds")


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
        chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []))
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
        packages[item.name] = item.definition
    catalog_file.write_text("# Written by the LABaPe job runner: the repository catalog plus the web UI's\n"
                            "# published applications (design §21.4).\n"
                            + yaml.safe_dump({"packages": packages}, sort_keys=False), encoding="utf-8")
    gv = inv / "group_vars" / "all"
    gv.mkdir(parents=True, exist_ok=True)
    (gv / "labape.yml").write_text(yaml.safe_dump({"software_store_path": roots(db)["installers"]}),
                                   encoding="utf-8")


def profile_host_groups(db: Session, env: Environment) -> list[dict]:
    """The spec's host groups, with each static group's allocated addresses."""
    allocs = {a.vm_name: a.address for a in db.scalars(select(IpAllocation)
                                                       .where(IpAllocation.environment_id == env.id))}
    groups = []
    for g in (env.spec or {}).get("host_groups", []):
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


def prepare(db: Session, env: Environment, host: KvmHost) -> Path:
    s = get_settings()
    work = s.data_dir / "environments" / env.name / "engine"
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
    lines.append(f"host_groups = {_hcl(profile_host_groups(db, env))}")
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_catalog_inputs(db, env, work)

    # The Linux admin account this environment was created with (Settings > VM accounts).
    env_file = work / "environment.yml"
    env_yml = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    env_yml["linux_admin_user"] = spec.get("linux_admin_user") or "labape"
    env_file.write_text(yaml.safe_dump(env_yml, sort_keys=False), encoding="utf-8")
    # The environment's own accounts (design §42) join the directory manifest, their passwords the vault.
    from .. import env_accounts
    env_accounts.merge_manifest(work / "ansible" / "directory-manifest.yml", env, str(env_yml.get("domain_name") or ""))
    # Passwords rotated for this environment (design §24, R4) replace the vault's.
    rotated = {**env_password_overrides(db, env.id), **env_accounts.vault_overrides(db, env)}
    if rotated:
        overlay_vault(work, rotated)

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
    if job.type == "environment.destroy":
        return ["bash", str(work / "scripts" / "destroy.sh"), *common, "--yes", "--delete-workspace"]
    if job.type == "environment.refresh_addresses":
        return ["bash", str(work / "scripts" / "refresh-addresses.sh"), "libvirt", env.name,
                "--env-file", str(work / "environment.yml")]
    raise RuntimeError(f"unknown job type {job.type}")


def run(job_id: int, worker: str) -> None:
    Session_ = session_factory()
    with Session_() as db:
        job = db.get(Job, job_id)
        env = db.get(Environment, job.environment_id) if job.environment_id else None
        host = db.get(KvmHost, job.kvm_host_id) if job.kvm_host_id else None
        log_path = Path(job.log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Jobs the worker runs itself, with no KVM host or engine scripts.
        system = {"system.backup": maintenance.run_backup, "files.download": downloads.run_download,
                  "secret.rotate": rotation.run_job, "template.delete": template_versions.run_delete,
                  "environment.power": power.run_job, "host.install": hostadmin.run_install,
                  "backup.store_linked": stackbackup.store_linked, "vm.snapshot": snapshots.run_job,
                  "files.sync_templates": filedist.run_sync_templates}
        if job.type in system:
            try:
                rc = system[job.type](db, job)
            except Exception as exc:  # noqa: BLE001
                _append(job, f"labape: {job.type} failed: {exc}\n")
                rc = None
            _finish(db, job, None, rc)
            return
        try:
            if host is None:
                raise RuntimeError("job has no KVM host")
            if job.type.startswith("template."):
                work = prepare_template_job(db, job, host)
            elif env is None:
                raise RuntimeError("job has no environment")
            else:
                if job.type == "environment.deploy":    # a safety snapshot first, if the setting asks (§39)
                    snapshots.before_deploy(db, job, env, host, lambda m: _append(job, m))
                work = prepare(db, env, host)
            cmd = command_for(job, env, work)
            remote = hostio.RemoteJob(hostio.Host(host.libvirt_uri), work, job.id) \
                if hostio.is_remote(host.libvirt_uri) else None
        except Exception as exc:  # noqa: BLE001 — any setup failure fails the job, visibly
            _append(job, f"labape: could not start: {exc}\n")
            _finish(db, job, env, None)
            return
        iso_keys = _iso_keys(job, env)

    with open(log_path, "ab", buffering=0) as logf:
        what = env.name if env is not None and not job.type.startswith("template.") else \
            (job.params or {}).get("name") or (job.params or {}).get("new_name", "")
        logf.write(f"labape: job {job_id} ({job.type}) for {what} on {host.name}, worker {worker}\n".encode())
        sync_home()
        run_env = job_env(host)
        run_env["LABAPE_VAULT_PASS_FILE"] = str(work / ".vault-pass")
        if history_keys() and remote is None:
            run_env["ANSIBLE_SSH_COMMON_ARGS"] = " ".join(f"-i {k}" for k in history_keys())
        run_env.update(_cert_env(job_id, job.type, remote is not None))
        cert_token = run_env.get("LABAPE_CERT_TOKEN", "")
        if remote is None:
            # The stack's own host, when it isn't the primary file host (design §29.5): copies too.
            with Session_() as fdb:
                here = fdb.get(KvmHost, host.id)
                p = filedist.primary(fdb)
                if p is not None and p.id != here.id:
                    try:
                        _distribute(fdb, work, here, job_id, iso_keys, _TextLog(logf))
                    except Exception as exc:  # noqa: BLE001
                        logf.write(f"labape: could not get the files from the primary: {exc}\n".encode())
                        job = fdb.get(Job, job_id)
                        _finish(fdb, job, fdb.get(Environment, job.environment_id) if job.environment_id else None, None)
                        return
        if remote is not None:
            try:
                logf.write(f"labape: running on {remote.host.target} over SSH in {remote.root}\n".encode())
                with Session_() as fdb:            # files from the primary (design §29.4)
                    _distribute(fdb, work, fdb.get(KvmHost, host.id), job_id, iso_keys, _TextLog(logf))
                secrets_dir = get_settings().secrets_dir
                files = {"vault-pass": work / ".vault-pass"}
                if ssh_key_path():   # the scripts check the key has its .pub beside it
                    files["ssh-key"] = Path(ssh_key_path())
                    files["ssh-key.pub"] = Path(ssh_key_path() + ".pub")
                remote.push(run_env, {k: v for k, v in files.items() if v.is_file()})
                cmd, run_env = remote.command(cmd), hostio.tool_env()
            except Exception as exc:  # noqa: BLE001
                logf.write(f"labape: could not start on {host.name}: {exc}\n".encode())
                remote.cleanup()
                with Session_() as db:
                    job = db.get(Job, job_id)
                    _finish(db, job, db.get(Environment, job.environment_id) if job.environment_id else None, None)
                return
        proc = subprocess.Popen(cmd, cwd=work, env=run_env, stdin=subprocess.DEVNULL, stdout=logf,
                                stderr=subprocess.STDOUT, start_new_session=True)
        cancelled_at = None
        last_beat = 0.0
        while True:
            try:
                rc = proc.wait(timeout=2)
                break
            except subprocess.TimeoutExpired:
                pass
            now = time.monotonic()
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                with Session_() as db:
                    row = db.get(Job, job_id)
                    row.heartbeat_at = utcnow()
                    cancel = row.cancel_requested
                    db.commit()
                if cancel and cancelled_at is None:
                    logf.write(b"\nlabape: cancel requested; stopping (SIGTERM)...\n")
                    cancelled_at = now
                    _signal(proc, signal.SIGTERM)
            if cancelled_at is not None and now - cancelled_at > TERM_GRACE_SECONDS:
                logf.write(b"\nlabape: still running; killing (SIGKILL).\n")
                _signal(proc, signal.SIGKILL)
                cancelled_at = float("inf")  # don't repeat

    if remote is not None:
        try:
            remote.pull("ansible/inventory")      # hosts.generated, credentials, inventory
        except Exception as exc:  # noqa: BLE001
            _append_path(log_path, f"labape: copying the results back failed: {exc}\n")
            rc = rc or 1
        finally:
            remote.cleanup()
    with Session_() as db:
        job = db.get(Job, job_id)
        env = db.get(Environment, job.environment_id) if job.environment_id else None
        if rc == 0 and job.type in ("template.build", "template.refresh"):
            # The primary holds every file (design §29.5): a template built elsewhere goes there too.
            name = (job.params or {}).get("new_name") or (job.params or {}).get("name") or ""
            try:
                if name:
                    filedist.to_primary(db, db.get(KvmHost, job.kvm_host_id), name, _PathLog(log_path))
            except Exception as exc:  # noqa: BLE001 — the template is built; only the copy failed
                _append_path(log_path, f"labape: warning: copying {name} to the primary file host failed: {exc}\n")
        if cert_token:
            from ..api.ca import token_name
            put_secret(db, token_name(job_id), "")      # the token dies with the job
        _finish(db, job, env, rc, work=work)


class _PathLog:
    """A log.write() that appends to a job's log file."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, msg: str) -> None:
        _append_path(self.path, msg)


def _cert_env(job_id: int, job_type: str, remote: bool) -> dict[str, str]:
    """A deploy's one-time token for having VMs' certificates signed (design §22.5).
    Without the platform CA the variables stay empty and labape_cert does nothing."""
    from .. import ca
    if job_type != "environment.deploy" or not ca.configured():
        return {}
    import secrets as _secrets
    from ..api.ca import token_name
    token = _secrets.token_urlsafe(32)
    with session_factory()() as db:
        put_secret(db, token_name(job_id), token)
    s = get_settings()
    return {"LABAPE_CERT_TOKEN": token, "LABAPE_JOB_ID": str(job_id), "LABAPE_CA_RENEW_URL": s.ca_renew_url,
            # A remote host's Ansible reaches the API through the public URL; local jobs use the internal one.
            "LABAPE_CERT_API": s.public_url.rstrip("/") if remote else s.internal_api_url}


class _TextLog:
    def __init__(self, fh):
        self.fh = fh

    def write(self, text: str) -> None:
        self.fh.write(text.encode())


def _append_path(path: Path, msg: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(msg)


def _iso_keys(job: Job, env: Environment | None) -> set[str]:
    """OS keys whose ISO a job installs from (template builds, ISO host groups)."""
    if job.type == "template.build":
        return {(job.params or {}).get("os_key", "")}
    if job.type != "environment.deploy" or env is None:
        return set()
    return {g.get("os", "") for g in (env.spec or {}).get("host_groups", [])
            if (g.get("image_source") or "iso_direct") == "iso_direct"}


def _distribute(db: Session, work: Path, host: KvmHost, job_id: int, iso_keys: set[str], log) -> None:
    """Before a job runs on a remote host: the files it needs from the primary file host
    (design §29.4), and the workspace pointed at the host's copies. ISOs go to
    <vm storage>/.labape-isos, installers to <vm storage>/.labape-files/installers (Ansible
    reads them there: it runs on that host), templates into the host's library."""
    job = db.get(Job, job_id)
    env = db.get(Environment, job.environment_id) if job.environment_id else None
    path = work / "environment.yml"
    env_yml = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    iso_dir = f"{host.vm_storage_path.rstrip('/')}/.labape-isos"
    isos = env_yml.get("os_iso_paths") or {}
    for key, src in list(isos.items()):
        if key in iso_keys:
            size, sha = filedist.known(db, src)
            isos[key] = filedist.ensure(db, host, src, f"{iso_dir}/{Path(src).name}", log, size, sha)
        elif not host.is_primary and not host.files_from_share:
            isos[key] = f"{iso_dir}/{Path(src).name}"
    env_yml["os_iso_paths"] = isos
    path.write_text(yaml.safe_dump(env_yml, sort_keys=False), encoding="utf-8")
    if job.type != "environment.deploy" or env is None:
        return
    for name in sorted({g.get("template") for g in (env.spec or {}).get("host_groups", [])
                        if g.get("image_source") == "packer_template" and g.get("template")}):
        filedist.ensure_template(db, host, name, log)
    # Installers of the applications chosen here (`source: local` entries of the catalog).
    catalog = yaml.safe_load((work / "ansible" / "package_catalog.yml").read_text(encoding="utf-8")) \
        if (work / "ansible" / "package_catalog.yml").is_file() else {}
    packages = (catalog or {}).get("packages") or {}
    wanted = set()
    for g in (env.spec or {}).get("host_groups", []):
        chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []))
        for app in chosen["applications"]:
            for spec in (packages.get(app) or {}).values():
                if isinstance(spec, dict) and spec.get("source") == "local" and spec.get("path"):
                    wanted.add(spec["path"])
    store = roots(db)["installers"].rstrip("/")
    if host.is_primary or host.files_from_share:
        return
    cache = filedist.cache_dir(host, "installers")
    for rel in sorted(wanted):
        size, sha = filedist.known(db, f"{store}/{rel}")
        filedist.ensure(db, host, f"{store}/{rel}", f"{cache}/{rel}", log, size, sha)
    gv = work / "ansible" / "inventory" / "group_vars" / "all" / "labape.yml"
    if gv.parent.is_dir():
        gv.write_text(yaml.safe_dump({"software_store_path": cache}), encoding="utf-8")


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _finish(db: Session, job: Job, env: Environment | None, rc: int | None, work: Path | None = None) -> None:
    job.exit_code = rc
    job.finished_at = utcnow()
    if job.cancel_requested and rc != 0:
        job.state = "cancelled"
    else:
        job.state = "succeeded" if rc == 0 else "failed"
    if job.type.startswith("template."):
        env = None  # a template job never changes an environment's status
        if work is not None:
            shutil.rmtree(work.parent, ignore_errors=True)
    if env is not None:
        if job.type == "environment.refresh_addresses":
            # Read-only for the VMs: the environment's status stays as it was.
            if job.state == "succeeded":
                _collect_outputs(db, job, env, work)
        elif job.state != "succeeded":
            env.status = "failed"
        elif job.type == "environment.deploy":
            env.status = "deployed"
            _collect_outputs(db, job, env, work)
        elif job.type == "environment.destroy":
            env.status = "destroyed"
            env.inventory = ""
            release(db, env.id)
            put_secret(db, f"environment/{env.id}/credentials", "")
            put_secret(db, f"environment/{env.id}/inventory", "")
            # Rotated passwords (design §24, R4) belonged to these VMs; a new
            # deploy builds its DC from the vault's matching pair again.
            clear_env_passwords(db, env.id)
            if work is not None:
                shutil.rmtree(work.parent, ignore_errors=True)
    db.commit()
    audit(db, job.requested_by, f"job.{job.state}", object_type="job", object_id=job.id,
          detail={"type": job.type, "exit_code": rc, "environment_id": job.environment_id})
    try:
        maintenance.notify_job(db, job)
    except Exception:  # noqa: BLE001 — email is best effort
        log.exception("job %s: notification failed", job.id)


def _collect_outputs(db: Session, job: Job, env: Environment, work: Path | None) -> None:
    """hosts.generated is shown to anyone who can see the environment; the
    full inventory and the tester credentials go to the secrets store."""
    if work is None:
        return
    inv = work / "ansible" / "inventory"
    hosts = inv / "hosts.generated"
    env.inventory = hosts.read_text(encoding="utf-8") if hosts.is_file() else ""
    for name, secret in (("credentials.generated", "credentials"), ("generated", "inventory")):
        f = inv / name
        if f.is_file():
            text = f.read_text(encoding="utf-8")
            if secret == "credentials":            # with the environment's own accounts (design §42)
                from .. import env_accounts
                envy = yaml.safe_load((work / "environment.yml").read_text(encoding="utf-8")) \
                    if (work / "environment.yml").is_file() else {}
                text = env_accounts.apply_sheet(text, env_accounts.sheet_section(
                    db, env, str((envy or {}).get("netbios_name") or "")))
            put_secret(db, f"environment/{env.id}/{secret}", text)
            f.unlink()  # don't leave plaintext credentials on disk


# --- worker loop -----------------------------------------------------------

def serve(slots: int | None = None) -> None:
    s = get_settings()
    slots = slots or s.worker_slots
    worker = _worker_id()
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    sync_home()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    log.info("worker %s: %d slots", worker, slots)

    active: dict[int, threading.Thread] = {}
    last_recover = 0.0
    last_purge = 0.0
    last_update_check = 0.0
    while not stop.is_set():
        for jid in [j for j, t in active.items() if not t.is_alive()]:
            del active[jid]
        if time.monotonic() - last_recover > 60:
            last_recover = time.monotonic()
            with session_factory()() as db:
                recover_stale(db)
            with session_factory()() as db:
                sync_templates(db)
            with session_factory()() as db:
                collect_host_stats(db)
            with session_factory()() as db:
                hostadmin.collect_due(db)          # host facts and metrics over SSH (design §28.4)
            with session_factory()() as db:
                syslogstack.tick(db)               # the stack collector's schema, certificates, settings (§29.1)
            with session_factory()() as db:
                filedist.ensure_primary(db)        # always one primary file host (§29.4)
            stackmon.tick(session_factory())       # stack hosts, peer stacks, alerts (§30)
            _send_backups_in_background()          # the newest full backup to linked stacks (§36)
            with session_factory()() as db:
                scan_files(db)
            hash_in_background()
            with session_factory()() as db:
                _heartbeat(db, worker)
                if maintenance_mode.start_if_due(db):
                    log.info("scheduled maintenance started")
                paused = maintenance_mode.get(db)["active"]
            # Maintenance mode (design §23.3): scheduled work waits.
            if not paused:
                with session_factory()() as db:
                    if maintenance.schedule_backup(db):
                        log.info("queued the daily backup")
                with session_factory()() as db:
                    from .. import leases
                    for what in leases.tick(db):           # environment leases (§40)
                        log.info("lease: %s", what)
                with session_factory()() as db:
                    jid = filedist.schedule_template_sync(db)   # templates ahead of need (§43)
                    if jid:
                        log.info("queued template copies to every KVM host (job %s)", jid)
                with session_factory()() as db:
                    if stackbackup.schedule(db):
                        log.info("asked the update agent for the daily full-stack backup")
                with session_factory()() as db:
                    rotation.ensure_policies(db)
                    for jid in rotation.schedule(db):
                        log.info("queued secret rotation job %s", jid)
                with session_factory()() as db:
                    for jid in template_versions.schedule_refresh(db):
                        log.info("queued monthly template refresh job %s", jid)
                    for jid in template_versions.schedule_retention(db):
                        log.info("queued template retention delete job %s", jid)
            if not paused and time.monotonic() - last_update_check > 86400:
                last_update_check = time.monotonic()
                with session_factory()() as db:
                    log.info("release check: %s", version.check(db))
            if not paused and time.monotonic() - last_purge > 86400:
                last_purge = time.monotonic()
                with session_factory()() as db:
                    log.info("retention purge: %s", maintenance.purge_history(db))
        with session_factory()() as db:
            # Read fresh, not cached: a stale "off" would start a job during maintenance.
            draining = maintenance_mode.get(db)["active"]
        if len(active) < slots and not draining:      # maintenance: running jobs finish, no new ones
            with session_factory()() as db:
                job = claim(db, worker)
            if job is not None:
                log.info("job %s: claimed (%s)", job.id, job.type)
                t = threading.Thread(target=_run_safely, args=(job.id, worker), name=f"job-{job.id}", daemon=True)
                active[job.id] = t
                t.start()
                continue
        stop.wait(3)
    log.info("worker %s: stopping; %d job(s) still running will be failed by recovery", worker, len(active))


_send_lock = threading.Lock()


def _send_backups_in_background() -> None:
    """Uploads can take minutes: in a thread, one at a time, so the loop goes on."""
    if not _send_lock.acquire(blocking=False):
        return

    def go():
        try:
            with session_factory()() as db:
                for link_id, r in stackbackup.send_to_links(db).items():
                    log.info("full backup to linked stack %s: %s", link_id, r.get("error") or f"sent {r.get('archive')}")
        except Exception:  # noqa: BLE001
            log.exception("sending the full backup to linked stacks")
        finally:
            _send_lock.release()

    threading.Thread(target=go, name="backup-send", daemon=True).start()


def _heartbeat(db: Session, worker: str) -> None:
    from ..models import Setting
    value = {"at": utcnow().isoformat(), "worker": worker}
    row = db.get(Setting, "status.worker")
    if row is None:
        db.add(Setting(key="status.worker", value=value))
    else:
        row.value = value
    db.commit()


def _run_safely(job_id: int, worker: str) -> None:
    try:
        run(job_id, worker)
    except Exception:  # noqa: BLE001
        log.exception("job %s: runner error", job_id)
        with session_factory()() as db:
            job = db.get(Job, job_id)
            if job is not None and job.state == "running":
                job.state = "failed"
                job.finished_at = utcnow()
                _append(job, "\nlabape: internal runner error; see the worker log.\n")
                db.commit()
