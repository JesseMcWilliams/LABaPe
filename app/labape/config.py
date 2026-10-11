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
}

ROLES = ("admin", "template_editor", "deployer", "file_manager", "viewer")


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
    database_url: str = "postgresql+psycopg://labape:labape@postgres/labape"
    secret_key: str = Field(default="", description="Signs session cookies and encrypts stored secrets")
    session_hours: int = 12
    web_dir: Path = Path("/opt/labape/web")            # the built single-page app

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
