"""`labape` command, run inside the container.

    labape serve                    API + web UI (uvicorn)
    labape worker                   job runner
    labape init-db                  create tables
    labape breakglass enable [--minutes N] [--local-only]
    labape breakglass revoke
    labape bootstrap FILE.json      hosts, networks, attachments, role bindings (idempotent)
    labape auth status
    labape auth disable-provider NAME | enable-provider NAME

Break-glass (Claude_Docs/Planning_Web-Interface-Options.md decision 25):
being able to run this command — `docker compose exec labape-api labape
breakglass enable` — is the proof of identity. It prints a one-time
credential; nothing is stored in plain text.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import socket
import sys

from .config import get_settings


def _who() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no passwd entry for the container uid
        user = str(os.getuid())
    return f"{user}@{socket.gethostname()}"


def cmd_serve(args) -> int:
    import uvicorn

    from .db import init_db

    init_db()
    uvicorn.run("labape.main:app_factory", factory=True, host=args.host, port=args.port,
                proxy_headers=True, forwarded_allow_ips="*", log_level="info")
    return 0


def cmd_worker(args) -> int:
    from .db import init_db
    from .engine import runner

    init_db()
    runner.serve(args.slots)
    return 0


def cmd_init_db(_args) -> int:
    from .db import init_db

    init_db()
    print("labape: database tables are in place.")
    return 0


def cmd_breakglass(args) -> int:
    from .auth import breakglass
    from .db import init_db, session_factory
    from .security import audit

    init_db()
    s = get_settings()
    with session_factory()() as db:
        if args.action == "revoke":
            n = breakglass.revoke_all(db)
            audit(db, _who(), "breakglass.revoke", detail={"revoked": n})
            print(f"labape: revoked {n} unused break-glass credential(s).")
            return 0
        token, expires = breakglass.issue(db, _who(), args.minutes, args.local_only)
        audit(db, _who(), "breakglass.enable", detail={"minutes": args.minutes or s.breakglass_minutes,
                                                       "local_only": args.local_only})
    url = "https://localhost:8443/breakglass" if args.local_only else s.public_url.rstrip("/") + "/breakglass"
    print("Break-glass sign-in enabled.")
    print(f"  Open:       {url}")
    print(f"  Credential: {token}")
    print(f"  Valid until {expires:%Y-%m-%d %H:%M:%S} UTC, for one sign-in; the session lasts "
          f"{s.breakglass_session_minutes} minutes.")
    if args.local_only:
        print("  Host-only: works from the container host itself (or through an SSH tunnel to it).")
    return 0


def cmd_auth(args) -> int:
    from .auth import load_providers, provider_enabled, set_provider_enabled
    from .db import init_db, session_factory
    from .security import audit

    init_db()
    with session_factory()() as db:
        providers = load_providers()
        if args.action == "status":
            for name, p in providers.items():
                ok, msg = asyncio.run(p.test_connection())
                state = "enabled" if provider_enabled(db, name) else "disabled"
                print(f"{name:12} {p.kind:9} {state:9} {'healthy' if ok else 'UNHEALTHY':10} {msg}")
            return 0
        if args.name not in providers:
            print(f"labape: unknown or unconfigured provider {args.name!r}", file=sys.stderr)
            return 1
        enabled = args.action == "enable-provider"
        set_provider_enabled(db, args.name, enabled)
        audit(db, _who(), f"auth.provider.{'enable' if enabled else 'disable'}", detail={"provider": args.name})
        print(f"labape: provider {args.name} {'enabled' if enabled else 'disabled'}.")
    return 0


def cmd_bootstrap(args) -> int:
    from .bootstrap import apply_file
    from .db import init_db, session_factory
    from .security import audit

    init_db()
    with session_factory()() as db:
        changes = apply_file(db, args.file)
        if changes:
            audit(db, _who(), "bootstrap.apply", detail={"file": args.file, "changes": changes})
    for c in changes:
        print(f"labape: {c}")
    print(f"labape: bootstrap applied ({len(changes)} change(s)).")
    return 0


def cmd_maintenance(args) -> int:
    """Maintenance mode for the update agent: on, off, status (JSON)."""
    import json as _json
    from . import maintenance_mode
    from .api.maintenance import status
    from .db import init_db, session_factory
    from .security import audit
    init_db()
    with session_factory()() as db:
        if args.action == "on":
            maintenance_mode.turn_on(db, args.actor, args.reason or "maintenance", args.source)
            audit(db, args.actor, "maintenance.on", detail={"reason": args.reason, "source": args.source})
        elif args.action == "off":
            maintenance_mode.turn_off(db)
            audit(db, args.actor, "maintenance.off", detail={"source": args.source})
        print(_json.dumps(status(db), default=str))
    return 0


def cmd_backup(args) -> int:
    """A database dump now (the update agent's pre-update backup); prints its path."""
    import sys
    from .db import session_factory
    from .engine.maintenance import dump_database
    with session_factory()() as db:
        rc, path = dump_database(db, sys.stderr, args.label)
    if rc == 0:
        print(path)
    return rc


def cmd_restore(args) -> int:
    """Restore a dump over the database (the API and worker must be stopped)."""
    import sys
    from pathlib import Path as _P
    from .engine.maintenance import restore_database
    if not args.yes:
        print("labape: restore replaces the whole database; pass --yes", file=sys.stderr)
        return 2
    return restore_database(_P(args.file), sys.stderr)


def cmd_health(args) -> int:
    """Exit 0 when the stack is healthy: database and layout, worker heartbeat, CA (design §23.2)."""
    import datetime as _dt
    import json as _json
    from . import ca
    from .db import engine, session_factory
    from .migrate import current, head
    from .models import Setting
    checks: dict = {}
    try:
        with engine().connect() as conn:
            rev = current(conn)
        checks["database"] = {"ok": rev == head(), "revision": rev, "expected": head()}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": str(exc)[:300]}
    if args.deep:
        try:
            with session_factory()() as db:
                row = db.get(Setting, "status.worker")
            at = _dt.datetime.fromisoformat(row.value["at"]) if row else None
            age = (_dt.datetime.now(_dt.timezone.utc) - at).total_seconds() if at else None
            checks["worker"] = {"ok": age is not None and age < 180, "seconds_since_heartbeat": age}
        except Exception as exc:  # noqa: BLE001
            checks["worker"] = {"ok": False, "error": str(exc)[:300]}
        ok, msg = ca.health()
        checks["ca"] = {"ok": ok is not False, "message": msg}
        if ok and ca.configured():          # the password works: the CA really signs
            try:
                from cryptography import x509 as _x509
                from cryptography.hazmat.primitives import hashes as _h, serialization as _s
                from cryptography.hazmat.primitives.asymmetric import ec as _ec
                from cryptography.x509.oid import NameOID as _N
                csr = _x509.CertificateSigningRequestBuilder().subject_name(_x509.Name(
                    [_x509.NameAttribute(_N.COMMON_NAME, "labape-health-probe")])).sign(
                    _ec.generate_private_key(_ec.SECP256R1()), _h.SHA256()).public_bytes(_s.Encoding.PEM).decode()
                ca.sign(csr, hours=1)
                checks["ca"]["signs"] = True
            except Exception as exc:  # noqa: BLE001
                checks["ca"] = {"ok": False, "message": f"signing failed: {exc}"[:300]}
    healthy = all(c["ok"] for c in checks.values())
    print(_json.dumps({"healthy": healthy, "checks": checks}, default=str))
    return 0 if healthy else 1


def cmd_rewrap_master(args) -> int:
    """Re-wrap the store's data keys from one master key file to another (the agent's rotation)."""
    from pathlib import Path as _P
    from .db import session_factory
    from .security import rewrap_master
    old = _P(args.from_file).read_text(encoding="utf-8").strip()
    new = _P(args.to_file).read_text(encoding="utf-8").strip()
    with session_factory()() as db:
        n = rewrap_master(db, old, new)
    print(f"labape: re-wrapped {n} data key(s)")
    return 0


def cmd_ca_repass(args) -> int:
    """Re-encrypt the CA's signing provisioner key with a new password (the agent's rotation)."""
    from pathlib import Path as _P
    from .ca import repass_provisioner
    repass_provisioner(args.config, _P(args.old).read_bytes().strip(), _P(args.new).read_bytes().strip())
    print("labape: provisioner key re-encrypted")
    return 0


def cmd_ca_sign(args) -> int:
    """Sign a CSR from stdin with the platform CA; print the chain (and the root
    with --with-root). For hosts outside the VM flow, such as a KVM host's own
    services: whoever can run this inside the container controls the stack."""
    from .ca import CaError, csr_names, root_pem, sign
    from .db import init_db, session_factory
    from .security import audit

    if not 1 <= args.hours <= 2160:
        print("labape: --hours must be 1-2160 (step-ca allows up to 90 days)", file=sys.stderr)
        return 2
    csr = sys.stdin.read()
    try:
        cn, dns, ips = csr_names(csr)
        chain = sign(csr, hours=args.hours)
    except CaError as exc:
        print(f"labape: {exc}", file=sys.stderr)
        return 1
    init_db()
    with session_factory()() as db:
        audit(db, _who(), "ca.sign", object_type="certificate", object_id=cn,
              detail={"dns": sorted(dns), "ips": sorted(ips), "hours": args.hours, "via": "cli"})
    sys.stdout.write(chain if chain.endswith("\n") else chain + "\n")
    if args.with_root:
        sys.stdout.write(root_pem())
    return 0


def cmd_push_vm_key(args) -> int:
    """Add a new VM key on the deployed environments' Linux VMs (the agent's rotation, design §24 R2)."""
    import json as _json
    import os as _os
    import shutil as _shutil
    import sys as _sys
    import tempfile as _tempfile
    from pathlib import Path as _P
    from .config import get_settings
    from .db import session_factory
    from .engine.vmaccess import AccessError, push_vm_key
    ssh = get_settings().secrets_dir / "ssh"
    with _tempfile.TemporaryDirectory() as d:
        # OpenSSH only uses private keys this process owns with 0600; take copies.
        def copy(src: _P, dst: _P) -> _P:
            _shutil.copyfile(src, dst)
            _os.chmod(dst, 0o600)
            return dst
        new = copy(_P(args.new), _P(d) / "new")
        copy(_P(args.new + ".pub"), _P(d) / "new.pub")
        current = copy(ssh / "labape_bootstrap", _P(d) / "current")
        hist = _P(d) / "history"
        hist.mkdir()
        for f in sorted((ssh / "history").glob("*")) if (ssh / "history").is_dir() else []:
            copy(f, hist / f.name)
        try:
            with session_factory()() as db:
                report = push_vm_key(db, new, current, hist, _sys.stdout)
        except AccessError as exc:
            print(f"labape: {exc}", file=_sys.stderr)
            return 1
    print(_json.dumps(report))
    return 0


def cmd_db_status(_args) -> int:
    """The database's migration revision and this code's newest one."""
    from .db import engine
    from .migrate import current, head
    with engine().connect() as conn:
        print(f"database: {current(conn) or '(not stamped)'}")
        print(f"code:     {head()}")
    return 0


def cmd_worker_key(_args) -> int:
    """Print the worker's SSH public key for remote KVM hosts (design §22.4)."""
    from .engine.hostio import ensure_key
    print(ensure_key())
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="labape")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("serve", help="run the API and web UI")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("worker", help="run the job runner")
    p.add_argument("--slots", type=int, default=None)
    p.set_defaults(func=cmd_worker)

    sub.add_parser("init-db", help="create or migrate the database schema").set_defaults(func=cmd_init_db)
    sub.add_parser("db-status", help="the database's migration revision and this code's").set_defaults(func=cmd_db_status)

    p = sub.add_parser("breakglass", help="one-time emergency admin sign-in")
    p.add_argument("action", choices=["enable", "revoke"])
    p.add_argument("--minutes", type=int, default=None, help="how long the credential stays valid (default 5)")
    p.add_argument("--local-only", action="store_true", help="redeemable only from the host's loopback address")
    p.set_defaults(func=cmd_breakglass)

    p = sub.add_parser("bootstrap", help="create/update hosts, networks and role bindings from a JSON file")
    p.add_argument("file")
    p.set_defaults(func=cmd_bootstrap)

    p = sub.add_parser("maintenance", help="maintenance mode: on, off, status (used by the update agent)")
    p.add_argument("action", choices=["on", "off", "status"])
    p.add_argument("--reason", default="")
    p.add_argument("--source", default="admin")
    p.add_argument("--actor", default="update-agent")
    p.set_defaults(func=cmd_maintenance)

    p = sub.add_parser("backup", help="dump the database now and print the file's path")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("restore", help="restore a dump over the database (stop the API and worker first)")
    p.add_argument("file")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("rewrap-master", help="re-wrap the secret store's data keys to a new master key")
    p.add_argument("--from", dest="from_file", required=True)
    p.add_argument("--to", dest="to_file", required=True)
    p.set_defaults(func=cmd_rewrap_master)

    p = sub.add_parser("ca-repass", help="re-encrypt the CA's signing provisioner key with a new password")
    p.add_argument("--config", required=True)
    p.add_argument("--old", required=True)
    p.add_argument("--new", required=True)
    p.set_defaults(func=cmd_ca_repass)

    p = sub.add_parser("ca-sign", help="sign a CSR from stdin with the platform CA; print the chain")
    p.add_argument("--hours", type=int, default=720, help="validity, 1-2160 hours (default 720, 30 days)")
    p.add_argument("--with-root", action="store_true", help="also print the root certificate")
    p.set_defaults(func=cmd_ca_sign)

    p = sub.add_parser("push-vm-key", help="add a new VM SSH key on the deployed Linux VMs (the agent's rotation)")
    p.add_argument("--new", required=True, help="the new private key; its .pub beside it")
    p.set_defaults(func=cmd_push_vm_key)

    p = sub.add_parser("health", help="exit 0 when healthy (--deep: worker heartbeat and CA too)")
    p.add_argument("--deep", action="store_true")
    p.set_defaults(func=cmd_health)

    sub.add_parser("worker-key", help="print the SSH public key to authorize on remote KVM hosts"
                   ).set_defaults(func=cmd_worker_key)

    p = sub.add_parser("auth", help="sign-in providers")
    p.add_argument("action", choices=["status", "disable-provider", "enable-provider"])
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_auth)

    args = ap.parse_args(argv)
    if args.cmd == "auth" and args.action != "status" and not args.name:
        ap.error("auth disable-provider/enable-provider needs a provider name")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
