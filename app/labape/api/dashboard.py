"""The dashboard (design §22.1): KVM host load and environments for
everyone, filtered to what they may see, plus platform health for admins."""
from __future__ import annotations

import datetime as dt
import smtplib
import socket
import ssl
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import appsettings, ca, notify, spread, stores, version
from ..auth import load_providers, provider_enabled
from ..config import get_settings
from ..db import get_db
from ..engine.maintenance import EMAIL_STATUS_KEY
from ..models import (BreakglassGrant, Environment, HostStats, KvmHost, MetricSample, MonitorAlert, MonitorSilence,
                      Secret, Setting, User, utcnow)
from ..naming import instance_names
from ..permissions import visible_hosts, visible_ids
from ..security import Principal, _fernet, audit, current_principal, decrypt_value, get_secret, require_roles

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
BUCKET_MINUTES = 10
INACTIVE = ("destroyed", "new")


# --- series ----------------------------------------------------------------------

def _bucket(at: dt.datetime) -> dt.datetime:
    at = at if at.tzinfo else at.replace(tzinfo=dt.timezone.utc)
    return at.replace(minute=at.minute - at.minute % BUCKET_MINUTES, second=0, microsecond=0)


def _series(rows, fields: tuple[str, ...], combine: dict[str, str] | None = None) -> dict:
    """Ten-minute buckets over 24 hours. Each field is averaged over its
    samples, or summed across VMs first where combine[field] == "sum"."""
    combine = combine or {}
    per: dict[dt.datetime, dict[str, dict[str, list]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for at, vm, data in rows:
        b = _bucket(at)
        for f in fields:
            if data.get(f) is not None:
                per[b][f][vm].append(float(data[f]))
    times = sorted(per)
    out: dict = {"t": [t.isoformat() for t in times]}
    for f in fields:
        col = []
        for t in times:
            by_vm = {vm: sum(v) / len(v) for vm, v in per[t][f].items() if v}
            if not by_vm:
                col.append(None)
            elif combine.get(f) == "sum":
                col.append(round(sum(by_vm.values()), 1))
            else:
                col.append(round(sum(by_vm.values()) / len(by_vm), 1))
        out[f] = col
    return out


# --- what a user sees ------------------------------------------------------------

def _host_out(h: KvmHost, row: HostStats | None, samples) -> dict:
    s = (row.stats if row else None) or {}
    vm = s.get("storage", {}).get("vm", {})
    io = s.get("disk_io", {})
    return {
        "id": h.id, "name": h.name, "enabled": h.enabled,
        "collected_at": row.collected_at if row else None, "error": row.error if row else "",
        "cpus": s.get("cpus"), "cpu_pct": s.get("cpu_pct"), "load": s.get("load", []),
        "memory_total_mb": s.get("memory_total_mb"),
        "memory_used_mb": (s["memory_total_mb"] - s["memory_available_mb"]) if s.get("memory_total_mb") else None,
        "disk_total_gb": vm.get("total_gb"), "disk_free_gb": vm.get("free_gb"),
        "disk_read_bps": io.get("read_bps"), "disk_write_bps": io.get("write_bps"),
        "vms_running": s.get("vms_running"), "vms_defined": s.get("vms_defined"),
        "series": _series(samples, ("cpu_pct", "memory_used_pct", "disk_free_gb", "read_bps", "write_bps")),
    }


def _vm_out(v: dict | None, name: str) -> dict:
    if v is None:
        return {"name": name, "state": "missing", "online": False, "responding": None}
    g = v.get("guest_disk") or {}
    return {
        "name": name, "state": v["state"], "online": v["online"], "responding": v.get("responding"),
        "vcpus": v.get("vcpus"), "cpu_pct": v.get("cpu_pct"),
        "memory_mb": v.get("memory_mb"), "memory_used_mb": v.get("memory_used_mb"),
        "disk_total_gb": g.get("total_gb"), "disk_free_gb": g.get("free_gb"),
        "disk_capacity_gb": v.get("disk_capacity_gb"), "disk_allocated_gb": v.get("disk_allocated_gb"),
        "disk_read_bps": v.get("disk_read_bps"), "disk_write_bps": v.get("disk_write_bps"),
    }


def _sum(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals), 1) if vals else None


def _env_out(e: Environment, host: KvmHost | None, vms_by_name: dict, samples) -> dict:
    names = [n for g in (e.spec or {}).get("host_groups", []) for n in instance_names(g)]
    vms = [_vm_out(vms_by_name.get(n), n) for n in names]
    cpu = [v["cpu_pct"] for v in vms if v.get("cpu_pct") is not None]
    return {
        "id": e.id, "name": e.name, "status": e.status, "kvm_host": host.name if host else "",
        "host_count": len(vms),
        "online": sum(v["online"] for v in vms),
        "not_responding": sum(1 for v in vms if v["online"] and v.get("responding") is False),
        "cpu_pct": round(sum(cpu) / len(cpu), 1) if cpu else None,
        "memory_mb": _sum(v.get("memory_mb") for v in vms if v["online"]),
        "memory_used_mb": _sum(v.get("memory_used_mb") for v in vms),
        "disk_total_gb": _sum(v.get("disk_total_gb") for v in vms),
        "disk_free_gb": _sum(v.get("disk_free_gb") for v in vms),
        "disk_read_bps": _sum(v.get("disk_read_bps") for v in vms),
        "disk_write_bps": _sum(v.get("disk_write_bps") for v in vms),
        "vms": vms,
        "series": _series(samples, ("cpu_pct", "disk_read_bps", "disk_write_bps"),
                          {"disk_read_bps": "sum", "disk_write_bps": "sum"}),
    }


@router.get("")
def dashboard(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    env_ids = visible_ids(db, p, "environment")
    q = select(Environment).where(Environment.status.not_in(INACTIVE)).order_by(Environment.name)
    if env_ids is not None:
        q = q.where(Environment.id.in_(env_ids or {-1}))
    envs = list(db.scalars(q))
    hosts = [h for h in visible_hosts(db, p) if h.enabled]     # a disabled host is parked: not on the dashboard
    host_ids = {h.id for h in hosts} | {hid for e in envs for hid in [e.kvm_host_id, *spread.host_ids(e)]}
    stats = {r.kvm_host_id: r for r in db.scalars(select(HostStats).where(HostStats.kvm_host_id.in_(host_ids or {-1})))}
    since = utcnow() - dt.timedelta(hours=24)
    samples: dict[tuple[int, str], list] = defaultdict(list)
    for at, host_id, vm, data in db.execute(
            select(MetricSample.at, MetricSample.kvm_host_id, MetricSample.vm, MetricSample.data)
            .where(MetricSample.at >= since, MetricSample.kvm_host_id.in_(host_ids or {-1}))
            .order_by(MetricSample.at)):
        samples[(host_id, vm)].append((at, vm, data))
    by_id = {h.id: h for h in db.scalars(select(KvmHost).where(KvmHost.id.in_(host_ids or {-1})))}
    env_out = []
    for e in envs:
        vm_hosts = spread.vm_host_ids(e)                      # each VM on its own host (design §45)
        vms = {v["name"]: v for hid in set(vm_hosts.values()) | {e.kvm_host_id}
               for v in ((stats[hid].stats if hid in stats else None) or {}).get("vms", []) if vm_hosts.get(v["name"]) == hid}
        env_samples = [s for n, hid in vm_hosts.items() for s in samples.get((hid, n), [])]
        env_out.append(_env_out(e, by_id.get(e.kvm_host_id), vms, env_samples))
    return {
        "hosts": [_host_out(h, stats.get(h.id), samples.get((h.id, ""), [])) for h in hosts],
        "environments": env_out,
        "is_admin": p.is_admin,
    }


# --- platform health for admins ------------------------------------------------------

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, seconds: float, fn):
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _cache[key] = (time.monotonic(), value)
    return value


def _check(ok: bool | None, message: str, **extra) -> dict:
    return {"status": "ok" if ok else ("unknown" if ok is None else "error"), "message": message, **extra}


async def _auth(db: Session) -> list[dict]:
    out = []
    for name, prov in load_providers().items():
        if prov.kind == "token":
            continue
        enabled = provider_enabled(db, name)
        hit = _cache.get(f"auth.{name}")
        if hit and time.monotonic() - hit[0] < 60:
            ok, msg = hit[1]
        else:
            ok, msg = await prov.test_connection()
            _cache[f"auth.{name}"] = (time.monotonic(), (ok, msg))
        last = db.scalar(select(func.max(User.last_login)).where(User.provider == name))
        out.append({"name": name, "display_name": prov.display_name, "kind": prov.kind, "enabled": enabled,
                    "last_sign_in": last, **_check(ok if enabled else None, msg if enabled else "Turned off")})
    now = utcnow()
    active = [g for g in db.scalars(select(BreakglassGrant).where(BreakglassGrant.used_at.is_(None),
                                                                  BreakglassGrant.revoked.is_(False)))
              if (g.expires_at if g.expires_at.tzinfo else g.expires_at.replace(tzinfo=dt.timezone.utc)) > now]
    out.append({"name": "breakglass", "display_name": "Break-glass", "kind": "token", "enabled": bool(active),
                "last_sign_in": None,
                **_check(True, f"{len(active)} unused token(s), the last expiring {max(g.expires_at for g in active)}"
                         if active else "No active token (enable it with `labape breakglass enable`)")})
    d = appsettings.get(db, "directory")
    has_token = bool(get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET))
    out.append({"name": "directory", "display_name": "Directory (group search)", "kind": "directory",
                "enabled": d.enabled, "last_sign_in": None,
                **_check(None if not d.enabled else has_token,
                         "Turned off" if not d.enabled else ("API token saved" if has_token else "No API token"))})
    return out


