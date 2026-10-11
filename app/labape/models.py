"""Database tables (Claude_Docs/Planning_Web-Interface-Design.md §4), phase 10a subset."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
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


class Secret(Base):
    """Built-in secrets store (encrypted at rest); design §11."""

    __tablename__ = "secrets"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text)                   # Fernet token
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
