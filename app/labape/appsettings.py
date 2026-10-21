"""Application settings an admin manages in the UI (design §21.5, §21.6):
backups, history retention, syslog output, SMTP and the directory (group
search). Each section is one row in `settings`; the SMTP password and the
directory API token live in the encrypted secrets store.
"""
from __future__ import annotations

import re
import os
import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from .models import Setting


class BackupSettings(BaseModel):
    enabled: bool = True
    time_of_day: str = Field(default="02:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")   # worker's clock (UTC)
    retention_days: int = Field(default=14, ge=1, le=3650)
    directory: str = "/var/lib/labape/backups"

    @field_validator("directory")
    @classmethod
    def _abs(cls, v: str) -> str:
        if not os.path.isabs(v):
            raise ValueError("use an absolute path")
        return v


class StackBackupSettings(BaseModel):
    """The full-stack backup (design §32): the update agent runs deploy/stack-backup.sh on the host.
    `directory` is a host path the containers see at the same path (under the data root)."""
    enabled: bool = True
    time_of_day: str = Field(default="03:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")   # UTC
    retention_days: int = Field(default=14, ge=1, le=3650)
    directory: str = "/data/backups/stack"
    include_syslog: bool = True
    include_recordings: bool = False
    # A copy of the newest archive to every linked stack, kept on its primary file host (design §36).
    send_to_links: bool = True

    @field_validator("directory")
    @classmethod
    def _abs(cls, v: str) -> str:
        if not os.path.isabs(v):
            raise ValueError("use an absolute path")
        return v.rstrip("/") or "/"


class LeaseSettings(BaseModel):
    """Environment leases (design §40). Off by default: the end of a lease can destroy VMs."""
    enabled: bool = False
    default_days: int = Field(default=30, ge=1, le=3650)
    max_days: int = Field(default=180, ge=1, le=3650)
    remind_days: list[int] = Field(default_factory=lambda: [7, 1])
    grace_days: int = Field(default=7, ge=0, le=365)
    after_grace: str = Field(default="destroy", pattern="^(destroy|keep)$")

    @field_validator("remind_days")
    @classmethod
    def _remind(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 365 for d in v) or len(v) > 5:
            raise ValueError("up to 5 reminders, each 1-365 days before the end")
        return sorted(set(v), reverse=True)


class VmSnapshotSettings(BaseModel):
    """VM snapshots (design §39): automatic ones before a deploy changes existing VMs."""
    auto_before_deploy: bool = False
    keep_auto: int = Field(default=3, ge=1, le=20)


class RetentionSettings(BaseModel):
    job_logs_days: int = Field(default=90, ge=1, le=3650)
    audit_days: int = Field(default=0, ge=0, le=36500)      # 0 = keep forever
    errors_days: int = Field(default=90, ge=1, le=3650)
    deleted_hosts_days: int = Field(default=30, ge=1, le=3650)   # how long a deleted KVM host can be restored
    inactive_tokens_days: int = Field(default=30, ge=1, le=3650)   # how long a revoked or expired API token stays listed
    disk_history_days: int = Field(default=365, ge=7, le=3650)     # how long a drive's SMART readings are kept


