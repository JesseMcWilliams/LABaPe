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
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_factory
from ..models import CatalogItem, Environment, HostNetwork, IpAllocation, Job, KvmHost, Network, utcnow
from ..catalog import expand
from ..files import iso_paths, roots
from ..naming import instance_names
from .. import domain_member, linux_desktop, mail_server, openbao_vms, spread, timezones, vm_passwords
from .files_sync import hash_in_background, scan as scan_files
from .hoststats import collect_all as collect_host_stats
from . import (linked_copy, package_updates, downloads, engine_secrets, filedist, hostadmin, hostio, hostpower, maintenance, migrate, power, rotation,
               secret_health, snapshots, stackbackup, stackmon, template_versions)
from .. import linked_library, maintenance_mode, syslogstack, version
from . import planpreview, vmidentity
from .templates import sync_all as sync_templates
from ..netpolicy import release
from ..security import audit, get_secret, put_secret
from .vmaccess import clear_env_passwords, env_password_overrides

log = logging.getLogger("labape.worker")


HEARTBEAT_SECONDS = 10
STALE_AFTER = dt.timedelta(minutes=2)
CLAIM_LOCK = 0x1AB4E               # pg advisory lock serializing claims (per-host limits)
TERM_GRACE_SECONDS = 60            # tofu needs time to stop cleanly and release its state lock


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# --- claiming ----------------------------------------------------------------

def claim(db: Session, worker: str) -> Job | None:
    """Claim the oldest runnable queued job, or return None."""
    pg = db.bind.dialect.name == "postgresql"
    if pg:
        db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CLAIM_LOCK})
    running, busy_envs, rebooting = {}, set(), set()
    for host_id, env_id, jtype in db.execute(select(Job.kvm_host_id, Job.environment_id, Job.type).where(Job.state == "running")):
        running[host_id] = running.get(host_id, 0) + 1
        busy_envs.add(env_id)
        if jtype == "host.reboot":
            rebooting.add(host_id)            # H2: it waits for the host's jobs to end, so nothing new starts there
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
            if (not enabled and job.type not in HOST_OWN_JOBS) or running.get(job.kvm_host_id, 0) >= max(limit, 1):
                continue
            if job.kvm_host_id in rebooting:
                continue
        job.state = "running"
        job.worker = worker
        job.started_at = job.heartbeat_at = utcnow()
        job.log_path = str(get_settings().logs_dir / f"job-{job.id}.log")
        db.commit()
        return job
    db.commit()
    return None


HOST_OWN_JOBS = ("host.install", "host.update", "host.reboot")        # these also run on a host not enabled for deploys


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


from .runner_prepare import (  # noqa: F401 — moved out (the line limit); re-exported, other modules and tests import them from here
    BACKEND_OVERRIDE,
    CATALOG_KEYS,
    NOT_FOR_TOFU,
    PROFILE,
    _KEY_LINE,
    _hcl,
    _prepare_base,
    command_for,
    current_key_in_inventory,
    history_keys,
    job_env,
    network_catalog,
    overlay_vault,
    prepare,
    prepare_template_job,
    profile_host_groups,
    resolve_update_installers,
    ssh_key_path,
    sync_home,
    write_catalog_inputs,
)

@dataclass
class Part:
    """One command of a job, on one KVM host (design §45: a spread environment's job has several)."""
    host_id: int
    work: Path
    cmd: list[str]
    pulls: list[str] = field(default_factory=list)       # paths to copy back from a remote host afterwards
    groups: list[dict] | None = None                     # templates and ISOs to put on the host (None: all)
    installers: bool = True                              # the chosen applications' installers too
    before: Callable[[], None] | None = None             # run just before (e.g. merge the parts' hosts.json)
    keep_going: bool = False                             # a failure doesn't stop the next parts (destroy)


HOSTS_PART = "hosts.part.json"
HOSTS_MERGED = "hosts.merged.json"


def merge_hosts(sources: list[Path], dest: Path) -> None:
    """Every part's VMs (tofu's `hosts` output, keyed by VM name) in one hosts.json."""
    merged: dict = {}
    for src in sources:
        if not src.is_file():
            raise RuntimeError(f"a part's hosts list is missing ({src.parent.name}); its host didn't finish")
        merged.update(json.loads(src.read_text(encoding="utf-8")))
    dest.write_text(json.dumps(merged, indent=1), encoding="utf-8")


