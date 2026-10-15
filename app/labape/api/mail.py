"""Admin -> Mail: the stack's own mail server, Mailpit (design §52).

The relay settings themselves are the `stack_mail` settings section (PUT /api/admin/settings/stack_mail, which
also writes Mailpit's options); this router has what the page needs besides: Mailpit's status, a test message,
pointing Settings -> Email at it, and the check Caddy makes before showing Mailpit's web page (admins only).
"""
from __future__ import annotations

import smtplib

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import appsettings, notify, stack_mail
from ..db import get_db
from ..errors import FieldError
from ..security import Principal, audit, client_ip, get_secret, require_roles

router = APIRouter(tags=["mail"])


@router.get("/api/mail/auth")
def mail_auth(_: Principal = Depends(require_roles("admin"))):
    """Caddy's forward_auth for /mail/*: 200 for a signed-in admin, the usual 401/403 otherwise."""
    return {"ok": True}


@router.get("/api/admin/mail")
def mail_status(db: Session = Depends(get_db), _: Principal = Depends(require_roles("admin"))):
    return stack_mail.status(db)


class TestIn(BaseModel):
    to: str = Field(pattern=r"^[^@\s]+@[^@\s]+$")


@router.post("/api/admin/mail/test")
def mail_test(body: TestIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("admin"))):
    """Send a test email the way LABaPe sends its own (Settings -> Email); through Mailpit it lands there and
    leaves only when the relay is on and the address is one it forwards."""
    cfg = appsettings.get(db, "smtp")
    if not cfg.enabled or not cfg.host:
        raise FieldError("to", "Settings -> Email is off; use this server for LABaPe's email first.")
    name = appsettings.get(db, "branding", cached=True).app_name
    try:
        notify.send_email(cfg, get_secret(db, appsettings.SMTP_PASSWORD_SECRET) or "", [body.to],
                          f"{name} test email", f"This is a test email from {name}, sent by {p.username}.")
    except (OSError, smtplib.SMTPException) as exc:
        raise FieldError("to", f"Sending through {cfg.host}:{cfg.port} failed: {exc}") from exc
    relay = appsettings.get(db, "stack_mail")
    audit(db, p.username, "mail.test", detail={"to": body.to}, source_ip=client_ip(request))
    forwarded = stack_mail.uses_mailpit(cfg) and relay.relay
    return {"ok": True, "message": f"Test email sent to {body.to} through {cfg.host}:{cfg.port}."
            + (" It is in Mailpit; the relay forwards it if the address is one it forwards." if forwarded
               else " It is in Mailpit." if stack_mail.uses_mailpit(cfg) else "")}


@router.post("/api/admin/mail/use-for-email")
def use_for_email(request: Request, db: Session = Depends(get_db), p: Principal = Depends(require_roles("admin"))):
    """Point Settings -> Email at the stack's Mailpit."""
    if not stack_mail.available():
        raise FieldError("host", "This install has no Mailpit.")
    model = stack_mail.email_defaults(appsettings.get(db, "smtp"))
    appsettings.put(db, "smtp", model)
    audit(db, p.username, "mail.use_for_email", detail={"host": model.host, "port": model.port}, source_ip=client_ip(request))
    return stack_mail.status(db)