class SyslogSettings(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = Field(default=514, ge=1, le=65535)
    protocol: Literal["udp", "tcp", "tls"] = "udp"
    facility: Literal["local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7", "auth",
                      "daemon", "user"] = "local0"
    app_name: str = Field(default="labape", pattern=r"^[A-Za-z0-9_.-]{1,48}$")
    verify_tls: bool = True
    # TLS only: present LABaPe's client certificate (from the platform CA), which the
    # stack's own collector asks for by default (design §29.1, question 122).
    client_cert: bool = False


FACILITY_NAMES = ("kern", "user", "mail", "daemon", "auth", "syslog", "lpr", "news", "uucp", "cron", "authpriv",
                  "ftp", "ntp", "security", "console", "clock", *(f"local{i}" for i in range(8)))
SEVERITY_NAMES = ("emerg", "alert", "crit", "err", "warning", "notice", "info", "debug")


class SyslogForward(BaseModel):
    """One target the stack collector forwards to, e.g. a SIEM (questions 123, 139)."""
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,47}$")
    enabled: bool = True
    host: str = Field(pattern=r"^[A-Za-z0-9.:-]{1,253}$")
    port: int = Field(default=514, ge=1, le=65535)
    transport: Literal["udp", "tcp", "tls"] = "tcp"
    verify_tls: bool = True                     # TLS: check the server's certificate and name
    client_cert: bool = False                   # TLS: present LABaPe's client certificate
    format: Literal["rfc5424", "rfc3164"] = "rfc5424"
    facilities: list[str] = Field(default=[], max_length=24)   # empty: every facility
    min_severity: Literal[SEVERITY_NAMES] = "debug"             # debug: every severity

    @field_validator("facilities")
    @classmethod
    def _facilities(cls, v: list[str]) -> list[str]:
        bad = [f for f in v if f not in FACILITY_NAMES]
        if bad:
            raise ValueError(f"unknown facilities: {', '.join(bad)}")
        return v


class FileDistributionSettings(BaseModel):
    """Copies from the primary file host (design §29.4, question 135)."""
    wait_minutes: int = Field(default=30, ge=0, le=1440)    # how long a job waits for an unreachable primary
    # Every template on every enabled KVM host, copied daily ahead of need (design §43): placement and
    # deploys don't wait for copies. Off by default: templates are large.
    copy_templates_to_all: bool = False


class MonitoringSettings(BaseModel):
    """Alerts about hosts and stacks (design §30, question 143)."""
    email_to: list[str] = Field(default=[], max_length=20)            # empty: no email alerts
    syslog: bool = True                                               # through Settings → Syslog (audit trail)
    # Every host's filesystems: alert when free space falls below this percent (F1; a host may set its own).
    disk_free_pct: int = Field(default=20, ge=1, le=50)
    temp_c: int = Field(default=80, ge=50, le=110)                    # stack hosts (a Pi 5 throttles at 85)
    cert_days: int = Field(default=7, ge=1, le=90)
    backup_hours: int = Field(default=26, ge=2, le=24 * 14)
    peer_failures: int = Field(default=2, ge=1, le=60)               # failed probes in a row before an alert

    @field_validator("email_to")
    @classmethod
    def _emails(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if "@" not in a or " " in a.strip()]
        if bad:
            raise ValueError(f"not email addresses: {', '.join(bad)}")
        return [a.strip() for a in v]


class SyslogCollectorSettings(BaseModel):
    """The stack's syslog collector (design §29.1, questions 121-139)."""
    retention_days: int = Field(default=30, ge=1, le=3650)       # 30 days (user, 2026-10-13; was 90)
    require_client_cert: bool = True            # TLS 6514 (question 122)
    # The address KVM hosts and VMs send to; empty: the web interface's host name.
    address: str = Field(default="", pattern=r"^[A-Za-z0-9.:-]{0,253}$")
    forwards: list[SyslogForward] = Field(default=[], max_length=10)

    @field_validator("forwards")
    @classmethod
    def _unique(cls, v: list[SyslogForward]) -> list[SyslogForward]:
        names = [f.name for f in v]
        if len(names) != len(set(names)):
            raise ValueError("forward names must be unique")
        return v


