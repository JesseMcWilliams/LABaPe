"""Instance settings, read from LABAPE_* environment variables.

Container deployments set these in compose.yaml / Quadlet units
(container/). Secrets (database password, OIDC client secret, session
key) come from the environment or *_FILE variables pointing at container
secrets.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ROLE_MAP = {
    "labape-admins": "admin",
    "labape-template-editors": "template_editor",
    "labape-deployers": "deployer",
    "labape-file-managers": "file_manager",
    "labape-viewers": "viewer",
    "labape-auditors": "auditor",
    "labape-console": "console",
    "labape-ca-admins": "ca_admin",
    "labape-ca-requesters": "ca_requester",
    "labape-ca-auditors": "ca_auditor",
}

# auditor (question 118): reads the audit log, the remote session log and session recordings.
# console (design §59): an API token may run commands and open text sessions on VMs; an admin holds it implicitly.
# ca_admin manages the platform CA and sees every certificate; ca_requester may request certificates (design §65);
# ca_auditor sees every certificate and the CA's details and changes nothing.
ROLES = ("admin", "template_editor", "deployer", "file_manager", "viewer", "auditor", "console", "ca_admin", "ca_requester",
         "ca_auditor")


def _read_file_var(name: str) -> str | None:
    """Support NAME_FILE=/run/secrets/x for any secret setting."""
    path = os.environ.get(f"{name}_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LABAPE_", extra="ignore")

    # --- service ---
    public_url: str = "https://labape.local"
    # The other names the stack answers to (comma-separated, container/.env LABAPE_ALT_NAMES): a sign-in
    # that starts at one of them comes back to it (design §58).
    alt_names: str = ""
    database_url: str = "postgresql+psycopg://labape:labape@postgres/labape"
    secret_key: str = Field(default="", description="Signs session cookies and encrypts stored secrets")
    session_hours: int = 12
    web_dir: Path = Path("/opt/labape/web")            # the built single-page app
    help_dir: Path = Path("/opt/labape/help")          # the user guides the Help page serves (User_Docs/ in a checkout)

    # --- OpenID Connect (Authentik by default) ---
    oidc_issuer: str = ""          # e.g. https://authentik.example/application/o/labape/
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid email profile"
    oidc_groups_claim: str = "groups"
    oidc_display_name: str = "Authentik"

    # Authentik/AD group -> app role; extra bindings live in role_bindings.
    role_map: str = json.dumps(DEFAULT_ROLE_MAP)

    # --- break-glass (Claude_Docs/Planning_Web-Interface-Options.md decision 25) ---
    breakglass_minutes: int = 5
    breakglass_session_minutes: int = 60

    # --- engine ---
    engine_dir: Path = Path("/opt/labape/engine")      # snapshot of scripts/, tofu/, ansible/, ...
    data_dir: Path = Path("/var/lib/labape")           # jobs, logs, home
    config_dir: Path = Path("/etc/labape")             # base environment.yml, manifests
    secrets_dir: Path = Path("/run/labape-secrets")    # vault, vault password, SSH keys (phase 10a)
    tofu_pg_conn: str = ""                             # postgres://... for OpenTofu's pg state backend
    max_kvm_hosts: int = 4
    worker_slots: int = 4                              # concurrent jobs per worker process

    # --- platform CA (design §22.5) ---
    ca_url: str = ""                                   # how the API reaches step-ca, e.g. https://labape-ca:9000
    ca_public_url: str = ""                            # where VMs renew; default https://<public host>:9444
    ca_root: Path = Path("/var/lib/labape-ca/certs/root_ca.crt")
    ca_password_file: Path = Path("/run/secrets/ca_password")
    internal_api_url: str = "http://labape-api:8000"   # the API as the worker's jobs reach it
    update_dir: Path = Path("/var/lib/labape-update")  # shared with the host's update agent (design §23.2)

    # --- stack syslog collector (design §29.1) ---
    syslog_db_url: str = "postgresql+psycopg://labape_syslog@/syslog?host=/run/syslog-db"   # socket, no password
    syslog_conf_dir: Path = Path("/var/lib/labape-syslog/conf")    # rendered for rsyslog
    syslog_tls_dir: Path = Path("/var/lib/labape-syslog/tls")      # its certificates and LABaPe's client one

    @property
    def ca_renew_url(self) -> str:
        if self.ca_public_url:
            return self.ca_public_url.rstrip("/")
        from urllib.parse import urlsplit
        return f"https://{urlsplit(self.public_url).hostname}:9444"

    def model_post_init(self, __context) -> None:  # noqa: D401
        for name in ("secret_key", "oidc_client_secret", "database_url", "tofu_pg_conn"):
            value = _read_file_var(f"LABAPE_{name.upper()}")
            if value:
                object.__setattr__(self, name, value)

    @property
    def role_map_dict(self) -> dict[str, str]:
        try:
            return {k: v for k, v in json.loads(self.role_map).items() if v in ROLES}
        except (ValueError, AttributeError):
            return dict(DEFAULT_ROLE_MAP)

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
