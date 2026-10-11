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


class UpdateSettings(BaseModel):
    """Daily check for a newer LABaPe release (question 64); off for air-gapped sites."""
    check_enabled: bool = True
    repo: str = Field(default="JesseMcWilliams/LABaPe", pattern=r"^[\w.-]+/[\w.-]+$")


SECTIONS: dict[str, type[BaseModel]] = {
    "backup": BackupSettings,
    "retention": RetentionSettings,
    "syslog": SyslogSettings,
    "smtp": SmtpSettings,
    "directory": DirectorySettings,
    "updates": UpdateSettings,
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
