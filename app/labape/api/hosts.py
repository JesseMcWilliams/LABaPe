"""KVM host registration (admin only), capped at LABAPE_MAX_KVM_HOSTS.

Hosts are local (qemu:///system, the libvirt socket mounted into the
container) or remote over SSH (qemu+ssh://user@host/system, design §22.4).
"""
from __future__ import annotations

import ipaddress
import re
import subprocess
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..engine import hostio
from ..engine.hoststats import assess
from ..errors import FieldError
from ..engine import hostadmin
from ..models import HostFacts, HostMetric, HostStats, Job, KvmHost, Network
from ..permissions import visible_hosts
from ..models import utcnow
from ..security import Principal, audit, client_ip, current_principal, require_roles

router = APIRouter(prefix="/api/hosts", tags=["hosts"])


class HostIn(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    libvirt_uri: str = "qemu:///system"
    vm_storage_path: str = "/data/VMs/LABaPe"
    template_storage_path: str = "/data/VMs/LABaPe/templates"
    bridge: str = "br0"
    concurrency_limit: int = Field(default=2, ge=1, le=16)
    enabled: bool = True
    # SSH administration (design §28.4); set up with POST /{id}/access.
    ssh_address: str = Field(default="", pattern=r"^[A-Za-z0-9.:-]*$", max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="localadmin01", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    # Files (design §29.4): the primary holds them all; a host with a share sees them at the same paths.
    is_primary: bool = False
    files_from_share: bool = False


def _out(h: KvmHost) -> dict:
    return {c: getattr(h, c) for c in ("id", "name", "libvirt_uri", "vm_storage_path", "template_storage_path",
                                       "bridge", "concurrency_limit", "enabled", "ssh_address", "ssh_port",
                                       "ssh_user", "access_status", "access_error", "access_at", "log_forward",
                                       "log_forward_until", "log_forward_error", "is_primary",
                                       "files_from_share")}


def _resources(db: Session, host_id: int, demand: dict | None = None) -> dict:
    row = db.get(HostStats, host_id)
    if row is None or not row.stats:
        return {"collected_at": row.collected_at if row else None, "error": row.error if row else "",
                "stats": None, "assessment": None}
    stats = {k: v for k, v in row.stats.items() if not k.startswith("_")}
    return {"collected_at": row.collected_at, "error": row.error, "stats": stats,
            "assessment": assess(row.stats, demand)}


@router.get("")
def list_hosts(db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    return [{**_out(h), "resources": _resources(db, h.id)} for h in visible_hosts(db, p)]


@router.get("/worker-key")
def worker_key(_: Principal = Depends(require_roles("admin"))):
    """The public key to add to a remote KVM host's authorized_keys."""
    return {"public_key": hostio.ensure_key()}


def _check_uri(uri: str) -> None:
    if uri.startswith("qemu:///"):
        if uri != "qemu:///system":
            raise FieldError("libvirt_uri", "Use qemu:///system for this machine.")
        return
    if not uri.startswith("qemu+ssh://"):
        raise FieldError("libvirt_uri", "Use qemu:///system for this machine, or qemu+ssh://user@host/system.")
    parts = urlsplit(uri)
    if not parts.hostname or not parts.username:
        raise FieldError("libvirt_uri", "A remote host needs the service user and host: qemu+ssh://user@host/system.")
    if parts.path not in ("", "/system") or not re.match(r"^[A-Za-z0-9._-]+$", parts.username):
        raise FieldError("libvirt_uri", "Use the form qemu+ssh://user@host/system (an optional :port after the host).")


@router.get("/{host_id}")
def get_host(host_id: int, db: Session = Depends(get_db), p: Principal = Depends(current_principal)):
    h = next((h for h in visible_hosts(db, p) if h.id == host_id), None)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    return {**_out(h), "resources": _resources(db, h.id)}


@router.get("/{host_id}/resources")
def host_resources(host_id: int, vcpus: int = 0, memory_mb: int = 0, disk_gb: int = 0,
                   db: Session = Depends(get_db), _: Principal = Depends(current_principal)):
    """The host's resources and load; with vcpus/memory_mb/disk_gb, as if
    that much more were deployed (the deploy form's projection)."""
    if db.get(KvmHost, host_id) is None:
        raise HTTPException(status_code=404, detail="No such host")
    return _resources(db, host_id, {"vcpus": vcpus, "memory_mb": memory_mb, "disk_gb": disk_gb})


@router.post("", status_code=201)
def create_host(body: HostIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    limit = get_settings().max_kvm_hosts
    if db.scalar(select(func.count()).select_from(KvmHost)) >= limit:
        raise HTTPException(status_code=409, detail=f"At most {limit} KVM hosts (LABAPE_MAX_KVM_HOSTS)")
    _check_uri(body.libvirt_uri)
    h = KvmHost(**body.model_dump())
    # There's always one primary file host (design §29.4): the first host, or one named so.
    current = db.scalars(select(KvmHost).where(KvmHost.is_primary.is_(True))).all()
    if not current:
        h.is_primary, h.files_from_share = True, False
    elif h.is_primary:
        for other in current:
            other.is_primary = False
    db.add(h)
    db.commit()
    audit(db, p.username, "host.create", object_type="kvm_host", object_id=h.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(h)


@router.put("/{host_id}")
def update_host(host_id: int, body: HostIn, request: Request, db: Session = Depends(get_db),
                p: Principal = Depends(require_roles("admin"))):
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    _check_uri(body.libvirt_uri)
    data = body.model_dump()
    for k in ("ssh_address", "ssh_port", "ssh_user", "is_primary", "files_from_share"):   # kept unless sent
        if k not in body.model_fields_set:
            data.pop(k)
    if data.get("is_primary") is False and h.is_primary:
        raise FieldError("is_primary", "Make another host the primary instead: there's always one.")
    if data.get("is_primary"):
        if data.get("files_from_share", h.files_from_share):
            raise FieldError("is_primary", "The primary holds the files itself; it doesn't read them from a share.")
        for other in db.scalars(select(KvmHost).where(KvmHost.is_primary.is_(True), KvmHost.id != h.id)):
            other.is_primary = False                    # one primary (question 135)
    became_primary = bool(data.get("is_primary")) and not h.is_primary
    for k, v in data.items():
        setattr(h, k, v)
    db.commit()
    if became_primary and hostio.is_remote(h.libvirt_uri):
        try:                                  # the Files page will keep files there (design §29.5)
            hostadmin.ensure_file_roots(db, h)
        except hostadmin.AdminError as exc:
            raise FieldError("is_primary", f"{h.name} is the primary now, but its file locations couldn't be "
                                           f"prepared: {exc}") from exc
    audit(db, p.username, "host.update", object_type="kvm_host", object_id=h.id, detail=body.model_dump(),
          source_ip=client_ip(request))
    return _out(h)


@router.post("/{host_id}/check")
def check_host(host_id: int, request: Request, db: Session = Depends(get_db),
               p: Principal = Depends(require_roles("admin"))):
    """Can the worker reach the host, and does it have what jobs need?"""
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    out: dict = {"remote": hostio.is_remote(h.libvirt_uri), "libvirt": "", "problems": []}
    if out["remote"]:
        hostio.ensure_key()
        hostio.Host(h.libvirt_uri).prime()
    try:
        r = subprocess.run(["virsh", "--connect", hostio.effective_uri(h.libvirt_uri), "version"], capture_output=True,
                           text=True, timeout=30, env=hostio.tool_env(), check=False)
        if r.returncode == 0:
            out["libvirt"] = " ".join(line.strip() for line in r.stdout.splitlines() if "Running hypervisor" in line)
        else:
            out["problems"].append(f"libvirt: {(r.stderr or r.stdout).strip()[:300]}")
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["problems"].append(f"libvirt: {exc}")
    if out["remote"]:
        try:
            c = hostio.Host(h.libvirt_uri).python(hostio.CHECK, {"paths": [h.vm_storage_path, h.template_storage_path]})
            out["tools"], out["paths"] = c["tools"], c["paths"]
            missing = [t for t, ok in c["tools"].items() if not ok]
            if missing:
                out["problems"].append(f"missing on the host: {', '.join(missing)} "
                                       "(install-labape.py --profile kvm installs them)")
            for path, ok in c["paths"].items():
                if not ok:
                    out["problems"].append(f"{path} doesn't exist or isn't writable by the service user")
        except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError) as exc:
            out["problems"].append(f"SSH: {exc}")
    out["ok"] = not out["problems"]
    audit(db, p.username, "host.check", object_type="kvm_host", object_id=h.id, outcome="ok" if out["ok"] else "failed",
          detail={"problems": out["problems"]}, source_ip=client_ip(request))
    return out


# --- administration over SSH (design §28.4, questions 113-115, 120) -----------------------------

def _host(db: Session, host_id: int) -> KvmHost:
    h = db.get(KvmHost, host_id)
    if h is None:
        raise HTTPException(status_code=404, detail="No such host")
    return h


class AccessIn(BaseModel):
    address: str = Field(pattern=r"^[A-Za-z0-9.:-]+$", max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    account: str = Field(default="localadmin01", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    admin_user: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    admin_password: str = Field(min_length=1, max_length=512, repr=False)


def _refresh_later(host_id: int, smart: bool = False) -> None:
    import threading

    from ..db import session_factory

    def go():
        with session_factory()() as s:
            h = s.get(KvmHost, host_id)
            if h is not None and h.access_status == "ready":
                hostadmin.collect_facts(s, h, smart=smart)
                hostadmin.collect_metrics(s, h)
    threading.Thread(target=go, name=f"facts-{host_id}", daemon=True).start()


@router.post("/{host_id}/access")
def setup_access(host_id: int, body: AccessIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    """Create (or refresh) the host's LABaPe access account with an admin login typed in once
    (question 114). The password is used for this one SSH session and never stored or logged."""
    h = _host(db, host_id)
    try:
        hostadmin.setup_access(body.address, body.port, body.admin_user, body.admin_password, body.account)
        h.ssh_address, h.ssh_port, h.ssh_user = body.address, body.port, body.account
        hostadmin.verify(h)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        h.access_status, h.access_error, h.access_at = "failed", str(exc)[:1000], utcnow()
        db.commit()
        audit(db, p.username, "host.access.setup", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={"address": body.address, "account": body.account, "admin_user": body.admin_user,
                      "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("admin_password", f"Setting up access failed: {exc}") from exc
    h.access_status, h.access_error, h.access_at = "ready", "", utcnow()
    db.commit()
    audit(db, p.username, "host.access.setup", object_type="kvm_host", object_id=h.id,
          detail={"address": body.address, "port": body.port, "account": body.account, "admin_user": body.admin_user},
          source_ip=client_ip(request))
    _refresh_later(h.id, smart=True)
    return _out(h)


@router.post("/{host_id}/access/check")
def check_access(host_id: int, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    h = _host(db, host_id)
    if not h.ssh_address:
        raise FieldError("ssh_address", "Set up SSH access first.")
    try:
        hostadmin.verify(h)
        h.access_status, h.access_error = "ready", ""
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        h.access_status, h.access_error = "failed", str(exc)[:1000]
    h.access_at = utcnow()
    db.commit()
    return _out(h)


@router.get("/{host_id}/admin")
def host_admin(host_id: int, hours: int = 24, db: Session = Depends(get_db),
               _: Principal = Depends(require_roles("admin"))):
    """Everything the host's details sections show: facts, SMART, warnings, metric series."""
    h = _host(db, host_id)
    f = db.get(HostFacts, h.id)
    latest = db.scalar(select(HostMetric).where(HostMetric.kvm_host_id == h.id).order_by(HostMetric.at.desc()).limit(1))
    facts, smart = (f.facts if f else {}) or {}, (f.smart if f else {}) or {}
    return {"host": _out(h), "collected_at": f.collected_at if f else None, "smart_at": f.smart_at if f else None,
            "error": f.error if f else "", "facts": facts, "smart": smart,
            "latest": {"at": latest.at, **(latest.data or {})} if latest else None,
            "warnings": hostadmin.warnings(facts, smart, latest.data if latest else None),
            "series": hostadmin.series(db, h, min(max(hours, 1), 168)),
            "resources": _resources(db, h.id)}


@router.post("/{host_id}/admin/refresh")
def refresh_admin(host_id: int, request: Request, db: Session = Depends(get_db),
                  p: Principal = Depends(require_roles("admin"))):
    """Read the facts (and SMART) now, instead of waiting for the hourly collection."""
    h = _host(db, host_id)
    row = hostadmin.collect_facts(db, h, smart=True)
    hostadmin.collect_metrics(db, h)
    if row.error:
        raise HTTPException(status_code=502, detail=f"Reading the host failed: {row.error}")
    return {"collected_at": row.collected_at}


class ManageIn(BaseModel):
    op: str = Field(pattern=r"^(user\.(add|delete|lock|unlock|groups|password)|group\.(add|delete|member_add|member_remove))$")
    name: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    comment: str = Field(default="", max_length=120, pattern=r"^[^:\n]*$")
    groups: list[str] = Field(default=[], max_length=50)
    member: str = Field(default="", max_length=32)
    ssh_key: str = Field(default="", max_length=2000)
    password: str = Field(default="", max_length=512, repr=False)


@router.post("/{host_id}/accounts")
def manage_accounts(host_id: int, body: ManageIn, request: Request, db: Session = Depends(get_db),
                    p: Principal = Depends(require_roles("admin"))):
    """Change one account or group on the host (question 120: admins on KVM hosts)."""
    h = _host(db, host_id)
    try:
        hostadmin.manage(h, body.op, body.name, comment=body.comment, groups=body.groups, member=body.member,
                         ssh_key=body.ssh_key, password=body.password)
    except (hostadmin.AdminError, subprocess.TimeoutExpired) as exc:
        audit(db, p.username, f"host.{body.op}", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={"name": body.name, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("name", str(exc)) from exc
    audit(db, p.username, f"host.{body.op}", object_type="kvm_host", object_id=h.id,
          detail={"name": body.name, "groups": body.groups, "member": body.member,
                  "ssh_key_set": bool(body.ssh_key), "password_set": bool(body.password)},
          source_ip=client_ip(request))
    hostadmin.collect_facts(db, h)
    return {"ok": True}


class LogForwardIn(BaseModel):
    enabled: bool


@router.post("/{host_id}/log-forwarding")
def log_forwarding(host_id: int, body: LogForwardIn, request: Request, db: Session = Depends(get_db),
                   p: Principal = Depends(require_roles("admin"))):
    """Send the host's own logs to the stack's syslog collector, or stop (design §29.1, question 124)."""
    h = _host(db, host_id)
    try:
        hostadmin.set_log_forwarding(db, h, body.enabled)
    except hostadmin.AdminError as exc:
        audit(db, p.username, "host.log_forwarding", object_type="kvm_host", object_id=h.id, outcome="failed",
              detail={"enabled": body.enabled, "error": str(exc)[:300]}, source_ip=client_ip(request))
        raise FieldError("enabled", str(exc)) from exc
    audit(db, p.username, "host.log_forwarding", object_type="kvm_host", object_id=h.id,
          detail={"enabled": body.enabled}, source_ip=client_ip(request))
    return _out(h)


class NetworkAnswer(BaseModel):
    Name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    Cidr: str
    Gateway: str
    DnsServers: list[str] = []
    Bridge: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")
    Nic: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")
    HostAddress: str = "dhcp"
    Addressing: list[str] = ["static", "dhcp"]
    StaticPools: list[str] = []
    DhcpRanges: list[str] = []
    Reserved: list[str] = []


class NetworkChoice(BaseModel):
    """A network from Admin → Networks for the new host (question 127): only what's specific to the host."""
    network_id: int
    Bridge: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")
    Nic: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,15}$")
    HostAddress: str = "dhcp"


class InstallIn(BaseModel):
    """The answers install-labape.py --profile kvm needs (deploy/README.md); no secrets. Networks come
    from the catalog (NetworkChoices) or, for scripts, as full answers (Networks)."""
    ServiceUser: str = Field(default="labape", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    KvmHostName: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    VmStoragePath: str = Field(default="/data/VMs/LABaPe", pattern=r"^/[A-Za-z0-9._/-]+$")
    TemplateStoragePath: str = Field(default="/data/VMs/LABaPe/templates", pattern=r"^/[A-Za-z0-9._/-]+$")
    IsoPath: str = Field(default="/data/OS_Images", pattern=r"^/[A-Za-z0-9._/-]+$")
    Networks: list[NetworkAnswer] = Field(default=[], max_length=8)
    NetworkChoices: list[NetworkChoice] = Field(default=[], max_length=8)
    DefaultNetwork: str = ""
    InstallCli: bool = True              # OpenTofu and Packer: the host's jobs run them
    CliCheckout: bool = False            # a command-line checkout there too (question 126)
    ForwardHostLogs: bool = True        # the host's logs to the stack's syslog collector (question 124)


@router.post("/{host_id}/install")
def install_host(host_id: int, body: InstallIn, request: Request, db: Session = Depends(get_db),
                 p: Principal = Depends(require_roles("admin"))):
    """Prepare a new KVM host: run the installer's kvm profile on it as a job (question 114)."""
    h = _host(db, host_id)
    if h.access_status != "ready":
        raise FieldError("access", "Set up SSH access to this host first.")
    networks, ids = list(body.Networks), {}
    for ch in body.NetworkChoices:
        net = db.get(Network, ch.network_id)
        if net is None or not net.enabled:
            raise FieldError("NetworkChoices", f"Network {ch.network_id} isn't in Admin → Networks (or is turned off).")
        cidr = ipaddress.IPv4Network(net.cidr, strict=False)
        try:
            networks.append(NetworkAnswer(
                Name=net.name, Cidr=net.cidr, Gateway=net.gateway or str(cidr.network_address + 1),
                DnsServers=net.dns_servers or [], Bridge=ch.Bridge, Nic=ch.Nic, HostAddress=ch.HostAddress or "dhcp",
                Addressing=net.addressing or ["static"], StaticPools=net.static_pools or [],
                DhcpRanges=net.dhcp_ranges or [], Reserved=net.reserved or []))
        except ValidationError as exc:
            raise FieldError("NetworkChoices", f"{net.name}: {exc.errors()[0]['msg']}") from exc
        ids[net.name] = net.id
    if not networks:
        raise FieldError("NetworkChoices", "Choose at least one network the host carries.")
    names = [n.Name for n in networks]
    if len(set(names)) != len(names):
        raise FieldError("NetworkChoices", "Each network once.")
    default = body.DefaultNetwork or names[0]
    if default not in names:
        raise FieldError("DefaultNetwork", "The default network must be one of the networks.")
    busy = db.scalar(select(Job).where(Job.kvm_host_id == h.id, Job.type == "host.install",
                                       Job.state.in_(("queued", "running"))))
    if busy:
        raise HTTPException(status_code=409, detail=f"Job {busy.id} is already preparing this host")
    answers = body.model_dump(exclude={"NetworkChoices"})
    answers["Networks"] = [n.model_dump() for n in networks]
    answers["DefaultNetwork"] = default
    answers["_NetworkIds"] = ids          # not an installer answer ("_"): the attachments made on success
    job = Job(type="host.install", params={"answers": answers}, kvm_host_id=h.id, requested_by=p.username)
    db.add(job)
    db.commit()
    audit(db, p.username, "host.install", object_type="kvm_host", object_id=h.id, detail={"job": job.id},
          source_ip=client_ip(request))
    return {"job_id": job.id}
