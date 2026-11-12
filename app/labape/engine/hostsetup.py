"""Applying a host's setup again (question 229): the installer's alert and disk steps, on a host that is already
registered, from the web interface.

Preparing a host (`host.install`) runs the whole installer once. The steps that keep a host's problems reachable
(mail, RAID and SMART alerts, self-tests, the diagnostic sudo rule) arrived later, so a host prepared before them,
or registered without being prepared, never got them, and an update of the stack didn't reach it. `host.setup`
copies the installer and runs only those steps (each is idempotent and says what it did). The monitoring tick
queues it for a host whose last run predates the current steps, so an update of the stack brings every host along.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import shlex
import subprocess
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Job, KvmHost, Setting, utcnow
from . import hostadmin, hostio

HEALTH_STEPS = ("Prereq.AlertMail", "Prereq.RaidAlerts", "Prereq.SmartAlerts", "Prereq.SmartTests",
                "Prereq.DiagSudo", "Prereq.RaidDiskTimeouts")
KEY = "hostsetup.host{}"
AUTO_KEY = "hostsetup.auto"
RETRY_AFTER = dt.timedelta(hours=24)          # a failed automatic run isn't repeated sooner


def revision() -> str:
    """Changes when the installer's steps change: a hash of its step code (the image carries it), so an update that
    touches them re-applies the setup, and one that doesn't leaves the hosts alone."""
    from ..config import get_settings
    h = hashlib.sha256()
    root = get_settings().engine_dir / "deploy"
    for name in ("install-labape.py", "labape_deploy/steps.py"):
        try:
            h.update((root / name).read_bytes())
        except OSError:
            from .. import version
            h.update(version.current().encode())
    return h.hexdigest()[:12]


def state(db: Session, host_id: int) -> dict:
    row = db.get(Setting, KEY.format(host_id))
    return dict(row.value) if row else {}


def _put(db: Session, host_id: int, value: dict) -> None:
    row = db.get(Setting, KEY.format(host_id))
    if row is None:
        db.add(Setting(key=KEY.format(host_id), value=value))
    else:
        row.value = value
    db.commit()


def auto_enabled(db: Session) -> bool:
    row = db.get(Setting, AUTO_KEY)
    return True if row is None else bool((row.value or {}).get("on", True))


def push_installer(h: KvmHost, ssh, log, answers: dict, answers_file: str, popen=subprocess.Popen) -> bool:
    """Copy the installer to the host and write the answers file next to it, owned by the service user.
    True when something failed (already logged)."""
    from ..config import get_settings
    root = hostadmin.INSTALL_ROOT
    engine = get_settings().engine_dir
    # The copy needs tar on the host; minimal RHEL-family images don't have it.
    tar_ok = ssh.run("command -v tar >/dev/null || sudo -n dnf install -y -q tar >/dev/null 2>&1 "
                     "|| sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tar >/dev/null 2>&1; "
                     "command -v tar >/dev/null", timeout=300)
    if tar_ok.returncode != 0:
        log("labape: the host has no tar and installing it failed (dnf or apt-get)\n")
        return True
    pruned = ssh.run(f"sudo -n python3 - {root} {' '.join(hostadmin.INSTALL_KEEP)}", input=hostadmin.PRUNE.encode(), timeout=120)
    if pruned.returncode != 0:
        log(f"labape: tidying {root} failed: {pruned.stderr.decode(errors='replace').strip()[-300:]}\n")
        return True
    # Only what this copy doesn't bring back is worth reporting.
    gone = [g.removeprefix("removed ") for g in pruned.stdout.decode(errors="replace").split("\n") if g]
    gone = [g for g in gone if not any(g == f or g.startswith(f + "/") or f.startswith(g + "/") for f in hostadmin.INSTALL_FILES)]
    if gone:
        log(f"labape: removed what older installs left in {root}: {', '.join(gone)}\n")
    log(f"labape: copying the installer to {h.ssh_user}@{h.ssh_address}:{root}\n")
    tar = popen(["tar", "-C", str(engine), *[f"--exclude={x}" for x in hostadmin.INSTALL_EXCLUDE], "-czf", "-",
                 *[d for d in hostadmin.INSTALL_FILES if (engine / d).exists()]], stdout=subprocess.PIPE)
    r = subprocess.run([*ssh.base(), f"sudo -n mkdir -p {root} && sudo -n tar -xzf - -C {root}"],
                       stdin=tar.stdout, capture_output=True, timeout=900, env=hostio.tool_env(), check=False)
    tar.stdout.close()
    tar.wait()
    if r.returncode != 0 or tar.returncode != 0:
        log(f"labape: copying failed: {r.stderr.decode(errors='replace').strip()[-400:]}\n")
        return True
    put = ssh.run(f"sudo -n tee {root}/deploy/{answers_file} >/dev/null && sudo -n chmod 0600 {root}/deploy/{answers_file}",
                  input=json.dumps({k: v for k, v in answers.items() if k != "ForwardHostLogs"}, indent=2).encode())
    # The service user owns the repository copy, as on any LABaPe host: the installer runs
    # ansible-vault and the CLI checks as that user. Created here if it doesn't exist yet
    # (the same as the installer's Prereq.User).
    svc = shlex.quote(answers.get("ServiceUser") or "labape")
    own = ssh.run(f"(id -u {svc} >/dev/null 2>&1 || sudo -n useradd --create-home --shell /bin/bash {svc}) "
                  f"&& sudo -n chown -R {svc}:{svc} {root}")
    if own.returncode != 0:
        log(f"labape: handing {root} to the service user failed: {own.stderr.decode(errors='replace').strip()[-300:]}\n")
        return True
    if put.returncode != 0:
        log("labape: writing the answer file failed\n")
        return True
    return False


