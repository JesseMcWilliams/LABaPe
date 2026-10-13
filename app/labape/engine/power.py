"""VM power: start, shut down, restart and force off, for chosen VMs or a whole
environment (design §28.3, questions 108-111).

A whole-environment start goes group by group in start order: the groups with
the lowest `start_order` first (domain controllers default to 1, everything
else to 2), so whatever provides a service to others is up before its
clients, and the KVM host isn't hit by every boot at once. After a group
starts, the next waits until each of its VMs answers through the QEMU guest
agent (or the wait times out), then for the group's `start_delay_seconds`.
Shutting down goes in reverse order. A shutdown is clean (guest agent, then
ACPI); a VM that hasn't stopped by the timeout fails the job, unless the
request said to force it off then. Force off (`virsh destroy`) is its own
action. Runs in the worker with `virsh --connect`, so remote KVM hosts work
the same way.
"""
from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..models import Environment, Job, KvmHost
from ..naming import instance_names
from . import hostio

ACTIONS = ("start", "shutdown", "restart", "force_off")
WAITS = ("agent", "none")
DEFAULT_TIMEOUT = 300          # seconds a VM gets to boot to its agent, or to shut down
POLL = 3


class PowerError(RuntimeError):
    pass


def default_order(group: dict) -> int:
    """Domain controllers first (they serve DNS and logons to the rest), then everything else."""
    return 1 if "domain_controller" in (group.get("roles") or []) else 2


def plan(env: Environment, only: list[str] | None = None) -> list[tuple[int, int, str, list[str]]]:
    """[(start_order, delay_seconds, wait, [vm names])] in start order, one entry per order value.
    `only` limits it to those VMs (keeping their groups' order)."""
    by_order: dict[int, tuple[int, str, list[str]]] = {}
    for g in (env.spec or {}).get("host_groups", []):
        names = [n for n in instance_names(g) if only is None or n in only]
        if not names:
            continue
        order = int(g.get("start_order") or default_order(g))
        group_wait = g.get("start_wait") or "agent"
        if order in by_order:
            # Groups sharing an order start together: the longest delay, and wait if any of them waits.
            delay, wait, vms = by_order[order]
            delay = max(delay, int(g.get("start_delay_seconds") or 0))
            wait = "agent" if "agent" in (wait, group_wait) else "none"
        else:
            delay, wait, vms = int(g.get("start_delay_seconds") or 0), group_wait, []
        by_order[order] = (delay, wait, vms + names)
    return [(o, d, w, v) for o, (d, w, v) in sorted(by_order.items())]


# --- virsh (one function, so tests can swap it) ----------------------------------------------

def virsh(uri: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["virsh", "--connect", hostio.effective_uri(uri), *args], capture_output=True, text=True,
                          timeout=timeout, check=False, env=hostio.tool_env())


class Vms:
    """The virsh calls the power job makes, against one KVM host."""

    def __init__(self, uri: str, run: Callable[..., subprocess.CompletedProcess] | None = None):
        self.uri, self.run = uri, run or virsh

    def state(self, name: str) -> str:
        r = self.run(self.uri, "domstate", name)
        if r.returncode != 0:
            raise PowerError(f"{name}: {(r.stderr or r.stdout).strip() or 'no such VM on this host'}")
        return r.stdout.strip()

    def start(self, name: str) -> None:
        r = self.run(self.uri, "start", name)
        if r.returncode != 0 and "already active" not in (r.stderr or ""):
            raise PowerError(f"{name}: start failed: {(r.stderr or r.stdout).strip()}")

    def shutdown(self, name: str) -> None:
        # The guest agent is the cleanest; ACPI if the guest has no agent.
        r = self.run(self.uri, "shutdown", name, "--mode", "agent,acpi")
        if r.returncode != 0 and "not running" not in (r.stderr or ""):
            raise PowerError(f"{name}: shutdown failed: {(r.stderr or r.stdout).strip()}")

    def destroy(self, name: str) -> None:
        r = self.run(self.uri, "destroy", name)
        if r.returncode != 0 and "not running" not in (r.stderr or ""):
            raise PowerError(f"{name}: force off failed: {(r.stderr or r.stdout).strip()}")

    def agent(self, name: str) -> bool | None:
        """True when the guest agent answers; False when it doesn't (yet); None when the VM has no agent channel."""
        r = self.run(self.uri, "qemu-agent-command", name, json.dumps({"execute": "guest-ping"}), timeout=15)
        if r.returncode == 0:
            return True
        return None if "not configured" in (r.stderr or "") else False


