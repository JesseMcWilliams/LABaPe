"""Application settings an admin manages in the UI (design §21.5, §21.6):
backups, history retention, syslog output, SMTP and the directory (group
search). Each section is one row in `settings`; the SMTP password and the
directory API token live in the encrypted secrets store.
"""
from __future__ import annotations

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


class RetentionSettings(BaseModel):
    job_logs_days: int = Field(default=90, ge=1, le=3650)
    audit_days: int = Field(default=0, ge=0, le=36500)      # 0 = keep forever
    errors_days: int = Field(default=90, ge=1, le=3650)


class SyslogSettings(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = Field(default=514, ge=1, le=65535)
    protocol: Literal["udp", "tcp", "tls"] = "udp"
    facility: Literal["local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7", "auth",
                      "daemon", "user"] = "local0"
    app_name: str = Field(default="labape", pattern=r"^[A-Za-z0-9_.-]{1,48}$")
    verify_tls: bool = True


class SmtpSettings(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["none", "starttls", "ssl"] = "starttls"
    username: str = ""
    from_address: str = Field(default="", max_length=255)
    notify_jobs: bool = False          # email the requester when their job finishes


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


SECTIONS: dict[str, type[BaseModel]] = {
    "backup": BackupSettings,
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
}
SMTP_PASSWORD_SECRET = "settings/smtp_password"
DIRECTORY_TOKEN_SECRET = "settings/directory_token"
# Section -> (write-only request field, secrets-store name, flag reported back).
SECRET_FIELDS = {
    "smtp": ("password", SMTP_PASSWORD_SECRET, "has_password"),
    "directory": ("token", DIRECTORY_TOKEN_SECRET, "has_token"),
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
