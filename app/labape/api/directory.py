"""Group search for fields that take Authentik group names (design §21.6).

With a read-only Authentik API token (Settings > Directory) the search asks
Authentik. Without one, or when Authentik can't be reached, it searches the
groups LABaPe has seen at sign-in and in role bindings, and says so; the
field still accepts any name typed in.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import appsettings
from ..config import get_settings
from ..db import get_db
from ..errors import FieldError
from ..models import RoleBinding, User
from ..security import Principal, audit, client_ip, get_secret, require_roles

router = APIRouter(prefix="/api", tags=["directory"])
LIMIT = 20


def api_base(cfg) -> str:
    if cfg.api_url:
        return cfg.api_url.rstrip("/")
    issuer = urlsplit(get_settings().oidc_issuer)
    return f"{issuer.scheme}://{issuer.netloc}" if issuer.netloc else ""


def authentik_groups(cfg, token: str, q: str, timeout: float = 5.0) -> list[str]:
    """GET /api/v3/core/groups/?search=q; raises httpx.HTTPError on failure."""
    r = httpx.get(f"{api_base(cfg)}/api/v3/core/groups/",
                  params={"search": q, "page_size": LIMIT, "ordering": "name", "include_users": "false"},
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                  timeout=timeout, verify=cfg.verify_tls)
    r.raise_for_status()
    return [g["name"] for g in r.json().get("results", [])]


def known_groups(db: Session, q: str) -> list[str]:
    names: set[str] = set()
    for groups, in db.execute(select(User.groups)):
        names.update(g for g in (groups or []) if isinstance(g, str))
    names.update(db.scalars(select(RoleBinding.principal).where(RoleBinding.principal_type == "group")))
    ql = q.lower()
    return sorted(n for n in names if ql in n.lower())[:LIMIT]


@router.get("/directory/groups")
def search_groups(q: str = "", db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    cfg = appsettings.get(db, "directory", cached=True)
    token = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) or ""
    if cfg.enabled and token and api_base(cfg):
        try:
            return {"source": "authentik", "groups": authentik_groups(cfg, token, q.strip()), "warning": ""}
        except (httpx.HTTPError, ValueError) as exc:
            warning = f"Authentik group search failed ({_why(exc)}); showing groups LABaPe has seen at sign-in."
    else:
        warning = "" if not cfg.enabled else "Directory search needs an API token; showing groups LABaPe has seen at sign-in."
    return {"source": "known", "groups": known_groups(db, q.strip()), "warning": warning}


@router.post("/admin/settings/directory/test")
def test_directory(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    cfg = appsettings.get(db, "directory")
    token = get_secret(db, appsettings.DIRECTORY_TOKEN_SECRET) or ""
    if not api_base(cfg):
        raise FieldError("api_url", "Give the Authentik URL (no OIDC issuer is configured to take it from).")
    if not token:
        raise FieldError("token", "Save a read-only Authentik API token first.")
    try:
        groups = authentik_groups(cfg, token, "")
    except (httpx.HTTPError, ValueError) as exc:
        raise FieldError("api_url", f"Authentik at {api_base(cfg)} refused or failed: {_why(exc)}") from exc
    audit(db, p.username, "settings.directory.test", source_ip=client_ip(request))
    shown = ", ".join(groups[:5]) + (f" and {len(groups) - 5} more" if len(groups) > 5 else "")
    return {"ok": True, "message": f"Authentik answered with {len(groups)} group(s): {shown or 'none visible'}."}


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        hint = {401: "the token was rejected", 403: "the token is invalid or can't view groups"}.get(code, "")
        return f"HTTP {code}{': ' + hint if hint else ''}"
    return str(exc) or type(exc).__name__