def _running(state: str) -> bool:
    return state in ("running", "paused", "in shutdown", "pmsuspended")


def start_all(vms: Vms, steps, log, timeout: int, sleep=time.sleep, now=time.monotonic) -> None:
    for order, delay, wait, names in steps:
        log(f"labape: start order {order}: {', '.join(names)}\n")
        for n in names:
            if _running(vms.state(n)):
                log(f"labape:   {n} is already running\n")
            else:
                vms.start(n)
                log(f"labape:   {n} started\n")
        if wait == "agent":
            deadline, pending = now() + timeout, list(names)
            while pending:
                for n in list(pending):
                    ok = vms.agent(n)
                    if ok is None:
                        log(f"labape:   {n} has no guest agent channel; not waiting for it\n")
                        pending.remove(n)
                    elif ok:
                        log(f"labape:   {n} is up (guest agent answers)\n")
                        pending.remove(n)
                if pending and now() >= deadline:
                    log(f"labape:   still no guest agent after {timeout} s: {', '.join(pending)}; going on\n")
                    break
                if pending:
                    sleep(POLL)
        if delay:
            log(f"labape:   waiting {delay} s before the next group\n")
            sleep(delay)


def shutdown_all(vms: Vms, steps, log, timeout: int, force_after: bool, sleep=time.sleep,
                 now=time.monotonic) -> list[str]:
    """Reverse start order. Returns the VMs still running after the timeout (forced off when force_after)."""
    left: list[str] = []
    for order, _delay, _wait, names in reversed(steps):
        log(f"labape: shutting down start order {order}: {', '.join(names)}\n")
        was_up = [n for n in names if _running(vms.state(n))]
        for n in names:
            if n not in was_up:
                log(f"labape:   {n} is already off\n")
        for n in was_up:
            vms.shutdown(n)
        up = list(was_up)
        deadline = now() + timeout
        while up and now() < deadline:
            sleep(POLL)
            up = [n for n in up if _running(vms.state(n))]
        for n in was_up:
            if n not in up:
                log(f"labape:   {n} is off\n")
        if up:
            if force_after:
                for n in up:
                    vms.destroy(n)
                    log(f"labape:   {n} didn't shut down in {timeout} s; forced off\n")
            else:
                log(f"labape:   didn't shut down in {timeout} s: {', '.join(up)}\n")
                left += up
                return left        # stop here: clients' servers stay up when a client is stuck
    return left


def force_off_all(vms: Vms, steps, log) -> None:
    for _order, _delay, _wait, names in reversed(steps):
        for n in names:
            if _running(vms.state(n)):
                vms.destroy(n)
                log(f"labape: {n} forced off\n")
            else:
                log(f"labape: {n} is already off\n")


def run_job(db: Session, job: Job, run=None, sleep=time.sleep, now=time.monotonic) -> int:
    """The `environment.power` job. params: action, vms (None = all), force_after_timeout, timeout_seconds."""
    from .runner import _append
    p = job.params or {}
    env = db.get(Environment, job.environment_id)
    host = db.get(KvmHost, job.kvm_host_id)
    if env is None or host is None:
        _append(job, "labape: the environment or its KVM host is gone\n")
        return 1
    action, only = p.get("action"), p.get("vms") or None
    timeout = int(p.get("timeout_seconds") or DEFAULT_TIMEOUT)
    steps = plan(env, only)
    log = lambda msg: _append(job, msg)                                   # noqa: E731
    what = ", ".join(only) if only else f"all of {env.name}"
    log(f"labape: {action.replace('_', ' ')} {what} on {host.name}\n")
    vms = Vms(host.libvirt_uri, run)
    try:
        if action == "start":
            start_all(vms, steps, log, timeout, sleep, now)
        elif action == "force_off":
            force_off_all(vms, steps, log)
        elif action in ("shutdown", "restart"):
            left = shutdown_all(vms, steps, log, timeout, bool(p.get("force_after_timeout")), sleep, now)
            if left:
                log(f"labape: failed: {', '.join(left)} didn't shut down. Use Force off, or shut down again with "
                    "'force off after the timeout'.\n")
                return 1
            if action == "restart":
                start_all(vms, steps, log, timeout, sleep, now)
        else:
            log(f"labape: unknown action {action}\n")
            return 1
    except PowerError as exc:
        log(f"labape: failed: {exc}\n")
        return 1
    log("labape: done\n")
    return 0