class SmtpSettings(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["none", "starttls", "ssl"] = "starttls"
    username: str = ""
    from_address: str = Field(default="", max_length=255)
    notify_jobs: bool = False          # email the requester when their job finishes


class PackageUpdateSettings(BaseModel):
    """Catalog → Updates (U1, question 170): when the weekly check runs (UTC)."""
    enabled: bool = True
    day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"] = "sun"
    hour: int = Field(default=3, ge=0, le=23)


class StackMailSettings(BaseModel):
    """The stack's own mail server, Mailpit (design §52): what it keeps, and the relay that forwards mail outside.
    Off (the default), it only catches what LABaPe sends. On, it forwards through `host` the recipients that
    match `allow` (addresses, or @domain for a whole domain), or every recipient."""
    relay: bool = False
    host: str = Field(default="", max_length=253, pattern=r"^[A-Za-z0-9.-]*$")
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["none", "starttls", "ssl"] = "starttls"
    verify_tls: bool = True
    username: str = Field(default="", max_length=255)
    auth: Literal["plain", "login"] = "plain"                       # used when there is a user name
    forward: Literal["listed", "all"] = "listed"
    allow: list[str] = Field(default=[], max_length=50)
    override_from: str = Field(default="", max_length=255)          # replaces the From address (some relays need it)
    keep_messages: int = Field(default=5000, ge=100, le=100000)
    keep_days: int = Field(default=30, ge=1, le=3650)

    @field_validator("username", "override_from")
    @classmethod
    def _one_line(cls, v: str) -> str:
        if re.search(r"[\r\n\x00]", v):
            raise ValueError("no line breaks")
        return v.strip()

    @field_validator("allow")
    @classmethod
    def _allow(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if not re.fullmatch(r"([A-Za-z0-9._%+-]+)?@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", a.strip())]
        if bad:
            raise ValueError(f"not an address or @domain: {', '.join(bad)}")
        return sorted({a.strip().lower() for a in v})


class LabMailRelaySettings(BaseModel):
    """M1 (question 169): the SMTP account the environments' mail servers (Mailpit) may forward through.
    Off by default; its own account, not Settings → Email's (the VMs' owners can read what's on them).
    Only recipients matching `allow` (an address, or a domain as @example.com) are forwarded."""
    enabled: bool = False
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    starttls: bool = True
    username: str = Field(default="", max_length=255)
    allow: list[str] = Field(default=[], max_length=50)

    @field_validator("allow")
    @classmethod
    def _allow(cls, v: list[str]) -> list[str]:
        bad = [a for a in v if not re.fullmatch(r"[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", a.strip())]
        if bad:
            raise ValueError(f"not an address or @domain: {', '.join(bad)}")
        return [a.strip().lower() for a in v]


class DirectorySettings(BaseModel):
    """Authentik API for the group search; blank URL = the OIDC issuer's origin."""
    enabled: bool = False
    api_url: str = Field(default="", pattern=r"^(https?://[^\s/]+/?)?$")
    verify_tls: bool = True


class DownloadSettings(BaseModel):
    """Files from a URL (question 71): HTTPS only unless HTTP is allowed here."""
    allow_http: bool = False
    proxy: str = Field(default="", pattern=r"^(https?://\S+)?$")    # e.g. http://proxy.example:3128


class MaintenanceWindowSettings(BaseModel):
    """The weekly maintenance window (design §23.3, §24): rotations that need
    a restart run inside it; a banner gives notice before it starts. UTC."""
    enabled: bool = False
    weekday: int = Field(default=6, ge=0, le=6)                     # Monday = 0 ... Sunday = 6
    time_of_day: str = Field(default="02:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    duration_minutes: int = Field(default=120, ge=15, le=1440)
    heads_up_hours: int = Field(default=24, ge=0, le=168)


class VmAccountSettings(BaseModel):
    """The admin account made on new Linux VMs (question 91): SSH key only,
    passwordless sudo. Recorded per environment when it's created, so a
    change applies to new environments only."""
    linux_admin_user: str = Field(default="labape", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")

    @field_validator("linux_admin_user")
    @classmethod
    def _not_system(cls, v: str) -> str:
        if v in ("root", "daemon", "bin", "sys", "nobody", "admin", "sudo", "wheel"):
            raise ValueError("choose an account name the OS doesn't already use")
        return v


class BrandingSettings(BaseModel):
    """The app's name in the top bar, browser title and emails (question 68)."""
    app_name: str = Field(default="LABaPe", min_length=1, max_length=40, pattern=r"^[^<>]+$")


class UpdateSettings(BaseModel):
    """Daily check for a newer LABaPe release (question 64); off for air-gapped sites."""
    check_enabled: bool = True
    repo: str = Field(default="JesseMcWilliams/LABaPe", pattern=r"^[\w.-]+/[\w.-]+$")


class TemplateSettings(BaseModel):
    """Dated template versions (Design_Base-Images.md §8, questions 15-16):
    how many versions of each template to keep, and the monthly refresh
    (UTC, worker's clock). 0 versions keeps them all."""
    keep_versions: int = Field(default=3, ge=0, le=50)
    refresh_enabled: bool = True
    refresh_day: int = Field(default=15, ge=1, le=28)      # after Patch Tuesday (the 2nd Tuesday, 8th-14th)
    refresh_time: str = Field(default="03:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class SecretStoreSettings(BaseModel):
    """Where new environment secrets go (design §25, question 94). The API
    checks the store is configured and passing its test."""
    default_environment_store: str = Field(default="builtin", pattern=r"^[a-z][a-z0-9_]{0,63}$")


class RemoteSettings(BaseModel):
    """Browser remote access (design §28.5, questions 116-117)."""
    record_sessions: bool = False                                 # guacd records every session
    idle_minutes: int = Field(default=30, ge=5, le=480)           # close a session with no keyboard/mouse input
    keep_recordings_days: int = Field(default=90, ge=1, le=3650)


class CertificateSettings(BaseModel):
    """Who may ask the platform CA for what (design §65, question 195). The stack's own names and the parent domain of
    its address are always allowed; CA administrators are not limited by these lists."""
    allowed_domains: list[str] = Field(default_factory=list, max_length=50)       # a name is allowed if it is, or ends in, one of these
    allowed_networks: list[str] = Field(default_factory=lambda: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"], max_length=50)
    default_days: int = Field(default=30, ge=1, le=90)
    max_days: int = Field(default=90, ge=1, le=90)                                 # step-ca allows up to 90 days


SECTIONS: dict[str, type[BaseModel]] = {
    "backup": BackupSettings,
    "stack_backup": StackBackupSettings,
    "vm_snapshots": VmSnapshotSettings,
    "leases": LeaseSettings,
    "retention": RetentionSettings,
    "syslog": SyslogSettings,
    "smtp": SmtpSettings,
    "directory": DirectorySettings,
    "updates": UpdateSettings,
    "branding": BrandingSettings,
    "downloads": DownloadSettings,
    "maintenance_window": MaintenanceWindowSettings,
    "vm_accounts": VmAccountSettings,
    "secret_stores": SecretStoreSettings,
    "templates": TemplateSettings,
    "remote": RemoteSettings,
    "certificates": CertificateSettings,
    "syslog_collector": SyslogCollectorSettings,
    "file_distribution": FileDistributionSettings,
    "monitoring": MonitoringSettings,
    "lab_mail_relay": LabMailRelaySettings,
    "stack_mail": StackMailSettings,
    "package_updates": PackageUpdateSettings,
}
SMTP_PASSWORD_SECRET = "settings/smtp_password"
DIRECTORY_TOKEN_SECRET = "settings/directory_token"
# Section -> (write-only request field, secrets-store name, flag reported back).
SECRET_FIELDS = {
    "smtp": ("password", SMTP_PASSWORD_SECRET, "has_password"),
    "directory": ("token", DIRECTORY_TOKEN_SECRET, "has_token"),
    "lab_mail_relay": ("password", "settings/lab_mail_relay_password", "has_password"),
    "stack_mail": ("password", "settings/stack_mail_password", "has_password"),
}

_cache: dict[str, tuple[float, BaseModel]] = {}
CACHE_SECONDS = 15


def get(db: Session, section: str, cached: bool = False) -> BaseModel:
    if cached and section in _cache and time.monotonic() - _cache[section][0] < CACHE_SECONDS:
        return _cache[section][1]
    row = db.get(Setting, f"app.{section}")
    model = SECTIONS[section](**((row.value or {}) if row else {}))
    _cache[section] = (time.monotonic(), model)
    return model


def put(db: Session, section: str, model: BaseModel) -> BaseModel:
    row = db.get(Setting, f"app.{section}")
    if row is None:
        db.add(Setting(key=f"app.{section}", value=model.model_dump()))
    else:
        row.value = model.model_dump()
    db.commit()
    _cache.pop(section, None)
    return model
