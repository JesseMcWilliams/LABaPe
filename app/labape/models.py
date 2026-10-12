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


class RoleBinding(Base):
    """Group or user -> role, on top of the LABAPE_ROLE_MAP defaults."""

    __tablename__ = "role_bindings"
    __table_args__ = (UniqueConstraint("principal_type", "principal", "role"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(16))    # "group" | "user"
    principal: Mapped[str] = mapped_column(String(255))        # group name or username
    role: Mapped[str] = mapped_column(String(32))


class KvmHost(Base):
    __tablename__ = "kvm_hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    libvirt_uri: Mapped[str] = mapped_column(String(255), default="qemu:///system")
    vm_storage_path: Mapped[str] = mapped_column(String(255), default="/data/VMs/LABaPe")
    template_storage_path: Mapped[str] = mapped_column(String(255), default="/data/VMs/LABaPe/templates")
    bridge: Mapped[str] = mapped_column(String(64), default="br0")
    concurrency_limit: Mapped[int] = mapped_column(Integer, default=2)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


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


class MetricSample(Base):
    """One minute's load for a KVM host (vm = "") or one of its VMs, kept
    24 hours for the dashboard's sparklines (design §22.1)."""

    __tablename__ = "metric_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    kvm_host_id: Mapped[int] = mapped_column(ForeignKey("kvm_hosts.id"), index=True)
    vm: Mapped[str] = mapped_column(String(64), default="", index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


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
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
