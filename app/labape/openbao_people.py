"""People's own access to their environments' secrets in OpenBao (V1b, design §49.2; question 163 b).

With stores.sh's OIDC sign-in set up (LABAPE_OPENBAO_PEOPLE=yes: auth mount `oidc`, the stack's
Authentik), people open OpenBao's UI or run `bao login -method=oidc` as themselves. For each
environment whose secrets are in OpenBao there is:

  policy  labape-env-<name>   read its folder (env/<name>/), write user/ and vm/ (what owners store)
  group   labape-env-<name>   internal, with that policy; members: the environment's owners
  policy  labape-env-admins   read every environment's folder; group labape-admins: LABaPe's admins

The policies and the groups' policies are written by `stores.sh people-policies` with the root token,
from a fixed template, when the update agent runs it: LABaPe only asks, naming the environments,
whenever the set it wants differs from the policies OpenBao has. LABaPe itself keeps the members:
  - a user grant: the user's identity (an entity with an OIDC alias by user name; the one OpenBao
    made at their first sign-in, or labape-user-<name>)
  - a group grant: an external group labape-group-<g>, its alias the Authentik group name (the
    token's groups claim)
It can't write or attach a policy, so at most it could add itself to a group reading an environment's
folder, which it reads anyway. A sync runs from the worker every few minutes; it also adopts secrets
people store directly in OpenBao (user/, vm/<vm>/) into LABaPe's catalog, so the page lists them.
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import appsettings, stores, update
from .config import get_settings
from .models import Environment, Grant, RoleBinding, Secret
from .security import env_store
from .stores import StoreError, layout

log = logging.getLogger(__name__)

PREFIX = "labape-env-"
ADMINS_POLICY, ADMINS_GROUP = "labape-env-admins", "labape-admins"
SYNC_SECONDS = 300
_last = 0.0


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")[:100] or "x"


def enabled(db: Session) -> bool:
    cfg = appsettings.get(db, "store_openbao", cached=True)
    return bool(cfg.enabled and cfg.people_url) and "openbao" in stores.configured(db)


class _Bao:
    """The identity calls, on LABaPe's own OpenBao login."""

    def __init__(self, store, mount: str):
        self.s = store
        auths = store.api("GET", "sys/auth")
        auths = auths.get("data") or auths
        if f"{mount}/" not in auths:
            raise StoreError(f"OpenBao has no {mount}/ sign-in yet (stores.sh with LABAPE_OPENBAO_PEOPLE=yes)")
        self.accessor = auths[f"{mount}/"]["accessor"]

    def user(self, username: str) -> str:
        """The entity a user signs in as (OpenBao's own, from an earlier sign-in, or one LABaPe makes)."""
        hit = self.s.api("POST", "identity/lookup/entity", {"alias_name": username, "alias_mount_accessor": self.accessor})
        if (hit.get("data") or {}).get("id"):
            return hit["data"]["id"]
        name = f"labape-user-{_slug(username)}"
        self.s.api("POST", f"identity/entity/name/{name}", {"metadata": {"labape_user": username}})
        eid = self.s.api("GET", f"identity/entity/name/{name}")["data"]["id"]
        self.s.api("POST", "identity/entity-alias", {"name": username, "canonical_id": eid, "mount_accessor": self.accessor})
        return eid

    def external(self, group: str) -> str:
        """An external group for an Authentik group (matched by the token's groups claim)."""
        name = f"labape-group-{_slug(group)}"
        self.s.api("POST", f"identity/group/name/{name}", {"type": "external", "metadata": {"labape_group": group}})
        gid = self.s.api("GET", f"identity/group/name/{name}")["data"]["id"]
        hit = self.s.api("POST", "identity/lookup/group", {"alias_name": group, "alias_mount_accessor": self.accessor})
        if not (hit.get("data") or {}).get("id"):
            self.s.api("POST", "identity/group-alias", {"name": group, "canonical_id": gid, "mount_accessor": self.accessor})
        return gid

    def members(self, name: str, entities: list[str], groups: list[str]) -> None:
        """The group's members; its policy is stores.sh's (an update leaves it as it is)."""
        self.s.api("POST", f"identity/group/name/{name}", {"type": "internal",
                                                          "member_entity_ids": sorted(set(entities)),
                                                          "member_group_ids": sorted(set(groups))})

    def policies(self) -> set[str]:
        """The environment policies OpenBao has (stores.sh's), each counted only when its group carries
        it (and the admins' when theirs does): anything less, and stores.sh has to run again."""
        keys = (self.s.api("LIST", "sys/policies/acl", missing_ok=True).get("data") or {}).get("keys") or []

        def carried(group: str, policy: str) -> bool:
            data = self.s.api("GET", f"identity/group/name/{group}", missing_ok=True).get("data") or {}
            return policy in keys and data.get("policies") == [policy]

        have = {k for k in keys if k.startswith(PREFIX) and k != ADMINS_POLICY and carried(k, k)}
        return have if carried(ADMINS_GROUP, ADMINS_POLICY) else have | {"(admins)"}