def plan_parts(db: Session, job: Job, env: Environment) -> list[Part]:
    """The commands an environment job runs: one, on its host; or, for an environment spread over
    hosts (design §45), each host's OpenTofu part on that host, then Ansible (or the inventory) once
    on the home host over every part's VMs."""
    home = db.get(KvmHost, env.kvm_host_id)
    if not spread.is_spread(env):
        work = prepare(db, env, home)
        pulls = [planpreview.PLAN_FILE] if job.type == planpreview.JOB_TYPE else ["ansible/inventory", vm_passwords.APPLIED_DIR]
        return [Part(home.id, work, command_for(job, env, work), pulls=pulls)]
    parts: list[Part] = []
    outs: list[Path] = []
    hosts = spread.host_ids(env)
    for hid in hosts:
        host = db.get(KvmHost, hid)
        if host is None:
            raise RuntimeError(f"KVM host {hid} (of a host group) is gone")
        work = prepare(db, env, host, spread.groups_on(env, hid))
        ws, envfile = spread.workspace(env, hid), ["--env-file", str(work / "environment.yml")]
        pulls = [HOSTS_PART]
        if job.type == planpreview.JOB_TYPE:
            cmd = ["bash", str(work / "scripts" / "deploy.sh"), "libvirt", ws, PROFILE, *envfile,
                   "--plan-only", str(work / planpreview.PLAN_FILE)]
            pulls = [planpreview.PLAN_FILE]
        elif job.type == "environment.deploy":
            cmd = ["bash", str(work / "scripts" / "deploy.sh"), "libvirt", ws, PROFILE, *envfile,
                   "--no-ansible", "--hosts-out", str(work / HOSTS_PART)]
        elif job.type == "environment.refresh_addresses":
            cmd = ["bash", str(work / "scripts" / "refresh-addresses.sh"), "libvirt", ws, *envfile,
                   "--hosts-out", str(work / HOSTS_PART)]
        elif job.type == "environment.destroy":
            cmd = ["bash", str(work / "scripts" / "destroy.sh"), "libvirt", ws, PROFILE, *envfile,
                   "--yes", "--delete-workspace"]
        else:
            raise RuntimeError(f"unknown job type {job.type}")
        (work / HOSTS_PART).unlink(missing_ok=True)
        outs.append(work / HOSTS_PART)
        parts.append(Part(hid, work, cmd, pulls=pulls, groups=spread.groups_on(env, hid),
                          installers=False, keep_going=job.type == "environment.destroy"))
    if job.type == "environment.destroy":
        return parts[1:] + parts[:1]                  # the home's part last
    if job.type == planpreview.JOB_TYPE:
        return parts                                  # each host plans its own VMs; no Ansible
    # Ansible from the home host: it reaches every part's VMs over the shared lab networks.
    work = prepare(db, env, home, spread.groups_on(env, home.id))
    merged = work / HOSTS_MERGED
    cmd = ["bash", str(work / "scripts" / "deploy.sh"), "libvirt", env.name, PROFILE,
           "--env-file", str(work / "environment.yml"), "--hosts-from", str(merged)]
    if job.type == "environment.refresh_addresses":
        cmd.append("--no-ansible")
    parts.append(Part(home.id, work, cmd, pulls=["ansible/inventory", vm_passwords.APPLIED_DIR], groups=[],
                      before=lambda: merge_hosts(outs, merged)))
    return parts


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
                  "host.update": hostpower.run_update, "host.reboot": hostpower.run_reboot,
                  "backup.store_linked": stackbackup.store_linked, "vm.snapshot": snapshots.run_job, "environment.move": migrate.run_job,
                  "secret.check": secret_health.run_job,
                  "files.sync_templates": filedist.run_sync_templates, "host.files_move": filedist.run_move_job,
                  "files.update_check": package_updates.run_job, "linked.copy": linked_copy.run_copy}
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
            _copy_isos_from_links(db, job, env)
            if job.type.startswith("template."):
                work = prepare_template_job(db, job, host)
                parts = [Part(host.id, work, command_for(job, env, work), pulls=["ansible/inventory"])]
            elif env is None:
                raise RuntimeError("job has no environment")
            else:
                if job.type == "environment.deploy":    # a safety snapshot first, if the setting asks (§39)
                    snapshots.before_deploy(db, job, env, host, lambda m: _append(job, m))
                    vmidentity.forget_env(db, env)    # the VMs are rebuilt: new keys (question 198)
                parts = plan_parts(db, job, env)
        except Exception as exc:  # noqa: BLE001 — any setup failure fails the job, visibly
            _append(job, f"labape: could not start: {exc}\n")
            _finish(db, job, env, None)
            return
        iso_keys = _iso_keys(job, env)
        what = env.name if env is not None and not job.type.startswith("template.") else \
            (job.params or {}).get("name") or (job.params or {}).get("new_name", "")
        host_name = host.name
        names = {pt.host_id: db.get(KvmHost, pt.host_id).name for pt in parts}

    with open(log_path, "ab", buffering=0) as logf:
        logf.write(f"labape: job {job_id} ({job.type}) for {what} on {host_name}, worker {worker}\n".encode())
        if len(parts) > 1:
            logf.write(f"labape: spread over {len({pt.host_id for pt in parts})} KVM hosts: "
                       f"{len(parts)} parts (design §45)\n".encode())
        sync_home()
        rc: int | None = 0
        for i, part in enumerate(parts):
            if len(parts) > 1:
                logf.write(f"\nlabape: part {i + 1}/{len(parts)} on {names[part.host_id]}: "
                           f"{Path(part.cmd[1]).name} {part.cmd[3]}\n".encode())
            try:
                if part.before is not None:
                    part.before()
            except Exception as exc:  # noqa: BLE001
                logf.write(f"labape: {exc}\n".encode())
                rc = 1
                break
            prc = _execute(Session_, job_id, part, iso_keys, logf, log_path)
            if prc != 0:
                rc = prc if rc == 0 else rc
                if not part.keep_going:
                    break
            if _cancelled(Session_, job_id):
                rc = rc or 1
                break
        work = parts[-1].work
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
        if rc == 0 and job.type == planpreview.JOB_TYPE:
            try:
                planpreview.store(db, job, [pt.work for pt in parts])
            except Exception as exc:  # noqa: BLE001
                _append_path(log_path, f"labape: reading the plan failed: {exc}\n")
                rc = 1
        _finish(db, job, env, rc, work=work)


