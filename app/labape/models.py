"""Database tables (Claude_Docs/Planning_Web-Interface-Design.md §4), phase 10a subset."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(64))          # auth provider name
    subject: Mapped[str] = mapped_column(String(255))          # provider's stable user id
    username: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255), default="")
    display_name: Mapped[str] = mapped_column(String(255), default="")
    groups: Mapped[list] = mapped_column(JSON, default=list)   # snapshot from the last sign-in
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # the user's own: page size (L1)
    disabled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)   # no sessions or tokens while set
    disabled_reason: Mapped[str] = mapped_column(String(32), default="")    # provider (the sweep, question 196) | admin


class RoleBinding(Base):
    """Group or user -> role, on top of the LABAPE_ROLE_MAP defaults."""

    __tablename__ = "role_bindings"
    __table_args__ = (UniqueConstraint("principal_type", "principal", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(16))    # "group" | "user"
    principal: Mapped[str] = mapped_column(String(255))        # group name or username
    role: Mapped[str] = mapped_column(String(32))
    subject: Mapped[str | None] = mapped_column(String(320), nullable=True)   # user bindings: the account it was made for (question 200)


class KvmHost(Base):
    __tablename__ = "kvm_hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    libvirt_uri: Mapped[str] = mapped_column(String(255), default="qemu:///system")
    vm_storage_path: Mapped[str] = mapped_column(String(255), default="/data/VMs/LABaPe")
    template_storage_path: Mapped[str] = mapped_column(String(255), default="/data/VMs/LABaPe/templates")
    # Its copies of LABaPe's files (F1): ISOs/, Installers/, Other/ under it; empty: beside the VM
    # folder (/data/VMs/LABaPe -> /data/Files; filedist.files_path). The primary keeps the stack's roots.
    files_path: Mapped[str] = mapped_column(String(255), default="")
    # Alert when a filesystem's free space falls below this (percent); None: Settings → Monitoring's.
    disk_free_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bridge: Mapped[str] = mapped_column(String(64), default="br0")
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Administration over SSH (design §28.4, questions 113-114): a dedicated account
    # (default localadmin01), separate from the service user, reached with the worker's
    # host-admin key. Set up once from credentials an admin types in (never stored).
    ssh_address: Mapped[str] = mapped_column(String(255), default="")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(32), default="localadmin01")
    access_status: Mapped[str] = mapped_column(String(16), default="none")   # none | ready | failed
    access_error: Mapped[str] = mapped_column(Text, default="")
    access_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The host's own logs go to the stack's syslog collector over TLS (design §29.1, question 124);
    # `until` is its client certificate's expiry, renewed 10 days before.
    log_forward: Mapped[bool] = mapped_column(Boolean, default=False)
    log_forward_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    log_forward_error: Mapped[str] = mapped_column(Text, default="")
    # Files (design §29.4, questions 131-135): one host is the primary and holds every file;
    # the others get copies when a job needs them, unless they see the primary's file
    # locations at the same paths through a share their admin mounted.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    files_from_share: Mapped[bool] = mapped_column(Boolean, default=False)
    # What runs on it (design §30, question 142): kvm, stack (a LABaPe web stack only) or both;
    # a stack's https address, so the stacks can watch each other.
    role: Mapped[str] = mapped_column(String(8), default="kvm")
    stack_url: Mapped[str] = mapped_column(String(255), default="")
    # Deleted (design §55): hidden everywhere (db.py), purged after the retention period; `purged` marks the
    # hidden record kept when environments still point at the host.
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str] = mapped_column(String(255), default="")
    purged: Mapped[bool] = mapped_column(Boolean, default=False)


class Network(Base):
    """Central network catalog entry: what may be used (design §20.1)."""

    __tablename__ = "networks"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(63), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    cidr: Mapped[str] = mapped_column(String(43))
    gateway: Mapped[str] = mapped_column(String(15), default="")      # empty = the network's .1
    dns_servers: Mapped[list] = mapped_column(JSON, default=list)      # empty = [gateway]
    vlan: Mapped[int | None] = mapped_column(Integer, nullable=True)    # informational
    addressing: Mapped[list] = mapped_column(JSON, default=lambda: ["static"])
    static_pools: Mapped[list] = mapped_column(JSON, default=list)     # ["a-b", ...]
    dhcp_ranges: Mapped[list] = mapped_column(JSON, default=list)      # the DHCP server's scope
    reserved: Mapped[list] = mapped_column(JSON, default=list)
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)    # empty + empty groups = anyone who deploys
    allowed_groups: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class HostNetwork(Base):
    """A catalog network carried by a KVM host, and on which bridge (design §20.2)."""

    __tablename__ = "host_networks"
    __table_args__ = (UniqueConstraint("kvm_host_id", "network_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"))
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    bridge: Mapped[str] = mapped_column(String(64))
    static_pool: Mapped[list] = mapped_column(JSON, default=list)      # optional slice of the network's pools
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class IpAllocation(Base):
    """A static address handed to an environment's VM (design §20.4)."""

    __tablename__ = "ip_allocations"
    __table_args__ = (UniqueConstraint("network_id", "address"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    network_id: Mapped[int] = mapped_column(ForeignKey("networks.id"))
    address: Mapped[str] = mapped_column(String(15))
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    vm_name: Mapped[str] = mapped_column(String(63))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(63), unique=True)  # also the OpenTofu workspace
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"))
    spec: Mapped[dict] = mapped_column(JSON)                   # host_groups, static_ip_offset_start, ...
    status: Mapped[str] = mapped_column(String(32), default="new")
    inventory: Mapped[str] = mapped_column(Text, default="")   # generated inventory, credentials removed
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # The lease (design §40): when it ends (None: never), and what it has done (reminded, stopped, destroy job).
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    kvm_host: Mapped[KvmHost] = relationship()


class Grant(Base):
    """Ownership / use of an object by a user or group (design §5)."""

    __tablename__ = "grants"
    __table_args__ = (UniqueConstraint("object_type", "object_id", "principal_type", "principal", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    object_type: Mapped[str] = mapped_column(String(32))      # environment | template | file
    object_id: Mapped[int] = mapped_column(Integer)
    principal_type: Mapped[str] = mapped_column(String(16))   # user | group
    principal: Mapped[str] = mapped_column(String(255))
    level: Mapped[str] = mapped_column(String(16))            # owner | user
    subject: Mapped[str | None] = mapped_column(String(320), nullable=True)   # user grants: the account it was made for (question 200)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    kvm_host_id: Mapped[int | None] = mapped_column(ForeignKey("kvm_hosts.id"), nullable=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(255))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_of: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str] = mapped_column(String(255), default="")
    worker: Mapped[str] = mapped_column(String(128), default="")      # host:pid of the claiming worker
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Template(Base):
    """A template in a KVM host's library, with its metadata (design §21).

    The worker syncs these from <template_storage_path>/<name>.qcow2 and
    the <name>.json metadata file beside it (written by the build/promote
    scripts, or inferred from the name the first time)."""

    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("kvm_host_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"))
    name: Mapped[str] = mapped_column(String(128))
    os_key: Mapped[str] = mapped_column(String(64), default="")       # e.g. rocky9, windows_server_2022
    edition: Mapped[str] = mapped_column(String(32), default="")      # windows: core | desktop
    sku: Mapped[str] = mapped_column(String(16), default="")          # windows server: standard | datacenter
    disk_gb: Mapped[int] = mapped_column(Integer, default=0)          # virtual size: the clone's disk floor
    min_cpu: Mapped[int] = mapped_column(Integer, default=1)
    min_memory_mb: Mapped[int] = mapped_column(Integer, default=1024)
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")              # quirks, after-tasks
    source: Mapped[str] = mapped_column(String(16), default="inferred")  # metadata | inferred
    present: Mapped[bool] = mapped_column(Boolean, default=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)        # edited here; worker rewrites the file
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StoredFile(Base):
    """A file in one of the file manager's locations (design §9, §21.3):
    ISOs, installers or other files. Uploaded through the UI, or found
    there by the worker's scan."""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32), index=True)     # isos | installers | files
    name: Mapped[str] = mapped_column(String(255))
    path: Mapped[str] = mapped_column(String(1024), unique=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    mtime: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)
    os_key: Mapped[str] = mapped_column(String(64), default="")       # ISOs: which OS installs from it
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # ISOs: the one used for its os_key
    # Installers: the OSes they may be used on, generic and/or fine-grained (ostargets.py, question 112).
    targets: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[str] = mapped_column(String(255), default="")  # empty: found by the scan
    uploaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    present: Mapped[bool] = mapped_column(Boolean, default=True)