def run_setup(db: Session, job, popen=subprocess.Popen) -> int:
    """The `host.setup` job: the alert and disk steps of the installer, on the host, as root."""
    from .runner import _append, beat
    h = db.get(KvmHost, job.kvm_host_id)
    log = lambda m: _append(job, m)                                         # noqa: E731
    if h is None:
        log("labape: no such KVM host\n")
        return 1
    try:
        ssh = hostadmin.for_host(h)
    except hostadmin.AdminError as exc:
        log(f"labape: {exc}\n")
        return 1
    answers = {"Profile": "kvm", "InstallWebUi": False, "TemplatesToBuild": [],
               "AlertMailHost": hostadmin.stack_mail_host(db)}
    if hostadmin.service_user(h):
        answers["ServiceUser"] = hostadmin.service_user(h)
    if not answers["AlertMailHost"]:
        log("labape: the stack has no address for its mail server yet, so the host's own mail can't be pointed at it\n")
    if push_installer(h, ssh, log, answers, "answers.setup.json", popen):
        return 1
    root = hostadmin.INSTALL_ROOT
    steps = " ".join(f"--step {shlex.quote(s)}" for s in HEALTH_STEPS)
    cmd = (f"cd {root} && sudo -n env DEBIAN_FRONTEND=noninteractive python3 deploy/install-labape.py --profile kvm "
           f"--instance setup --config-file deploy/answers.setup.json --non-interactive {steps}")
    log(f"labape: running the alert and disk steps on {h.name}\n")
    with open(job.log_path, "ab", buffering=0) as logf:
        p = popen([*ssh.base(), cmd], stdout=logf, stderr=subprocess.STDOUT, env=hostio.tool_env())
        while (rc := p.poll()) is None:
            if beat(job.id):
                p.terminate()
                log("labape: cancelled; the installer may still finish its current step on the host\n")
                return 1
            time.sleep(5)
    ok = rc == 0
    _put(db, h.id, {"revision": revision() if ok else state(db, h.id).get("revision"), "at": utcnow().isoformat(),
                    "ok": ok, "attempt": utcnow().isoformat(), "job": job.id})
    log("labape: done\n" if ok else f"labape: the installer failed (exit {rc}); see the steps above\n")
    return rc


def queue(db: Session, h: KvmHost, who: str, auto: bool = False) -> Job | None:
    """A host.setup job for the host, unless one is queued or running. None when there is one."""
    busy = db.scalar(select(Job).where(Job.kvm_host_id == h.id, Job.type == "host.setup",
                                       Job.state.in_(("queued", "running"))))
    if busy:
        return None
    job = Job(type="host.setup", params={"auto": auto}, kvm_host_id=h.id, requested_by=who)
    db.add(job)
    db.commit()
    return job


def due(db: Session, now: dt.datetime) -> None:
    """From the monitoring tick: a ready host whose setup predates the current steps gets one run (a failed run
    waits a day before the next try). Off with Setting hostsetup.auto = {"on": false}."""
    if not auto_enabled(db):
        return
    rev = revision()
    for h in db.scalars(select(KvmHost).where(KvmHost.access_status == "ready", KvmHost.deleted_at.is_(None)).order_by(KvmHost.id)):
        s = state(db, h.id)
        if s.get("revision") == rev:
            continue
        tried = s.get("attempt")
        if tried and now - dt.datetime.fromisoformat(tried).astimezone(dt.timezone.utc) < RETRY_AFTER:
            continue
        job = queue(db, h, "system", auto=True)
        if job is not None:
            _put(db, h.id, {**s, "attempt": now.isoformat()})