def _cancelled(Session_, job_id: int) -> bool:
    with Session_() as db:
        return bool(db.get(Job, job_id).cancel_requested)


def _execute(Session_, job_id: int, part: Part, iso_keys: set[str], logf, log_path: Path) -> int | None:
    """Run one part on its host: the files it needs from the primary, the workspace over SSH for a
    remote host, the command (heartbeats, cancel), the results back. Returns its exit code (None: it
    couldn't start)."""
    with Session_() as db:
        host = db.get(KvmHost, part.host_id)
        job = db.get(Job, job_id)
        uri, name = host.libvirt_uri, host.name
        try:
            remote = hostio.RemoteJob(hostio.Host(uri), part.work, job_id) if hostio.is_remote(uri) else None
        except Exception as exc:  # noqa: BLE001
            logf.write(f"labape: could not start on {name}: {exc}\n".encode())
            return None
        run_env = job_env(host)
        run_env["LABAPE_VAULT_PASS_FILE"] = str(part.work / ".vault-pass")
        if history_keys() and remote is None:
            run_env["ANSIBLE_SSH_COMMON_ARGS"] = " ".join(f"-i {k}" for k in history_keys())
        run_env.update(_cert_env(job_id, job.type, remote is not None))
        cert_token = run_env.get("LABAPE_CERT_TOKEN", "")
        cmd = part.cmd
        try:
            # A remote host, or the stack's own host when it isn't the primary file host (design §29.5):
            # the files from the primary (design §29.4).
            if job.type == "environment.deploy" and part.installers and job.environment_id:
                try:        # an installer only a linked stack has is copied here first; if that fails the deploy says what is missing
                    linked_copy.ensure_installers(db, _wanted_installers(db, db.get(Environment, job.environment_id), part.work),
                                                  _TextLog(logf), job.requested_by or "system", beat=lambda: downloads._beat(job_id))
                except downloads.Cancelled as exc:
                    logf.write(f"labape: {exc}\n".encode())
                    return 130
                except (linked_copy.CopyError, downloads.DownloadError, linked_library.LibraryError) as exc:
                    logf.write(f"labape: warning: couldn't copy an installer from a linked stack: {exc}\n".encode())
            p = filedist.primary(db)
            if remote is not None or (p is not None and p.id != host.id):
                _distribute(db, part.work, host, job_id, iso_keys, _TextLog(logf), part.groups, part.installers)
            if remote is not None:
                logf.write(f"labape: running on {remote.host.target} over SSH in {remote.root}\n".encode())
                files = {"vault-pass": part.work / ".vault-pass"}
                if ssh_key_path():   # the scripts check the key has its .pub beside it
                    files["ssh-key"] = Path(ssh_key_path())
                    files["ssh-key.pub"] = Path(ssh_key_path() + ".pub")
                remote.push(run_env, {k: v for k, v in files.items() if v.is_file()})
                cmd, run_env = remote.command(cmd), hostio.tool_env()
        except Exception as exc:  # noqa: BLE001
            what = "could not start on" if remote is not None else "could not get the files from the primary for"
            logf.write(f"labape: {what} {name}: {exc}\n".encode())
            if remote is not None:
                remote.cleanup()
            return None
    try:
        proc = subprocess.Popen(cmd, cwd=part.work, env=run_env, stdin=subprocess.DEVNULL, stdout=logf,
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
                for rel in part.pulls:      # hosts.generated, credentials, inventory; a part's hosts list
                    remote.pull(rel)
            except Exception as exc:  # noqa: BLE001
                _append_path(log_path, f"labape: copying the results back failed: {exc}\n")
                rc = rc or 1
    finally:
        if remote is not None:
            remote.cleanup()
        if cert_token:
            from ..api.ca import token_name
            with Session_() as db:
                put_secret(db, token_name(job_id), "")      # the token dies with the part
    return rc


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


def _copy_isos_from_links(db: Session, job: Job, env: Environment | None) -> None:
    """An OS this job installs from has no ISO here but a linked stack has one: copy it before the job is prepared."""
    keys = _iso_keys(job, env)
    if not keys:
        return
    try:
        linked_copy.ensure_isos(db, keys, _PathLog(Path(job.log_path)), job.requested_by or "system",
                                beat=lambda: downloads._beat(job.id))
    except (linked_copy.CopyError, downloads.DownloadError, linked_library.LibraryError) as exc:
        raise RuntimeError(str(exc)) from exc


def _iso_keys(job: Job, env: Environment | None) -> set[str]:
    """OS keys whose ISO a job installs from (template builds, ISO host groups)."""
    if job.type == "template.build":
        return {(job.params or {}).get("os_key", "")}
    if job.type != "environment.deploy" or env is None:
        return set()
    return {g.get("os", "") for g in (env.spec or {}).get("host_groups", [])
            if (g.get("image_source") or "iso_direct") == "iso_direct"}


def _wanted_installers(db: Session, env: Environment, work: Path) -> set[str]:
    """Installers of the applications chosen here (`source: local` entries of the catalog), as paths under the installers root."""
    catalog = yaml.safe_load((work / "ansible" / "package_catalog.yml").read_text(encoding="utf-8")) \
        if (work / "ansible" / "package_catalog.yml").is_file() else {}
    packages = (catalog or {}).get("packages") or {}
    wanted = set()
    for g in (env.spec or {}).get("host_groups", []):
        chosen = expand(db, g.get("services", []), g.get("applications", []), g.get("packages", []),
                        "windows" if str(g.get("os", "")).startswith("windows") else "linux")
        for app in chosen["applications"]:
            for spec in (packages.get(app) or {}).values():
                if isinstance(spec, dict) and spec.get("source") in ("local", "update") and spec.get("path"):
                    wanted.add(spec["path"])
    return wanted


def _distribute(db: Session, work: Path, host: KvmHost, job_id: int, iso_keys: set[str], log,
                groups: list[dict] | None = None, installers: bool = True) -> None:
    """Before a job runs on a remote host: the files it needs from the primary file host
    (design §29.4), and the workspace pointed at the host's copies. ISOs go to
    <files path>/ISOs, installers to <files path>/Installers (F1; Ansible
    reads them there: it runs on that host), templates into the host's library. `groups`: only
    these groups' templates and ISOs (a spread environment's part, design §45); `installers`: False
    when Ansible runs elsewhere."""
    job = db.get(Job, job_id)
    env = db.get(Environment, job.environment_id) if job.environment_id else None
    if groups is not None:
        iso_keys = iso_keys & {g.get("os", "") for g in groups if (g.get("image_source") or "iso_direct") == "iso_direct"}
    path = work / "environment.yml"
    env_yml = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not host.is_primary and not host.files_from_share:
        try:                                    # F1: beside the VM folder, under a root-owned folder
            hostadmin.ensure_files_path(db, host)
        except hostadmin.AdminError as exc:
            log.write(f"labape: warning: {exc}\n")
    iso_dir = filedist.cache_dir(host, "isos")
    isos = env_yml.get("os_iso_paths") or {}
    for key, src in list(isos.items()):
        if key in iso_keys:
            size, sha = filedist.known(db, src)
            isos[key] = filedist.ensure(db, host, src, f"{iso_dir}/{Path(src).name}", log, size, sha,
                                        legacy=f"{filedist.legacy_dir(host, 'isos')}/{Path(src).name}")
        elif not host.is_primary and not host.files_from_share:
            isos[key] = f"{iso_dir}/{Path(src).name}"
    env_yml["os_iso_paths"] = isos
    path.write_text(yaml.safe_dump(env_yml, sort_keys=False), encoding="utf-8")
    if job.type != "environment.deploy" or env is None:
        return
    for name in sorted({g.get("template") for g in ((env.spec or {}).get("host_groups", []) if groups is None else groups)
                        if g.get("image_source") == "packer_template" and g.get("template")}):
        _from_link = {g.get("template"): g["template_from"] for g in ((env.spec or {}).get("host_groups", []) if groups is None else groups)
                      if g.get("template_from") and g.get("template")}
        if name in _from_link:                         # a linked stack's template: into the primary's library first (row 22)
            linked_copy.ensure_template(db, _from_link[name], name, log)
        filedist.ensure_template(db, host, name, log)
    if not installers:
        return
    wanted = _wanted_installers(db, env, work)
    store = roots(db)["installers"].rstrip("/")
    if host.is_primary or host.files_from_share:
        return
    cache = filedist.cache_dir(host, "installers")
    for rel in sorted(wanted):
        size, sha = filedist.known(db, f"{store}/{rel}")
        filedist.ensure(db, host, f"{store}/{rel}", f"{cache}/{rel}", log, size, sha,
                        legacy=f"{filedist.legacy_dir(host, 'installers')}/{rel}")
    gv = work / "ansible" / "inventory" / "group_vars" / "all" / "labape.yml"
    if gv.parent.is_dir():
        gv.write_text(yaml.safe_dump({"software_store_path": cache}), encoding="utf-8")


def _signal(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def _finish(db: Session, job: Job, env: Environment | None, rc: int | None, work: Path | None = None) -> None:
    db.refresh(job, ["cancel_requested"])      # a job run in this session was loaded before the cancel came in
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
        if job.type == planpreview.JOB_TYPE:
            pass                                      # a plan changes nothing: the status stays
        elif job.type == "environment.refresh_addresses":
            # Read-only for the VMs: the environment's status stays as it was.
            if job.state == "succeeded":
                _collect_outputs(db, job, env, work)
        elif job.state != "succeeded":
            env.status = "failed"
            if job.type == "environment.deploy" and work is not None and vm_passwords.promote(db, env, work):
                inv = get_secret(db, f"environment/{env.id}/inventory") or ""
                if inv:
                    put_secret(db, f"environment/{env.id}/inventory", vm_passwords.apply_to_inventory(db, env, inv))
        elif job.type == "environment.deploy":
            env.status = "deployed"
            if work is not None:
                vm_passwords.promote(db, env, work)
            _collect_outputs(db, job, env, work)
            _record_identities(db, job, env)
        elif job.type == "environment.destroy":
            env.status = "destroyed"
            env.inventory = ""
            vmidentity.forget_env(db, env)      # before the addresses are released
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


def _record_identities(db: Session, job: Job, env: Environment) -> None:
    """The deployed VMs' SSH host keys and WinRM certificates, to check later connections against (question 198)."""
    try:
        inv = yaml.safe_load(get_secret(db, f"environment/{env.id}/inventory") or "") or {}
        done = vmidentity.record_env(db, env, inv, log=lambda m: _append(job, m + "\n"))
        if done:
            _append(job, f"labape: recorded the identity of {len(done)} VM(s) for later connections\n")
    except Exception as exc:  # noqa: BLE001 — a missing record is learned at the first connection
        _append(job, f"labape: could not record the VMs' identities: {exc}\n")


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
                text = vm_passwords.apply_to_sheet(db, env, text)        # each member VM's own (A2)
            else:
                text = vm_passwords.apply_to_inventory(db, env, text)
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
            with session_factory()() as db:
                from .. import userstatus
                for what in userstatus.sweep_due(db):   # owners deactivated or removed in the provider (question 196)
                    log.info("user check: %s", what)
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
                    for jid in secret_health.schedule(db):     # S1: is each secret still good
                        log.info("queued secret check job %s", jid)
                with session_factory()() as db:
                    from .. import openbao_people
                    for what in openbao_people.schedule(db):   # V1b: owners' access in OpenBao
                        log.info("OpenBao people access: %s", what)
                with session_factory()() as db:
                    for jid in package_updates.schedule(db):           # U1: Catalog → Updates, weekly
                        log.info("queued the weekly package update check (job %s)", jid)
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
