"""Add hosts to an existing environment (design §63, batch E2, question 187).

Either more VMs in an existing host group (`group` + `add`) or a new group that copies an existing one
(`like` + `name`). The stored spec changes, the same checks as creating an environment run on what is added
(names across the KVM host, capacity, quotas, templates, catalog, addresses), and a normal deploy is queued:
OpenTofu creates only the new VMs and Ansible's plays run for them as for any host.
"""
from __future__ import annotations

import copy

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import quotas
from ..db import get_db
from ..errors import FieldError
from ..naming import HOSTNAME_RE
from ..permissions import is_owner
from ..security import Principal, audit, client_ip, require_roles
from .environments import (HostGroup, _check_capacity, _check_catalog, _check_names, _check_templates, _get,
                           _group_hosts, _out, _queue)

router = APIRouter(prefix="/api/environments", tags=["environments"])

MAX_PER_GROUP = 20                                   # the same limit as a new environment's `count`


class AddHostsIn(BaseModel):
    group: str | None = Field(default=None, max_length=12)       # add to this group ...
    like: str | None = Field(default=None, max_length=12)        # ... or make a new group copying this one
    name: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9]{0,11}$")    # the new group's name
    add: int = Field(default=1, ge=1, le=MAX_PER_GROUP)
    names: list[str] = Field(default_factory=list, max_length=MAX_PER_GROUP)    # optional VM names, in order


def _padded(g: dict) -> list[str]:
    names = [n.strip() for n in (g.get("names") or [])]
    return (names + [""] * g.get("count", 1))[: g.get("count", 1)]


@router.post("/{env_id}/hosts")
def add_hosts(env_id: int, body: AddHostsIn, request: Request, db: Session = Depends(get_db),
              p: Principal = Depends(require_roles("deployer", "template_editor"))):
    e = _get(db, env_id, p)
    if not is_owner(db, p, "environment", e.id):
        raise HTTPException(status_code=403, detail="Only the environment's owners can add hosts to it")
    if e.status not in ("deployed", "failed"):
        raise HTTPException(status_code=409, detail="Hosts can be added to a deployed (or failed) environment only")
    if bool(body.group) == bool(body.like):
        raise FieldError("group", "Name the group to add to (group), or a group to copy (like) with a new name.")
    if len(body.names) > body.add:
        raise FieldError("names", f"{len(body.names)} names were given for {body.add} hosts.")
    for n in body.names:
        if n and not HOSTNAME_RE.match(n):
            raise FieldError("names", f"{n!r} isn't a host name: 1-15 letters, digits and hyphens, starting with a letter.")
    old_spec = copy.deepcopy(e.spec)
    groups = copy.deepcopy((e.spec or {}).get("host_groups", []))
    by_name = {g["name"].lower(): g for g in groups}
    new_names = (list(body.names) + [""] * body.add)[: body.add]
    if body.group:
        g = by_name.get(body.group.lower())
        if g is None:
            raise FieldError("group", f"This environment has no host group {body.group!r}.", status_code=404)
        if g.get("count", 1) + body.add > MAX_PER_GROUP:
            raise FieldError("add", f"A host group holds at most {MAX_PER_GROUP} hosts; {g['name']} has {g.get('count', 1)}.")
        delta = {**copy.deepcopy(g), "count": body.add, "names": []}          # what is added, for capacity and quotas
        g["names"] = _padded(g) + new_names
        g["count"] = g.get("count", 1) + body.add
        added_group = g["name"]
    else:
        src = by_name.get(body.like.lower())
        if src is None:
            raise FieldError("like", f"This environment has no host group {body.like!r} to copy.", status_code=404)
        if not body.name:
            raise FieldError("name", "Give the new group a name (1-12 letters and digits).")
        if body.name.lower() in by_name:
            raise FieldError("name", f"The group name {body.name} is already used (names are compared without case).")
        g = {**copy.deepcopy(src), "name": body.name, "count": body.add, "names": new_names}
        HostGroup.model_validate(g)                                        # the same rules as a new environment's group
        groups.append(g)
        delta = {**copy.deepcopy(g), "names": []}
        added_group = body.name

    new_spec = {**e.spec, "host_groups": groups}
    try:
        quotas.check(db, p, groups, exclude_env=e.id)                      # the whole environment, as it would be
    except quotas.QuotaError as exc:
        raise FieldError("add", str(exc), status_code=403) from exc
    for hid, gs in _group_hosts(db, e.kvm_host_id, [delta]).items():       # capacity: only what is added
        _check_capacity(db, hid, gs)
    _check_names(db, e.kvm_host_id, groups, env_id=e.id)                   # names across the host, and within this one
    _check_templates(db, e.kvm_host_id, [delta])
    _check_catalog(db, [delta])

    e.spec = new_spec
    db.commit()
    try:
        job = _queue(db, e, "environment.deploy", p)                       # allocates the new addresses, then deploys
    except HTTPException:
        e.spec = old_spec                                                  # no half change: put the spec back
        db.commit()
        raise
    audit(db, p.username, "environment.add_hosts", object_type="environment", object_id=e.id,
          detail={"group": added_group, "added": body.add, "names": new_names, "new_group": bool(body.like),
                  "job": job.id}, source_ip=client_ip(request))
    return {"environment": _out(db, e, True), "job_id": job.id}