class Upload(Base):
    """An in-progress resumable (tus) upload."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    category: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(255))
    length: Mapped[int] = mapped_column(BigInteger)
    offset: Mapped[int] = mapped_column(BigInteger, default=0)
    partial_path: Mapped[str] = mapped_column(String(1024))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogItem(Base):
    """A service, application or package users can add to host groups
    (design §21.4). Only published items can be chosen."""

    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("kind", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)        # service | application | package
    name: Mapped[str] = mapped_column(String(63))
    display_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | published | retired
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    platforms: Mapped[list] = mapped_column(JSON, default=list)      # windows / linux
    created_by: Mapped[str] = mapped_column(String(255), default="")
    updated_by: Mapped[str] = mapped_column(String(255), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CatalogRequest(Base):
    """A user's request for a new catalog item (questions 51, 52)."""

    __tablename__ = "catalog_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    details: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open")   # open | accepted | done | declined
    response: Mapped[str] = mapped_column(Text, default="")
    requested_by: Mapped[str] = mapped_column(String(255))
    handled_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HostStats(Base):
    """A KVM host's latest resources and load, collected by the worker
    every minute (design §21.2). One row per host, overwritten."""

    __tablename__ = "host_stats"

    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), primary_key=True)
    collected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class RemoteSession(Base):
    """A browser SSH/RDP/VNC session through guacd (design §28.5, questions 116-118): who,
    to what, when, and its recording. The one-time token that opens the tunnel is stored
    hashed and cleared once used."""

    __tablename__ = "remote_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    username: Mapped[str] = mapped_column(String(255))
    target_type: Mapped[str] = mapped_column(String(16))               # vm | host
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True)
    kvm_host_id: Mapped[int | None] = mapped_column(ForeignKey("kvm_hosts.id"), nullable=True)
    target: Mapped[str] = mapped_column(String(255))                   # VM or host name
    address: Mapped[str] = mapped_column(String(255), default="")
    protocol: Mapped[str] = mapped_column(String(8))                   # ssh | rdp | vnc
    # Signed in as one of the environment's accounts (E2): its secret's name under environment/<id>/.
    account: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(16), default="pending")  # pending | open | closed | failed
    recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str] = mapped_column(Text, default="")
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StackStatus(Base):
    """The latest checks of a host that runs a LABaPe stack (design §30): its containers and volumes
    (over the access account), and either this stack's own checks (backups, certificates, database
    sizes) or, for another stack, how its health address answered."""

    __tablename__ = "stack_status"

    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), primary_key=True)
    checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class MonitorAlert(Base):
    """A warning that started (and maybe cleared): what the dashboard lists and what was sent to
    syslog and email (design §30, question 143). `key` identifies the condition."""

    __tablename__ = "monitor_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    kvm_host_id: Mapped[int | None] = mapped_column(ForeignKey("kvm_hosts.id"), nullable=True)
    level: Mapped[str] = mapped_column(String(8))                        # warning | error
    message: Mapped[str] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    cleared_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    # When its start went to syslog and email; empty while it waits for a silence or maintenance
    # to end (design §31). A clear is announced only when the start was.
    announced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by: Mapped[str] = mapped_column(String(255), default="")


