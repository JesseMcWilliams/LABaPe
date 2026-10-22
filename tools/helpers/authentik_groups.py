#!/usr/bin/env python3
"""Create LABaPe's role groups in Authentik (the ones that are missing).

    AUTHENTIK_URL=https://auth.example AUTHENTIK_TOKEN=... python tools/helpers/authentik_groups.py [--dry-run]

The token is an Authentik API token that may create groups (an admin's; LABaPe's own Directory token only views
them). The group names are LABaPe's defaults (`DEFAULT_ROLE_MAP` in app/labape/config.py); a stack that changed
its role map says so with LABAPE_ROLE_MAP, which this reads too. `AUTHENTIK_INSECURE=1` skips certificate checks
for a lab whose CA this computer doesn't trust. It prints what it found and what it created; nothing is deleted
and members are not added (bind users in Authentik)."""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_GROUPS = [
    "labape-admins", "labape-template-editors", "labape-deployers", "labape-file-managers", "labape-viewers",
    "labape-auditors", "labape-console", "labape-ca-admins", "labape-ca-requesters", "labape-ca-auditors",
]


def groups_wanted() -> list[str]:
    raw = os.environ.get("LABAPE_ROLE_MAP", "").strip()
    if raw:
        try:
            return sorted(json.loads(raw))
        except ValueError:
            sys.exit("LABAPE_ROLE_MAP is not JSON")
    return DEFAULT_GROUPS


def call(method: str, url: str, token: str, ctx, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {url}: {exc.code} {exc.read().decode(errors='replace')[:300]}")


def main() -> int:
    base = os.environ.get("AUTHENTIK_URL", "").rstrip("/")
    token = os.environ.get("AUTHENTIK_TOKEN", "")
    if not base or not token:
        print(__doc__)
        return 2
    dry = "--dry-run" in sys.argv
    ctx = ssl.create_default_context()
    if os.environ.get("AUTHENTIK_INSECURE") == "1":
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    have = set()
    url = f"{base}/api/v3/core/groups/?page_size=200"
    while url:
        page = call("GET", url, token, ctx)
        have |= {g["name"] for g in page.get("results", [])}
        nxt = (page.get("pagination") or {}).get("next")
        url = f"{base}/api/v3/core/groups/?page_size=200&page={nxt}" if nxt else ""
    for name in groups_wanted():
        if name in have:
            print(f"exists   {name}")
        elif dry:
            print(f"would create {name}")
        else:
            call("POST", f"{base}/api/v3/core/groups/", token, ctx, {"name": name})
            print(f"created  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
