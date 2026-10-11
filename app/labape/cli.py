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

    sub.add_parser("init-db", help="create database tables").set_defaults(func=cmd_init_db)

    p = sub.add_parser("breakglass", help="one-time emergency admin sign-in")
    p.add_argument("action", choices=["enable", "revoke"])
    p.add_argument("--minutes", type=int, default=None, help="how long the credential stays valid (default 5)")
    p.add_argument("--local-only", action="store_true", help="redeemable only from the host's loopback address")
    p.set_defaults(func=cmd_breakglass)

    p = sub.add_parser("bootstrap", help="create/update hosts, networks and role bindings from a JSON file")
    p.add_argument("file")
    p.set_defaults(func=cmd_bootstrap)

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