class MonitorSilence(Base):
    """No announcements for alerts with this key until `until` (design §31). The alerts are still
    recorded and shown."""

    __tablename__ = "monitor_silences"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(255), index=True)
    message: Mapped[str] = mapped_column(Text, default="")              # the alert it was made from
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    until: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class HostFacts(Base):
    """What an administrator wants to know about a KVM host, read over SSH (design §28.4):
    disks and SMART, volumes, NICs, sensors, OS, accounts, groups. One row per host,
    overwritten hourly (SMART daily) or on demand."""

    __tablename__ = "host_facts"

    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), primary_key=True)
    collected_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    smart_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    smart: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")


class HostMetric(Base):
    """A KVM host's metrics over SSH every 5 minutes, kept 7 days (question 115): CPU,
    memory, filesystems, temperatures, NIC counters."""

    __tablename__ = "host_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), index=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class MetricSample(Base):
    """One minute's load for a KVM host (vm = "") or one of its VMs, kept
    24 hours for the dashboard's sparklines (design §22.1)."""

    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), index=True)
    vm: Mapped[str] = mapped_column(String(64), default="", index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class DiskSample(Base):
    """One SMART reading of one physical drive of a KVM or stack host (design §64, question 189): the numbers worth a
    trend line (reallocated, pending and uncorrectable sectors, temperature, wear). Read every 6 hours and on demand,
    kept `disk_history_days` (Settings → History)."""

    __tablename__ = "disk_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), index=True)
    disk: Mapped[str] = mapped_column(String(128), index=True)        # the drive's serial number (else its device name)
    device: Mapped[str] = mapped_column(String(64), default="")
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Certificate(Base):
    """A certificate the platform CA signed (design §65): who asked, for which names, until when, and whether it was
    revoked. The register is LABaPe's own: step-ca has no list to read. Only the public certificate is kept."""

    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    serial: Mapped[str] = mapped_column(String(80), unique=True, index=True)      # decimal, as step-ca's `revoke` takes it
    subject: Mapped[str] = mapped_column(String(255), default="")
    sans: Mapped[list] = mapped_column(JSON, default=list)
    not_before: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    not_after: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    issuer: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(8), default="active")               # active | revoked
    requested_by: Mapped[str] = mapped_column(String(255), default="", index=True)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True, index=True)
    host: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="request")             # request | engine | stack | imported
    note: Mapped[str] = mapped_column(Text, default="")
    pem: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str] = mapped_column(String(255), default="")
    revoke_reason: Mapped[str] = mapped_column(String(255), default="")


