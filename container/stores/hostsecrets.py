#!/usr/bin/env python3
"""The stack's bootstrap secrets in the platform store (design §26, phase S4; question 104).

    hostsecrets.py [--store openbao|conjur] push DIR   every file in DIR -> the store
    hostsecrets.py [--store openbao|conjur] pull DIR   the store -> DIR (0700; files 0644, like container/secrets/)
    hostsecrets.py [--store openbao|conjur] same DIR   exit 0 when DIR matches the store exactly
    hostsecrets.py --store openbao|conjur drop         delete them from that store (after a move)
    hostsecrets.py active                              print the store that holds them now

The master key, the database password and connection strings, the CA
password and the OIDC client secret are what the stack needs before its
database (and the built-in store in it) is reachable, so they can't live in
the built-in store. Only the update agent's login reads them:
- OpenBao: labape-host/bootstrap/<name>, AppRole labape-agent
  (stores/secrets/agent_role_id, agent_secret_id);
- Conjur: variables labape/host/bootstrap/<name>, host labape/agent
  (stores/secrets/agent_conjur_api_key), which owns the labape/host branch.
Those logins are secret zero (question 105).

Without --store: the store recorded in stores/secrets/platform_store_active
(written by stores.sh once a move has finished), else LABAPE_PLATFORM_STORE
in container/.env, else openbao. So a changed setting takes effect only once
stores.sh has moved them, and a boot in between still reads the right store.
Standard library only; values never reach the terminal.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
C = HERE.parent
S = HERE / "secrets"
NAMES = ("postgres_password", "database_url", "tofu_pg_conn", "secret_key", "oidc_client_secret", "ca_password")
STORES = ("openbao", "conjur")


def die(msg: str) -> None:
    print(f"labape-stores: {msg}", file=sys.stderr)
    raise SystemExit(1)


def env(key: str, default: str) -> str:
    try:
        for line in (C / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip() or default
    except OSError:
        pass
    return default


def active() -> str:
    f = S / "platform_store_active"
    if f.is_file() and f.read_text().strip() in STORES:
        return f.read_text().strip()
    return env("LABAPE_PLATFORM_STORE", "openbao")


class Http:
    url = ""

    def __init__(self) -> None:
        self.ctx = ssl.create_default_context(cafile=str(HERE / "tls" / "root.crt"))

    def send(self, method: str, path: str, data: bytes | None, headers: dict, what: str):
        req = urllib.request.Request(self.url + path, method=method, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return 404, b""
            die(f"{type(self).__name__} answered HTTP {exc.code} ({what})")
        except (urllib.error.URLError, OSError) as exc:
            die(f"can't reach {type(self).__name__} at {self.url}: {exc}")
        return 0, b""


class OpenBao(Http):
    PREFIX = "/v1/labape-host/data/bootstrap/"

    def __init__(self) -> None:
        super().__init__()
        self.url = f"https://127.0.0.1:{env('LABAPE_OPENBAO_PORT', '8200')}"
        login = {"role_id": (S / "agent_role_id").read_text().strip(),
                 "secret_id": (S / "agent_secret_id").read_text().strip()}
        _, body = self.send("POST", "/v1/auth/approle/login", json.dumps(login).encode(),
                            {"Content-Type": "application/json"}, "the agent's login")
        self.h = {"X-Vault-Token": json.loads(body)["auth"]["client_token"], "Content-Type": "application/json"}

    def get(self, name: str) -> str | None:
        code, body = self.send("GET", self.PREFIX + name, None, self.h, f"reading {name}")
        return None if code == 404 else json.loads(body)["data"]["data"]["value"]

    def put(self, name: str, value: str) -> None:
        self.send("POST", self.PREFIX + name, json.dumps({"data": {"value": value}}).encode(), self.h, f"writing {name}")

    def drop(self, name: str) -> None:       # every version, not only the current one
        self.send("DELETE", self.PREFIX.replace("/data/", "/metadata/") + name, None, self.h, f"deleting {name}")


class Conjur(Http):
    BRANCH = "labape/host"

    def __init__(self) -> None:
        super().__init__()
        self.url = f"https://127.0.0.1:{env('LABAPE_CONJUR_PORT', '8444')}"
        key = (S / "agent_conjur_api_key").read_text().strip().encode()
        _, tok = self.send("POST", "/authn/labape/" + urllib.parse.quote("host/labape/agent", safe="") + "/authenticate",
                           key, {"Accept-Encoding": "base64"}, "the agent's login")
        self.h = {"Authorization": f'Token token="{tok.decode().strip()}"'}

    def _var(self, name: str) -> str:
        return "/secrets/labape/variable/" + urllib.parse.quote(f"{self.BRANCH}/bootstrap/{name}", safe="")

    def get(self, name: str) -> str | None:
        code, body = self.send("GET", self._var(name), None, self.h, f"reading {name}")
        return None if code == 404 else body.decode()

    def put(self, name: str, value: str) -> None:
        code, _ = self.send("POST", self._var(name), value.encode(), self.h, f"writing {name}")
        if code == 404:      # not declared yet: the agent owns its branch, so it declares it
            policy = f'- !variable\n  id: "bootstrap/{name}"\n'.encode()
            self.send("POST", "/policies/labape/policy/" + urllib.parse.quote(self.BRANCH, safe=""), policy,
                      {**self.h, "Content-Type": "application/x-yaml"}, f"declaring {name}")
            self.send("POST", self._var(name), value.encode(), self.h, f"writing {name}")

    def drop(self, name: str) -> None:
        if self.get(name) is not None:
            policy = f'- !delete\n  record: !variable "bootstrap/{name}"\n'.encode()
            self.send("PATCH", "/policies/labape/policy/" + urllib.parse.quote(self.BRANCH, safe=""), policy,
                      {**self.h, "Content-Type": "application/x-yaml"}, f"deleting {name}")


def main() -> None:
    args = sys.argv[1:]
    store = None
    if args[:1] == ["--store"] and len(args) > 1:
        store, args = args[1], args[2:]
    if args == ["active"]:
        print(active())
        return
    if args == ["drop"] and store in STORES:
        if store == active():
            die(f"{store} holds the bootstrap secrets now; not deleting them")
        s = OpenBao() if store == "openbao" else Conjur()
        for name in NAMES:
            s.drop(name)
        print(f"labape-stores: the old copy of the bootstrap secrets was deleted from {store}")
        return
    if len(args) != 2 or args[0] not in ("push", "pull", "same") or (store and store not in STORES):
        die("usage: hostsecrets.py [--store openbao|conjur] push|pull|same DIR  |  hostsecrets.py active")
    action, d = args[0], Path(args[1])
    store = store or active()
    s = OpenBao() if store == "openbao" else Conjur()
    if action == "push":
        for name in NAMES:
            f = d / name
            if not f.is_file():
                die(f"{f} is missing; nothing pushed after it")
            s.put(name, f.read_text(encoding="utf-8"))
        print(f"labape-stores: {len(NAMES)} bootstrap secrets written to {store}")
    elif action == "pull":
        values = {name: s.get(name) for name in NAMES}
        missing = [n for n, v in values.items() if v is None]
        if missing:
            die(f"not in {store}: {', '.join(missing)}")
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        for name, value in values.items():
            tmp = d / (name + ".tmp")
            tmp.write_text(value, encoding="utf-8")
            os.chmod(tmp, 0o644)            # containers read them as their own users (see setup.sh)
            os.replace(tmp, d / name)
        print(f"labape-stores: {len(NAMES)} bootstrap secrets from {store} written to {d}")
    else:
        diff = [n for n in NAMES if not (d / n).is_file() or s.get(n) != (d / n).read_text(encoding="utf-8")]
        if diff:
            print(f"labape-stores: differ from {store}: {', '.join(diff)}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
