#!/usr/bin/env python3
"""The stack's bootstrap secrets in OpenBao (design §26, phase S4).

    hostsecrets.py push DIR        every file in DIR -> labape-host/bootstrap/<file name>
    hostsecrets.py pull DIR        the store -> DIR (directory 0700, files 0644, like container/secrets/)
    hostsecrets.py same DIR        exit 0 when DIR matches the store exactly

The master key, the database password and connection strings, the CA
password and the OIDC client secret are what the stack needs before its
database (and the built-in store in it) is reachable, so they can't live in
the built-in store. They live under the labape-host/ mount, which only the
update agent's AppRole can read (stores/secrets/agent_role_id,
agent_secret_id: secret zero, question 105). Standard library only; values
never reach the terminal.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
C = HERE.parent
NAMES = ("postgres_password", "database_url", "tofu_pg_conn", "secret_key", "oidc_client_secret", "ca_password")
PREFIX = "labape-host/data/bootstrap/"


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


class Bao:
    def __init__(self) -> None:
        self.url = f"https://127.0.0.1:{env('LABAPE_OPENBAO_PORT', '8200')}"
        self.ctx = ssl.create_default_context(cafile=str(HERE / "tls" / "root.crt"))
        self.token = ""
        s = HERE / "secrets"
        login = {"role_id": (s / "agent_role_id").read_text().strip(),
                 "secret_id": (s / "agent_secret_id").read_text().strip()}
        self.token = self.call("POST", "auth/approle/login", login)["auth"]["client_token"]

    def call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.url}/v1/{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json",
                                              **({"X-Vault-Token": self.token} if self.token else {})})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return {}
            die(f"OpenBao answered HTTP {exc.code} ({method} {path.split('/')[-1]})")
        except (urllib.error.URLError, OSError) as exc:
            die(f"can't reach OpenBao at {self.url}: {exc}")
        return {}

    def get(self, name: str) -> str | None:
        d = self.call("GET", PREFIX + name)
        return d.get("data", {}).get("data", {}).get("value") if d else None

    def put(self, name: str, value: str) -> None:
        self.call("POST", PREFIX + name, {"data": {"value": value}})


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("push", "pull", "same"):
        die("usage: hostsecrets.py push|pull|same DIR")
    action, d = sys.argv[1], Path(sys.argv[2])
    bao = Bao()
    if action == "push":
        for name in NAMES:
            f = d / name
            if not f.is_file():
                die(f"{f} is missing; nothing pushed after it")
            bao.put(name, f.read_text(encoding="utf-8"))
        print(f"labape-stores: {len(NAMES)} bootstrap secrets written to OpenBao")
    elif action == "pull":
        values = {name: bao.get(name) for name in NAMES}
        missing = [n for n, v in values.items() if v is None]
        if missing:
            die(f"not in OpenBao: {', '.join(missing)}")
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        for name, value in values.items():
            tmp = d / (name + ".tmp")
            tmp.write_text(value, encoding="utf-8")
            os.chmod(tmp, 0o644)            # containers read them as their own users (see setup.sh)
            os.replace(tmp, d / name)
        print(f"labape-stores: {len(NAMES)} bootstrap secrets written to {d}")
    else:
        diff = [n for n in NAMES if not (d / n).is_file() or bao.get(n) != (d / n).read_text(encoding="utf-8")]
        if diff:
            print(f"labape-stores: differ from OpenBao: {', '.join(diff)}", file=sys.stderr)
            raise SystemExit(1)


if __name__ == "__main__":
    main()
