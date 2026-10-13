"""The Syslog tab (design §29.1, question 137): messages the stack's collector
received, for admins and auditors."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .. import appsettings, syslogstack
from ..appsettings import FACILITY_NAMES, SEVERITY_NAMES
from ..db import get_db
from ..errors import FieldError
from ..security import Principal, require_roles

router = APIRouter(prefix="/api/syslog", tags=["syslog"])


@router.get("/messages")
def messages(since: dt.datetime | None = None, until: dt.datetime | None = None, host: str = "", from_ip: str = "",
             facility: str = "", min_severity: str = "", app: str = "", q: str = "", before_id: int | None = None,
             after_id: int | None = None, limit: int = 200, _: Principal = Depends(require_roles("auditor"))):
    """Newest first. `before_id` pages back; `after_id` gives only what's new (the live view)."""
    if not syslogstack.present():
        raise HTTPException(status_code=404, detail="This install has no syslog collector")
    try:
        return syslogstack.query(since=since, until=until, host=host.strip(), from_ip=from_ip.strip(),
                                 facility=facility, min_severity=min_severity, app=app.strip(), q=q.strip(),
                                 before_id=before_id, after_id=after_id, limit=limit)
    except ValueError as exc:
        raise FieldError("facility" if "facility" in str(exc) else "min_severity", str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"The syslog database isn't reachable: {type(exc).__name__}") from exc


@router.get("/status")
def collector_status(db: Session = Depends(get_db), _: Principal = Depends(require_roles("auditor"))):
    """Whether the collector is part of this install, the worker's last check, the table's size,
    where hosts send to, and the names the filters accept."""
    cfg = appsettings.get(db, "syslog_collector")
    out = {"present": syslogstack.present(), "worker": syslogstack.last_status(db), "address": syslogstack.collector_address(cfg),
           "retention_days": cfg.retention_days, "require_client_cert": cfg.require_client_cert,
           "facilities": list(FACILITY_NAMES), "severities": list(SEVERITY_NAMES), "table": None, "error": ""}
    if out["present"]:
        try:
            out["table"] = syslogstack.summary()
        except SQLAlchemyError as exc:
            out["error"] = f"The syslog database isn't reachable: {type(exc).__name__}"
    return out
