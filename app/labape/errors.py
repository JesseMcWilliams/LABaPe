"""User-facing errors: field-specific messages that state the rule, and a
log of every refused request (design §21, question 59).

Responses for refused requests look like:

    {"detail": "2 fields need fixing.",
     "errors": [{"field": "host_groups.0.name", "label": "Host group 1: Group name",
                 "message": "Use 1-12 lower-case letters and digits, starting with a letter."}]}

The web UI puts each message under its field and the summary at the top.
"""
from __future__ import annotations

import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("labape.errors")

# The rule for each field, by its last path element (or "parent.field").
RULES = {
    "environment.name": "Use 3-40 lower-case letters, digits and hyphens, starting with a letter. "
                        "Names starting with test- are reserved for the CLI.",
    "host_groups.name": "Use 1-12 lower-case letters and digits, starting with a letter.",
    "host_groups.names": "Use 1-15 letters, digits and hyphens, starting with a letter "
                         "(Windows limits computer names to 15 characters).",
    "count": "Use a whole number from 1 to 20.",
    "cpu_count": "Use a whole number from 1 to 32.",
    "memory_mb": "Use a whole number of MB from 512 to 262144.",
    "disk_gb": "Use a whole number of GB from 10 to 2048.",
    "os": "Choose an OS from the list.",
    "template": "Choose a template from the list.",
    "network.name": "Use 1-63 lower-case letters, digits and hyphens, starting with a letter.",
    "cidr": "Use an IPv4 network such as 172.21.48.0/22.",
    "gateway": "Use an IPv4 address inside the network, such as 172.21.48.1.",
    "dns_servers": "Use IPv4 addresses, separated by commas.",
    "static_pools": "Use address ranges such as 172.21.50.1-172.21.51.254, inside the network.",
    "dhcp_ranges": "Use address ranges such as 172.21.48.16-172.21.49.254, inside the network.",
    "reserved": "Use addresses or address ranges inside the network.",
    "vlan": "Use a number from 1 to 4094, or leave it empty. It's a reference label only.",
    "bridge": "Use 1-15 letters, digits, dots, underscores or hyphens (a Linux interface name).",
    "addressing": "Choose static, DHCP or both.",
    "host.name": "Use 1-63 lower-case letters, digits and hyphens, starting with a letter or digit.",
    "concurrency_limit": "Use a whole number from 1 to 16.",
    "static_ip_offset_start": "Use a whole number from 2 to 4000.",
}

LABELS = {
    "name": "Name", "host_groups": "Host group", "names": "Host name", "count": "Count", "os": "OS",
    "roles": "Roles", "image_source": "Image source", "template": "Template", "cpu_count": "vCPUs",
    "memory_mb": "Memory (MB)", "disk_gb": "Disk (GB)", "network": "Network", "addressing": "Addressing",
    "cidr": "CIDR", "gateway": "Gateway", "dns_servers": "DNS servers", "static_pools": "Static pools",
    "dhcp_ranges": "DHCP scope", "reserved": "Reserved", "vlan": "VLAN", "bridge": "Bridge",
    "allowed_roles": "Limit to roles", "allowed_groups": "Limit to groups", "kvm_host_id": "KVM host",
    "libvirt_uri": "libvirt URI", "vm_storage_path": "VM storage path",
    "template_storage_path": "Template storage path", "concurrency_limit": "Concurrent jobs",
}


class FieldError(HTTPException):
    """A refusal tied to one field, with the rule in the message."""

    def __init__(self, field: str, message: str, status_code: int = 422, label: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.field = field
        self.label = label or label_for(field.split("."))


def label_for(path: list) -> str:
    parts = []
    for i, p in enumerate(path):
        if isinstance(p, int) or (isinstance(p, str) and p.isdigit()):
            # "host_groups 0" -> "Host group 1"; "names 2" -> "Host name 3"
            if parts:
                parts[-1] = f"{parts[-1]} {int(p) + 1}"
            continue
        label = LABELS.get(p, p.replace("_", " ").capitalize())
        if p == "name" and "host_groups" in path:
            label = "Group name"
        parts.append(label)
    return ": ".join(parts)


def _rule(path: list, scope: str) -> str | None:
    names = [p for p in path if not (isinstance(p, int) or (isinstance(p, str) and p.isdigit()))]
    if not names:
        return None
    leaf = names[-1]
    parent = names[-2] if len(names) > 1 else scope
    return RULES.get(f"{parent}.{leaf}") or RULES.get(leaf)


def _scope(path: str) -> str:
    if "/environments" in path:
        return "environment"
    if "/networks" in path:
        return "network"
    if "/hosts" in path:
        return "host"
    return ""


def validation_errors(exc: RequestValidationError, request_path: str) -> list[dict]:
    out = []
    scope = _scope(request_path)
    for err in exc.errors():
        loc = [p for p in err.get("loc", ()) if p != "body"]
        rule = _rule(loc, scope)
        msg = err.get("msg", "Invalid value").removeprefix("Value error, ")
        if err.get("type") in ("string_pattern_mismatch", "string_too_long", "string_too_short",
                               "int_parsing", "greater_than_equal", "less_than_equal", "missing") and rule:
            msg = ("This field is required. " + rule) if err.get("type") == "missing" else rule
        elif rule and rule not in msg:
            msg = f"{msg}. {rule}"
        out.append({"field": ".".join(str(p) for p in loc), "label": label_for(loc) or "Request", "message": msg})
    return out


def _username(request: Request) -> str:
    try:
        uid = request.session.get("uid")
    except AssertionError:
        return ""
    if not uid:
        return ""
    from .db import session_factory
    from .models import User
    with session_factory()() as db:
        user = db.get(User, uid)
        return user.username if user else str(uid)


def record(request: Request, status: int, message: str, fields: list[dict], origin: str = "server") -> None:
    """Keep the refusal for the admin Errors page; never let logging break the response."""
    try:
        from .db import session_factory
        from .models import UserError
        with session_factory()() as db:
            db.add(UserError(username=_username(request), method=request.method, path=request.url.path[:255],
                             status=status, message=message[:4000], fields=fields, origin=origin))
            db.commit()
    except Exception:  # noqa: BLE001
        log.exception("could not record a user error")


# Statuses worth reviewing: bad input, policy refusals, permission refusals.
LOGGED = {400, 403, 409, 422}


async def on_validation_error(request: Request, exc: RequestValidationError):
    errors = validation_errors(exc, request.url.path)
    summary = (f"{errors[0]['label']}: {errors[0]['message']}" if len(errors) == 1
               else f"{len(errors)} fields need fixing.")
    record(request, 422, summary, errors)
    return JSONResponse({"detail": summary, "errors": errors}, status_code=422)


async def on_http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    errors = []
    if isinstance(exc, FieldError):
        errors = [{"field": exc.field, "label": exc.label, "message": detail}]
        detail = f"{exc.label}: {detail}"
    if exc.status_code in LOGGED and request.url.path.startswith("/api/"):
        record(request, exc.status_code, detail, errors)
    return JSONResponse({"detail": detail, "errors": errors}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))
