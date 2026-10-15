"""Where an environment's secrets live in the store (V1, question 164; design §49).

LABaPe's own names stay `environment/<id>/...`; in OpenBao they sit in a folder per environment, by
name, so people (OpenBao's UI and `bao`, V1b) and VMs (V2) find them and policies can grant them:

  environment/<id>/user/<name>              env/<environment>/user/<name>       what owners store
  environment/<id>/uservm/<vm>/<name>       env/<environment>/vm/<vm>/<name>    one VM's own (owners store)
  environment/<id>/<anything else>          env/<environment>/accounts/...      what LABaPe generates

Only OpenBao gets the layout (question 166: people's and VMs' access is OpenBao only); other stores
keep LABaPe's names. An environment's name is unique and its row stays after a destroy, so the folder
never changes hands. Secrets written before V1 sit under their old name until read or written again
(stores.ext_get / ext_put move them).
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

LAYOUT_STORES = frozenset({"openbao"})
_ENV = re.compile(r"^environment/(\d+)/(.+)$")
USER, USER_VM = "user/", "uservm/"


def user_name(env_id: int, name: str, vm: str = "") -> str:
    """LABaPe's name for a secret an owner stores: shared (`user/`) or one VM's own (`uservm/<vm>/`)."""
    return f"environment/{env_id}/{USER_VM}{vm}/{name}" if vm else f"environment/{env_id}/{USER}{name}"


def env_path(env_name: str, rest: str) -> str:
    """The store path of `environment/<id>/<rest>` for the environment called `env_name`."""
    if rest.startswith(USER):
        return f"env/{env_name}/{rest}"
    if rest.startswith(USER_VM):
        vm, _, tail = rest[len(USER_VM):].partition("/")
        return f"env/{env_name}/vm/{vm}/{tail}"
    return f"env/{env_name}/accounts/{rest}"


def store_name(db: Session, store: str, name: str) -> str:
    """The name `store` keeps LABaPe's secret `name` under."""
    if store not in LAYOUT_STORES:
        return name
    m = _ENV.match(name)
    if not m:
        return name
    from ..models import Environment
    env = db.get(Environment, int(m.group(1)))
    return env_path(env.name, m.group(2)) if env is not None else name