def _vault_check() -> dict:
    """The engine vault opens with its password: in the secret store once imported (design §26)."""
    import tempfile
    from ..db import session_factory
    from ..engine import engine_secrets
    with session_factory()() as db:
        if engine_secrets.in_catalog(db):
            with tempfile.TemporaryDirectory() as d:
                try:
                    engine_secrets.write_vault(db, Path(d))
                except Exception as exc:  # noqa: BLE001 — a store error or a pair that doesn't open
                    return _check(False, f"From the secret store: {exc}")
            return _check(True, "In the secret store; decrypts with its password")
    s = get_settings()
    vault, pw = s.secrets_dir / "secrets.vault.yml", s.secrets_dir / "vault-pass"
    if not vault.is_file() or not pw.is_file():
        return _check(False, f"{'secrets.vault.yml' if not vault.is_file() else 'vault-pass'} is missing in {s.secrets_dir}")
    try:
        r = subprocess.run(["ansible-vault", "view", "--vault-password-file", str(pw), str(vault)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _check(None, f"Couldn't run ansible-vault: {exc}")
    return _check(r.returncode == 0, "Decrypts with the vault password" if r.returncode == 0
                  else f"Doesn't decrypt: {r.stderr.strip()[:200]}")


def _secrets(db: Session) -> list[dict]:
    count = db.scalar(select(func.count()).select_from(Secret).where(Secret.store == "builtin")) or 0
    try:
        f = _fernet()
        ok = f.decrypt(f.encrypt(b"labape-self-test")) == b"labape-self-test"
        sample = db.scalar(select(Secret).where(Secret.store == "builtin").limit(1))
        if sample is not None and decrypt_value(db, sample.value) is None:
            raise ValueError("a stored secret doesn't decrypt")
        builtin = _check(ok, f"Encrypt/decrypt self-test passed; {count} secret(s) stored")
    except Exception as exc:  # noqa: BLE001 — any failure here is the finding
        builtin = _check(False, f"Self-test failed ({type(exc).__name__}); the secret key may have changed")
    ca_ok, ca_msg = _cached("ca", 60, ca.health)
    external = []
    for name in stores.configured(db)[1:]:
        ok, msg = stores.check(db, name, cached=True)
        external.append({"name": f"store.{name}", "display_name": stores.providers()[name].display_name,
                         **_check(ok, msg)})
    return [{"name": "builtin", "display_name": "Built-in store (database, encrypted)", **builtin}, *external,
            {"name": "platform_ca", "display_name": "Platform CA (step-ca)", **_check(ca_ok, ca_msg)},
            {"name": "ansible_vault", "display_name": "Engine Ansible vault",
             **_cached("vault", 600, _vault_check)}]


def _smtp_probe(cfg) -> dict:
    try:
        if cfg.security == "ssl":
            server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=5, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(cfg.host, cfg.port, timeout=5)
        try:
            server.ehlo()
            if cfg.security == "starttls":
                server.starttls(context=ssl.create_default_context())
        finally:
            server.quit()
        return _check(True, f"{cfg.host}:{cfg.port} answered")
    except (OSError, smtplib.SMTPException) as exc:
        return _check(False, f"{cfg.host}:{cfg.port}: {exc}")


def _syslog_probe(cfg) -> dict:
    if cfg.protocol == "udp":
        last = notify.last_syslog
        if last["ok"] is None:
            return _check(None, "UDP can't be checked; nothing sent yet by the API")
        return _check(last["ok"], "Last send succeeded" if last["ok"] else f"Last send failed: {last['error']}")
    try:
        with socket.create_connection((cfg.host, cfg.port), timeout=5):
            pass
        return _check(True, f"{cfg.host}:{cfg.port} accepts connections")
    except OSError as exc:
        return _check(False, f"{cfg.host}:{cfg.port}: {exc}")


def _notifications(db: Session) -> list[dict]:
    smtp, syslog = appsettings.get(db, "smtp"), appsettings.get(db, "syslog")
    row = db.get(Setting, EMAIL_STATUS_KEY)
    last_email = row.value if row else None
    out = [{"name": "smtp", "display_name": "Email (SMTP)", "enabled": smtp.enabled,
            "summary": f"{smtp.host}:{smtp.port} ({smtp.security}), from {smtp.from_address}" if smtp.host else "",
            "last": last_email,
            **(_cached(f"smtp.{smtp.host}:{smtp.port}", 60, lambda: _smtp_probe(smtp)) if smtp.enabled and smtp.host
               else _check(None, "Turned off" if not smtp.enabled else "No server set"))},
           {"name": "syslog", "display_name": "Syslog", "enabled": syslog.enabled,
            "summary": f"{syslog.host}:{syslog.port} ({syslog.protocol}, {syslog.facility})" if syslog.host else "",
            "last": notify.last_syslog if notify.last_syslog["at"] else None,
            **(_cached(f"syslog.{syslog.host}:{syslog.port}", 60, lambda: _syslog_probe(syslog))
               if syslog.enabled and syslog.host else _check(None, "Turned off"))}]
    return out


@router.get("/admin")
async def admin_health(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return {"auth": await _auth(db), "secrets": await run_in_threadpool(_secrets, db), "notifications": _notifications(db),
            "version": version.status(db)}


@router.post("/admin/check-updates")
def check_updates(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    version.check(db)
    return version.status(db)


@router.get("/monitoring")
def monitoring(db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    """Alerts (open and recently cleared) and the stacks being watched (design §30)."""
    from ..engine import stackmon
    return stackmon.view(db)


class AckIn(BaseModel):
    ack: bool = True


class SilenceIn(BaseModel):
    alert_id: int
    hours: int = Field(ge=1, le=720)
    reason: str = Field(default="", max_length=500)


@router.post("/monitoring/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, body: AckIn, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    """Mark an open alert as seen (someone is on it), or undo that (design §31)."""
    a = db.get(MonitorAlert, alert_id)
    if a is None or a.cleared_at is not None:
        raise HTTPException(404, "No such open alert")
    a.acked_at, a.acked_by = (utcnow(), p.username) if body.ack else (None, "")
    db.commit()
    audit(db, p.username, "monitor.alert.ack" if body.ack else "monitor.alert.unack", object_type="monitor_alert",
          object_id=a.id, detail={"message": a.message})
    return {"ok": True}


@router.post("/monitoring/silences")
def add_silence(body: SilenceIn, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """No syslog or email for this alert's condition until the silence ends; still shown (design §31)."""
    a = db.get(MonitorAlert, body.alert_id)
    if a is None:
        raise HTTPException(404, "No such alert")
    now = utcnow()
    s = MonitorSilence(key=a.key, message=a.message, reason=body.reason, created_by=p.username, created_at=now,
                       until=now + dt.timedelta(hours=body.hours))
    db.add(s)
    db.commit()
    audit(db, p.username, "monitor.silence.add", object_type="monitor_silence", object_id=s.id,
          detail={"key": s.key, "message": s.message, "hours": body.hours, "reason": body.reason})
    return {"id": s.id, "until": s.until}


@router.delete("/monitoring/silences/{silence_id}")
def end_silence(silence_id: int, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    s = db.get(MonitorSilence, silence_id)
    if s is None:
        raise HTTPException(404, "No such silence")
    s.until = min(s.until if s.until.tzinfo else s.until.replace(tzinfo=dt.timezone.utc), utcnow())
    db.commit()
    audit(db, p.username, "monitor.silence.end", object_type="monitor_silence", object_id=s.id,
          detail={"key": s.key, "message": s.message})
    return {"ok": True}


@router.get("/stacks")
def all_stacks(db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    """The overall view of every linked stack (design §35): this stack live, the others as last read
    (stale when they stopped answering), and totals across them all. Empty `stacks` without links."""
    from .. import links
    from ..models import StackLink
    rows = list(db.scalars(select(StackLink).order_by(StackLink.name)))
    if not rows:
        return {"stacks": [], "totals": None}
    stacks = [{"self": True, "id": None, "url": links.own_url(), "status": "ok", "last_ok_at": utcnow(), "error": "",
               "summary": links.summary(db)}]
    for link in rows:
        o = links.out(link)
        status = "never" if not link.last_ok_at else ("down" if link.failures else ("stale" if o["stale"] else "ok"))
        stacks.append({"self": False, "id": link.id, "url": link.url, "status": status, "last_ok_at": link.last_ok_at,
                       "error": link.error, "summary": link.summary})
    totals = {"stacks": len(stacks), "answering": sum(1 for s in stacks if s["status"] == "ok"), "hosts": 0,
              "cpus": 0, "memory_total_mb": 0, "memory_used_mb": 0, "disk_total_gb": 0.0, "disk_free_gb": 0.0,
              "vms_running": 0, "vms_defined": 0, "environments": 0, "jobs_running": 0, "jobs_queued": 0,
              "alerts_error": 0, "alerts_warning": 0}
    # A machine two stacks both drive (a KVM host registered in each) counts once, with its newest
    # reading; hosts disabled everywhere add nothing to the capacity.
    machines: dict[str, dict] = {}
    for s in stacks:
        for h in (s["summary"] or {}).get("hosts") or []:
            key = h.get("machine") or f"{s['url']}#{h.get('name')}"
            cur = machines.get(key)
            if cur is None or (h.get("enabled") and (not cur.get("enabled") or
                                                    (h.get("collected_at") or "") > (cur.get("collected_at") or ""))):
                machines[key] = h
    totals["hosts"] = len(machines)
    for h in machines.values():
        if h.get("enabled"):
            for k in ("cpus", "memory_total_mb", "memory_used_mb", "disk_total_gb", "disk_free_gb", "vms_running",
                      "vms_defined"):
                totals[k] += h.get(k) or 0
    for s in stacks:
        m = s["summary"] or {}
        totals["environments"] += len(m.get("environments") or [])
        totals["jobs_running"] += (m.get("jobs") or {}).get("running", 0)
        totals["jobs_queued"] += (m.get("jobs") or {}).get("queued", 0)
        for a in m.get("alerts") or []:
            totals["alerts_error" if a.get("level") == "error" else "alerts_warning"] += 1
    return {"stacks": stacks, "totals": totals}
