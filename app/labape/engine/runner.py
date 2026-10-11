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
from .templates import sync_all as sync_templates
from ..netpolicy import release
from ..security import audit, get_secret, put_secret

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
    running = {}
    for host_id, in db.execute(select(Job.kvm_host_id).where(Job.state == "running")):
        running[host_id] = running.get(host_id, 0) + 1
    limits = {h.id: (h.concurrency_limit, h.enabled) for h in db.scalars(select(KvmHost))}
    q = select(Job).where(Job.state == "queued").order_by(Job.id)
    if pg:
        q = q.with_for_update(skip_locked=True)
    for job in db.scalars(q):
        if job.kvm_host_id is not None:
            limit, enabled = limits.get(job.kvm_host_id, (0, False))
            if not enabled or running.get(job.kvm_host_id, 0) >= limit:
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
        g = {k: v for k, v in g.items() if k not in CATALOG_KEYS}  # Ansible's business, not OpenTofu's
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
    vault = s.secrets_dir / "secrets.vault.yml"
    if vault.is_file():
        shutil.copy2(vault, work / "secrets.vault.yml")

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
        f"libvirt_uri = {_hcl(host.libvirt_uri)}",
        f"bridge_device = {_hcl(host.bridge)}",
    ]
    if spec.get("static_ip_offset_start") is not None:  # environments created before allocations
        lines.append(f"static_ip_offset_start = {_hcl(spec['static_ip_offset_start'])}")
    lines.append(f"host_groups = {_hcl(profile_host_groups(db, env))}")
    profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_catalog_inputs(db, env, work)

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
        if f.suffix != ".pub" and f.with_name(f.name + ".pub").is_file():
            return str(f)
    return ""


def sync_home() -> None:
    """Copy SSH keys from the secrets mount into the job HOME (OpenSSH wants 0600 files it owns)."""
    s = get_settings()
    ssh_src = s.secrets_dir / "ssh"
    ssh_dst = s.data_dir / "home" / ".ssh"
    ssh_dst.mkdir(parents=True, exist_ok=True)
    os.chmod(ssh_dst, 0o700)
    if ssh_src.is_dir():
        for f in ssh_src.iterdir():
            if f.is_file():
                dst = ssh_dst / f.name
                shutil.copyfile(f, dst)
                os.chmod(dst, 0o644 if f.suffix == ".pub" else 0o600)


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
                *(["--core"] if p.get("core") else []), *(["--disk-gb", str(p["disk_gb"])] if p.get("disk_gb") else [])]
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
        try:
            if host is None:
                raise RuntimeError("job has no KVM host")
            if job.type.startswith("template."):
                work = prepare_template_job(db, job, host)
            elif env is None:
                raise RuntimeError("job has no environment")
            else:
                work = prepare(db, env, host)
            cmd = command_for(job, env, work)
        except Exception as exc:  # noqa: BLE001 — any setup failure fails the job, visibly
            _append(job, f"labape: could not start: {exc}\n")
            _finish(db, job, env, None)
            return

    with open(log_path, "ab", buffering=0) as logf:
        what = env.name if env is not None and not job.type.startswith("template.") else \
            (job.params or {}).get("name") or (job.params or {}).get("new_name", "")
        logf.write(f"labape: job {job_id} ({job.type}) for {what} on {host.name}, worker {worker}\n".encode())
        proc = subprocess.Popen(cmd, cwd=work, env=job_env(host), stdin=subprocess.DEVNULL, stdout=logf,
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

    with Session_() as db:
        job = db.get(Job, job_id)
        env = db.get(Environment, job.environment_id) if job.environment_id else None
        _finish(db, job, env, rc, work=work)


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
            if work is not None:
                shutil.rmtree(work.parent, ignore_errors=True)
    db.commit()
    audit(db, job.requested_by, f"job.{job.state}", object_type="job", object_id=job.id,
          detail={"type": job.type, "exit_code": rc, "environment_id": job.environment_id})


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
            put_secret(db, f"environment/{env.id}/{secret}", f.read_text(encoding="utf-8"))
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
                scan_files(db)
            hash_in_background()
            if time.monotonic() - last_purge > 86400:
                last_purge = time.monotonic()
                with session_factory()() as db:
                    from ..api.admin import purge_user_errors
                    purge_user_errors(db)
        if len(active) < slots:
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