class UserError(Base):
    """A request the API refused (validation, policy, permission), kept so
    admins can see where users struggle (design §21, question 59)."""

    __tablename__ = "user_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    username: Mapped[str] = mapped_column(String(255), default="")
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[list] = mapped_column(JSON, default=list)          # [{"field", "message"}]
    origin: Mapped[str] = mapped_column(String(16), default="server")  # server | browser


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(32), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    outcome: Mapped[str] = mapped_column(String(16), default="ok")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    source_ip: Mapped[str] = mapped_column(String(64), default="")


class BreakglassGrant(Base):
    __tablename__ = "breakglass_grants"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(255))       # host/container user who ran the command
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    local_only: Mapped[bool] = mapped_column(Boolean, default=False)


class VmIdentity(Base):
    """What a VM was known by when LABaPe first saw it (question 198, engine/vmidentity.py): its SSH host keys
    (`type key` lines) or the SHA-256 of its WinRM certificate. Later connections must match."""

    __tablename__ = "vm_identities"
    __table_args__ = (UniqueConstraint("address", "kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    address: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(16))                       # ssh | winrm
    value: Mapped[str] = mapped_column(Text)
    environment_id: Mapped[int | None] = mapped_column(ForeignKey("environments.id"), nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UpdateSource(Base):
    """Catalog → Updates (U1, design §50.7): where a package's newest version is found, how many
    versions to keep, and what was downloaded (newest first: version, file id, name, when)."""

    __tablename__ = "update_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(16))                     # dotnet | github | url | page | chocolatey
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    targets: Mapped[list] = mapped_column(JSON, default=list)         # the installers' OS targets (ostargets)
    keep: Mapped[int] = mapped_column(Integer, default=3)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_version: Mapped[str] = mapped_column(String(64), default="")
    last_error: Mapped[str] = mapped_column(Text, default="")
    history: Mapped[list] = mapped_column(JSON, default=list)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class SecretDataKey(Base):
    """A data key of the built-in store (design §24): secrets are encrypted
    with the active data key, which is stored wrapped by the master key
    (LABAPE_SECRET_KEY). Rotating a data key re-encrypts the store with no
    restart; retired keys stay a while for writes already in flight."""

    __tablename__ = "secret_data_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    wrapped: Mapped[str] = mapped_column(Text)                 # Fernet(master).encrypt(data key)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    retired_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SecretPolicy(Base):
    """How one internal secret is rotated (design §24)."""

    __tablename__ = "secret_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)      # e.g. platform.data_key
    kind: Mapped[str] = mapped_column(String(64))                    # rotator key (engine/rotation.py)
    label: Mapped[str] = mapped_column(String(255), default="")
    store: Mapped[str] = mapped_column(String(64), default="builtin")
    secret_class: Mapped[str] = mapped_column(String(16), default="privileged")  # privileged | read_only | external
    interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    needs_restart: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    managed_externally: Mapped[bool] = mapped_column(Boolean, default=False)   # the store rotates it
    elevated_ref: Mapped[str] = mapped_column(String(128), default="")         # elevated account doing it
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending")         # ok | failed | pending | manual
    last_rotated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Health (S1, question 161): does the stored value still work; checked every quarter of the period.
    check_status: Mapped[str] = mapped_column(String(16), default="unchecked")   # ok | failed | recovered | unchecked | none
    check_error: Mapped[str] = mapped_column(Text, default="")
    last_checked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_check_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Secret(Base):
    """The secret catalog (design §11, §25): one row per stored secret. A
    built-in secret's value is here, encrypted; an external one's row holds
    no value, only the store it lives in. `policy_id` links the rotation
    policy (rotator, elevated account, period) that changes it, if any."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)                   # "v2:..." token; "" for an external store
    store: Mapped[str] = mapped_column(String(64), default="builtin")
    policy_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # V2 (R21): a shared environment secret's VMs, the only ones that may read it themselves.
    vms: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StackLink(Base):
    """Another LABaPe stack linked with this one (design §34). Each side holds the token it calls
    the other with (in the secret store, `link.<id>.outbound`) and the hash of the token the other
    calls it with; HTTPS to the peer is checked against its platform CA root, pinned at link time."""

    __tablename__ = "stack_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(255), unique=True)
    ca_pem: Mapped[str] = mapped_column(Text)
    ca_fingerprint: Mapped[str] = mapped_column(String(64))
    inbound_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_ok_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_try_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)     # the peer's last summary
    # Sign-on (design §47.2): this side lets the peer's users sign in ("Sign in with <peer>").
    sso_in: Mapped[bool] = mapped_column(Boolean, default=False)
    sso_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class StackLinkInvite(Base):
    """A one-time link code this stack handed out (only its hash is kept)."""

    __tablename__ = "stack_link_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by: Mapped[str] = mapped_column(String(255), default="")


class ApiToken(Base):
    """A token a script or CI signs in with (design §37): `Authorization: Bearer lpat_...` (personal:
    acts as its owner, with at most the owner's current roles) or `lpst_...` (service: the roles an
    admin gave it, as `svc:<name>`). Only the SHA-256 is kept; `prefix` identifies it in lists."""

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))                      # personal | service
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))         # owner, or the admin who made it
    roles: Mapped[list] = mapped_column(JSON, default=list)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_ip: Mapped[str] = mapped_column(String(64), default="")
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str] = mapped_column(String(255), default="")
    # Console scope and limits (design §59): `environments` null = every environment the owner can use.
    environments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    host_consoles: Mapped[bool] = mapped_column(Boolean, default=False)
    require_account: Mapped[bool] = mapped_column(Boolean, default=True)
    rate_per_minute: Mapped[int] = mapped_column(Integer, default=30)
    max_session_minutes: Mapped[int] = mapped_column(Integer, default=30)
    max_output_kb: Mapped[int] = mapped_column(Integer, default=256)


class VmSnapshot(Base):
    """A snapshot libvirt keeps (design §39), as the worker last read it after a snapshot job or a
    refresh (the API can't reach libvirt), with LABaPe's notes: who took it, why, automatic."""

    __tablename__ = "vm_snapshots"
    __table_args__ = (UniqueConstraint("environment_id", "vm", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id"), index=True)
    vm: Mapped[str] = mapped_column(String(63))
    name: Mapped[str] = mapped_column(String(63))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    state: Mapped[str] = mapped_column(String(16), default="")        # the VM's state when it was taken
    parent: Mapped[str] = mapped_column(String(63), default="")
    current: Mapped[bool] = mapped_column(Boolean, default=False)


class Quota(Base):
    """Limits on a user's environments (design §41): for a user, a group, or everyone (`default`).
    None is no limit."""

    __tablename__ = "quotas"
    __table_args__ = (UniqueConstraint("principal_type", "principal"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(16))           # user | group | default
    principal: Mapped[str] = mapped_column(String(255), default="")
    max_vcpus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_memory_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_disk_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_environments: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EnvTemplate(Base):
    """An environment template (design §68): the host groups of an environment without its name, host, VM names or
    addresses, plus the few values (parameters) a person fills in when starting from it. Built-in starters have
    no owner and cannot be edited."""

    __tablename__ = "env_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    owner: Mapped[str] = mapped_column(String(255), default="", index=True)
    shared: Mapped[bool] = mapped_column(Boolean, default=False)          # anyone who deploys may use it
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)                # {"host_groups": [...], "timezone": ...}
    params: Mapped[list] = mapped_column(JSON, default=list)              # [{"key","label","group","field","default"}]
    # Sub-assemblies: other templates this one is made of, [{"template_id","name","prefix","label","overrides"}]; their
    # host groups are added with the prefix on their names, so one template can hold the same part twice.
    includes: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)              # raised when the groups, parameters or parts change
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EnvTemplateVersion(Base):
    """What a template was at one version, so a template that includes it can stay on that version (design §71)."""

    __tablename__ = "env_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("env_templates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[list] = mapped_column(JSON, default=list)
    includes: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
