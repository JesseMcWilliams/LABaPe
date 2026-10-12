#!/usr/bin/env python3
"""The command line's engine secrets, read from the platform store (design §26, question 102 c).

    store_get.py vault-pass --vault FILE --out FILE [--try-file FILE]
    store_get.py ssh --out-dir DIR
    store_get.py check

Signs in to the platform store (question 104) with the command line's own
read-only login, from ~/.config/labape/store.env (written by
container/stores/stores.sh, mode 0600):

    LABAPE_STORE_KIND=openbao | conjur
    LABAPE_STORE_URL=https://127.0.0.1:8200
    LABAPE_STORE_CA=<the platform root certificate>
    LABAPE_STORE_ROLE_ID=..., LABAPE_STORE_SECRET_ID=...    (OpenBao: AppRole labape-cli)
    LABAPE_STORE_API_KEY=...                                (Conjur: host labape/cli)

vault-pass writes the current vault password to --out (0600). If --vault
(the command line's own secrets.vault.yml) doesn't open with it, the
worker has rotated the password since: it tries the previous passwords
(and --try-file) and re-encrypts the vault in place with the current one.
ssh writes the VM key and its previous keys (history/) for SSH and Ansible.
Standard library only; values never reach the terminal.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("LABAPE_STORE_ENV", Path.home() / ".config" / "labape" / "store.env"))
MOUNT = "labape"
BASE = "platform/engine/"


def die(msg: str) -> None:
    print(f"labape: {msg}", file=sys.stderr)
    raise SystemExit(1)


def config() -> dict:
    if not CONFIG.is_file():
        die(f"{CONFIG} is missing (container/stores/stores.sh writes it)")
    out = {}
    for line in CONFIG.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class Store:
    """OpenBao (KV v2 under labape/) or Conjur (variables under labape/secrets/), read-only."""

    def __init__(self, cfg: dict):
        self.kind = cfg.get("LABAPE_STORE_KIND", "openbao")
        self.url = cfg.get("LABAPE_STORE_URL", "").rstrip("/")
        self.ctx = ssl.create_default_context(cafile=cfg.get("LABAPE_STORE_CA") or None)
        self.headers: dict[str, str] = {}
        if self.kind == "conjur":
            login = urllib.parse.quote("host/labape/cli", safe="")
            tok = self._call("POST", f"/authn/labape/{login}/authenticate", cfg.get("LABAPE_STORE_API_KEY", "").encode(),
                             "signing in", {"Accept-Encoding": "base64"})
            self.headers = {"Authorization": f'Token token="{tok.decode().strip()}"'}
        else:
            body = json.dumps({"role_id": cfg.get("LABAPE_STORE_ROLE_ID", ""),
                               "secret_id": cfg.get("LABAPE_STORE_SECRET_ID", "")}).encode()
            out = json.loads(self._call("POST", "/v1/auth/approle/login", body, "signing in",
                                        {"Content-Type": "application/json"}))
            self.headers = {"X-Vault-Token": out["auth"]["client_token"]}

    def _call(self, method: str, path: str, data: bytes | None, what: str, headers: dict | None = None) -> bytes:
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={**self.headers, **(headers or {})})
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            die(f"the secret store answered HTTP {exc.code} {what} ({self.url})")
        except (urllib.error.URLError, OSError) as exc:
            die(f"can't reach the secret store at {self.url} ({what}): {exc}")
        return b""

    def get(self, name: str) -> str:
        what = f"reading {BASE}{name}"
        if self.kind == "conjur":
            var = urllib.parse.quote(f"labape/secrets/{BASE}{name}", safe="")
            return self._call("GET", f"/secrets/labape/variable/{var}", None, what).decode()
        return json.loads(self._call("GET", f"/v1/{MOUNT}/data/{BASE}{name}", None, what))["data"]["data"]["value"]


def write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w", encoding="utf-8") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def ansible_vault() -> str:
    """ansible-vault on PATH, or where a pip --user install puts it (not on a non-interactive PATH)."""
    import shutil
    for cand in (shutil.which("ansible-vault"), str(Path.home() / ".local" / "bin" / "ansible-vault"),
                 "/usr/local/bin/ansible-vault"):
        if cand and Path(cand).is_file():
            return cand
    die("ansible-vault isn't installed (needed to check the command line's vault)")
    return ""


def opens(vault: Path, pw_file: Path) -> bool:
    return subprocess.run([ansible_vault(), "view", "--vault-password-file", str(pw_file), str(vault)],
                          capture_output=True, timeout=60, check=False).returncode == 0


def cmd_vault_pass(store: Store, a) -> None:
    current = store.get("vault_password")
    out = Path(a.out)
    write(out, current, 0o600)
    vault = Path(a.vault)
    if not vault.is_file() or opens(vault, out):
        return
    candidates = json.loads(store.get("vault_password_history") or "[]")
    if a.try_file and Path(a.try_file).is_file():
        candidates.append(Path(a.try_file).read_text(encoding="utf-8").strip())
    with tempfile.TemporaryDirectory() as d:
        old = Path(d) / "old"
        for pw in candidates:
            write(old, pw, 0o600)
            if opens(vault, old):
                r = subprocess.run([ansible_vault(), "rekey", "--vault-password-file", str(old),
                                    "--new-vault-password-file", str(out), str(vault)], capture_output=True, timeout=120)
                if r.returncode != 0 or not opens(vault, out):
                    die(f"re-encrypting {vault} with the current password failed")
                print(f"labape: {vault} re-encrypted with the current vault password", file=sys.stderr)
                return
    die(f"{vault} opens with neither the current vault password nor a previous one")


def cmd_ssh(store: Store, a) -> None:
    d = Path(a.out_dir)
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    write(d / "labape_bootstrap", store.get("ssh_key"), 0o600)
    write(d / "labape_bootstrap.pub", store.get("ssh_key_pub"), 0o644)
    hist = d / "history"
    hist.mkdir(exist_ok=True)
    os.chmod(hist, 0o700)
    keep = set()
    for h in json.loads(store.get("ssh_history") or "[]"):
        name = f"labape_bootstrap-{h['stamp']}"
        write(hist / name, h["private"], 0o600)
        write(hist / (name + ".pub"), h["public"], 0o644)
        keep |= {name, name + ".pub"}
    for f in hist.iterdir():
        if f.name not in keep:
            f.unlink()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("vault-pass")
    p.add_argument("--vault", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--try-file", default="")
    p = sub.add_parser("ssh")
    p.add_argument("--out-dir", required=True)
    sub.add_parser("check")
    a = ap.parse_args()
    store = Store(config())
    if a.cmd == "vault-pass":
        cmd_vault_pass(store, a)
    elif a.cmd == "ssh":
        cmd_ssh(store, a)
    else:
        store.get("vault_password")
        print(f"labape: the command line's store login works ({store.url})")


if __name__ == "__main__":
    main()
