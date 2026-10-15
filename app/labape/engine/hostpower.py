"""Normal OS updates and reboots of a KVM or stack host (design §53, batch H2).

Both are jobs the worker runs itself (`host.update`, `host.reboot`), so they are logged, audited and
cancellable like the host install. Update is `apt-get update` + `upgrade` (never `dist-upgrade`) or
`dnf upgrade`. A reboot first waits until no other job is running on the host and keeps new jobs
from starting meanwhile (the runner skips a host with a running `host.reboot`), then schedules
`shutdown -r +1`; for a host other than the one this stack runs on it waits for the host to go away
and come back and refreshes its facts.
"""
from __future__ import annotations

import subprocess
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Job, KvmHost
from . import hostadmin, hostio
from .hostadmin import AdminError, for_host

WAIT_FOR_JOBS_HOURS = 12
BACK_WITHIN_SECONDS = 900

PENDING = r'''
if command -v apt-get >/dev/null 2>&1; then
  echo manager=apt
  apt-get -s upgrade 2>/dev/null | sed -n 's/^Inst \([^ ]*\) \[\([^]]*\)\] (\([^ ]*\).*/\1 \2 -> \3/p'
elif command -v dnf >/dev/null 2>&1; then
  echo manager=dnf
  dnf -q check-update 2>/dev/null | awk 'NF==3 && $1 !~ /^(Obsoleting|Security:)/ {print $1 " -> " $2}'
else
  echo manager=none
fi
if [ -f /var/run/reboot-required ]; then echo reboot_required=yes; fi
'''

UPDATE = r'''
set -e
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get -y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold upgrade
elif command -v dnf >/dev/null 2>&1; then
  dnf -y upgrade
else
  echo "labape: neither apt-get nor dnf is on this host"; exit 1
fi
if [ -f /var/run/reboot-required ]; then echo "labape: the host needs a reboot now"; fi
'''


def pending(h: KvmHost) -> dict:
    """The updates waiting according to the package lists as they are now (the job refreshes them first)."""
    r = for_host(h).run(PENDING, timeout=60)
    if r.returncode != 0:
        raise AdminError((r.stderr or r.stdout).decode(errors="replace").strip()[-300:] or f"exit {r.returncode}")
    out = {"manager": "none", "packages": [], "reboot_required": False}
    for line in r.stdout.decode(errors="replace").splitlines():
        if line.startswith("manager="):
            out["manager"] = line.split("=", 1)[1]
        elif line.startswith("reboot_required="):
            out["reboot_required"] = True
        elif line.strip():
            out["packages"].append(line.strip())
    return out


def _log(job: Job, text: str) -> None:
    from .runner import _append
    _append(job, text)


def run_update(db: Session, job: Job, popen=subprocess.Popen) -> int:
    """The `host.update` job: update the packages, streaming the output into the job's log."""
    from .runner import beat
    h = db.get(KvmHost, job.kvm_host_id)
    if h is None:
        _log(job, "labape: no such KVM host\n")
        return 1
    try:
        ssh = for_host(h)
    except AdminError as exc:
        _log(job, f"labape: {exc}\n")
        return 1
    _log(job, f"labape: updating the packages on {h.name} (the new package lists, then an upgrade; no dist-upgrade)\n")
    with open(job.log_path, "ab", buffering=0) as logf:
        p = popen([*ssh.base(), "sudo -n bash -s"], stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT,
                  env=hostio.tool_env())
        p.stdin.write(UPDATE.encode())
        p.stdin.close()
        while (rc := p.poll()) is None:
            if beat(job.id):
                p.terminate()
                _log(job, "labape: cancelled; the package manager may finish its current package on the host\n")
                return 1
            time.sleep(5)
    if rc != 0:
        _log(job, f"labape: the update failed (exit {rc}); the output above says why\n")
        return rc
    _log(job, "labape: updated\n")
    hostadmin.collect_facts(db, h)
    return 0


def _others_running(db: Session, job: Job) -> int:
    return len(db.scalars(select(Job.id).where(Job.state == "running", Job.kvm_host_id == job.kvm_host_id,
                                               Job.id != job.id)).all())


def _reachable(ssh) -> bool:
    try:
        return ssh.run("true", timeout=15).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_reboot(db: Session, job: Job, sleep=time.sleep) -> int:
    """The `host.reboot` job: wait for the host's other jobs, schedule the reboot, and (unless it is
    the host this stack runs on, which takes the worker down with it) wait for the host to return."""
    from .runner import beat
    from . import stackmon
    h = db.get(KvmHost, job.kvm_host_id)
    if h is None:
        _log(job, "labape: no such KVM host\n")
        return 1
    try:
        ssh = for_host(h)
    except AdminError as exc:
        _log(job, f"labape: {exc}\n")
        return 1
    waited, told = 0, -1
    while (n := _others_running(db, job)) > 0:
        if waited // 60 != told:
            told = waited // 60
            _log(job, f"labape: waiting for {n} running job{'s' if n != 1 else ''} on {h.name} to finish; no new job starts there meanwhile\n")
        if beat(job.id):
            _log(job, "labape: cancelled before the reboot\n")
            return 1
        if waited >= WAIT_FOR_JOBS_HOURS * 3600:
            _log(job, f"labape: the jobs didn't finish within {WAIT_FOR_JOBS_HOURS} hours; not rebooting\n")
            return 1
        sleep(10)
        waited += 10
        db.expire_all()
    who = (job.requested_by or "an admin").replace('"', "")
    r = ssh.run(f'sudo -n shutdown -r +1 "LABaPe: reboot requested by {who}"', timeout=60)
    if r.returncode != 0:
        _log(job, f"labape: couldn't schedule the reboot: {(r.stderr or r.stdout).decode(errors='replace').strip()[-300:]}\n")
        return 1
    if stackmon.is_self(h):
        _log(job, "labape: the reboot starts in a minute; this stack runs on that host, so it is offline until the host is back\n")
        return 0
    _log(job, f"labape: {h.name} reboots in a minute; waiting for it to go down and come back\n")
    deadline = time.monotonic() + 180
    while _reachable(ssh) and time.monotonic() < deadline:     # still up for the minute's grace
        sleep(5)
        if beat(job.id):
            _log(job, "labape: cancelled; the reboot is still scheduled on the host (shutdown -c cancels it)\n")
            return 1
    deadline = time.monotonic() + BACK_WITHIN_SECONDS
    while not _reachable(ssh):
        if time.monotonic() > deadline:
            _log(job, f"labape: {h.name} did not answer within {BACK_WITHIN_SECONDS // 60} minutes of going down\n")
            return 1
        sleep(10)
        beat(job.id)
    _log(job, f"labape: {h.name} is back\n")
    try:
        hostadmin.collect_facts(db, h)
    except Exception as exc:  # noqa: BLE001
        _log(job, f"labape: it is back, but refreshing its facts failed: {exc}\n")
    return 0
