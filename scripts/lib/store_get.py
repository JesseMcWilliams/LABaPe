#!/usr/bin/env python3
"""The command line's engine secrets, read from the platform store (design §26, question 102 c).

    store_get.py vault-pass --vault FILE --out FILE [--try-file FILE]
    store_get.py ssh --out-dir DIR
    store_get.py check

Signs in to OpenBao with the command line's own AppRole (read-only on
platform/engine/), from ~/.config/labape/store.env (written by
container/stores/stores.sh, mode 0600):

    LABAPE_STORE_URL=https://127.0.0.1:8200
    LABAPE_STORE_CA=<the platform root certificate>
    LABAPE_STORE_ROLE_ID=...
    LABAPE_STORE_SECRET_ID=...

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
    def __init__(self, cfg: dict):
        self.url = cfg.get("LABAPE_STORE_URL", "").rstrip("/")
        self.ctx = ssl.create_default_context(cafile=cfg.get("LABAPE_STORE_CA") or None)
        body = {"role_id": cfg.get("LABAPE_STORE_ROLE_ID", ""), "secret_id": cfg.get("LABAPE_STORE_SECRET_ID", "")}
        self.token = self._call("POST", "/v1/auth/approle/login", body, "signing in")["auth"]["client_token"]

    def _call(self, method: str, path: str, body: dict | None, what: str) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if getattr(self, "token", None):
            headers["X-Vault-Token"] = self.token
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as exc:
            die(f"the secret store answered HTTP {exc.code} {what} ({self.url})")
        except (urllib.error.URLError, OSError) as exc:
            die(f"can't reach the secret store at {self.url} ({what}): {exc}")
        return {}

    def get(self, name: str) -> str:
        return self._call("GET", f"/v1/{MOUNT}/data/{BASE}{name}", None, f"reading {BASE}{name}")["data"]["data"]["value"]


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