def _owners(db: Session, env_id: int) -> tuple[list[str], list[str]]:
    rows = db.scalars(select(Grant).where(Grant.object_type == "environment", Grant.object_id == env_id,
                                          Grant.level == "owner"))
    users, groups = [], []
    for g in rows:
        (users if g.principal_type == "user" else groups).append(g.principal)
    return users, groups


def _admins(db: Session) -> tuple[list[str], list[str]]:
    groups = [g for g, role in get_settings().role_map_dict.items() if role == "admin"]
    users = []
    for rb in db.scalars(select(RoleBinding).where(RoleBinding.role == "admin")):
        (users if rb.principal_type == "user" else groups).append(rb.principal)
    return users, groups


def _adopt(db: Session, store, env: Environment) -> list[str]:
    """Secrets people stored in the environment's folder directly in OpenBao (user/, vm/<vm>/): into the
    catalog, so the page lists them; catalog rows whose secret people deleted there: out."""
    found = set()
    for area in ("user", "vm"):
        for path in store.list(f"env/{env.name}/{area}"):
            rest = path[len(f"env/{env.name}/"):]
            name = (f"environment/{env.id}/{rest}" if area == "user"
                    else f"environment/{env.id}/{layout.USER_VM}{rest[len('vm/'):]}")
            found.add(name)
    done = []
    for name in sorted(found):
        if db.get(Secret, name) is None:
            db.add(Secret(name=name, value="", store="openbao"))
            done.append(f"adopted {name}")
    for row in db.scalars(select(Secret).where(Secret.store == "openbao",
                                               Secret.name.startswith(f"environment/{env.id}/"))):
        rest = row.name[len(f"environment/{env.id}/"):]
        if rest.startswith((layout.USER, layout.USER_VM)) and row.name not in found:
            db.delete(row)
            done.append(f"forgot {row.name} (deleted in OpenBao)")
    db.commit()
    return done


def _ask_agent(db: Session, names: list[str], have: set[str]) -> str:
    """Ask the update agent for stores.sh people-policies with exactly these environments."""
    if not update.agent_installed():
        return "the update agent isn't installed: run stores/stores.sh people-policies " + " ".join(names)
    if update.busy():
        return "the update agent is busy; asking again at the next sync"
    update.write_request("openbao_people", "", "system", envs=" ".join(names))
    want, have = {f"{PREFIX}{n}" for n in names}, have - {"(admins)"}
    return (f"asked the update agent for the policies (new: {', '.join(sorted(want - have)) or 'none'}; "
            f"removing: {', '.join(sorted(have - want)) or 'none'})")


def sync(db: Session) -> list[str]:
    """Bring OpenBao's people access in line with LABaPe's environments and owners; what changed."""
    if not enabled(db):
        return []
    store = stores.open_store(db, "openbao")
    bao = _Bao(store, appsettings.get(db, "store_openbao", cached=True).people_mount)
    out: list[str] = []
    entity_ids: dict[str, str] = {}
    group_ids: dict[str, str] = {}

    def members(users: list[str], groups: list[str]) -> tuple[list[str], list[str]]:
        for u in users:
            if u not in entity_ids:
                entity_ids[u] = bao.user(u)
        for g in groups:
            if g not in group_ids:
                group_ids[g] = bao.external(g)
        return [entity_ids[u] for u in users], [group_ids[g] for g in groups]

    names = []
    for env in db.scalars(select(Environment).where(Environment.status != "destroyed").order_by(Environment.id)):
        if env_store(db, env.id) != "openbao":
            continue
        names.append(env.name)
        bao.members(f"{PREFIX}{env.name}", *members(*_owners(db, env.id)))
        out += _adopt(db, store, env)
    bao.members(ADMINS_GROUP, *members(*_admins(db)))
    have = bao.policies()
    if have != {f"{PREFIX}{n}" for n in names}:
        out.append(_ask_agent(db, names, have))
    out.append(f"{len(names)} environment(s) in OpenBao: owners kept in step")
    from . import openbao_vms
    out += openbao_vms.sync(db)                     # V2: the VMs' own access
    return out


def schedule(db: Session) -> list[str]:
    """The worker's call: a sync every SYNC_SECONDS while people access is set up."""
    global _last
    if time.monotonic() - _last < SYNC_SECONDS or not enabled(db):
        return []
    _last = time.monotonic()
    try:
        return sync(db)
    except StoreError as exc:
        log.warning("OpenBao people access: %s", exc)
        return [f"failed: {exc}"]
