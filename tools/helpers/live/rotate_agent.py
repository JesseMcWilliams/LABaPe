"""Rotate a secret that the host's update agent handles, and wait for the result.

Usage: rotate_agent.py <kind>
  kind: master_key | postgres_password | ca_password   (restart the stack, phase R3)
        vault_password | bootstrap_ssh_key             (no restart; the worker once they are in the store, §26)
Signs in again on every poll, because a master-key rotation ends all sessions.
Afterwards it checks the secret store's self-test, directory search,
maintenance mode and /api/health.
"""
import sys
import time

import httpx

from oidc_login import LABAPE, login

kind = sys.argv[1]
admin = login("labape-admin")
pol = next(p for p in admin.get(f"{LABAPE}/api/admin/secrets").json()["policies"] if p["kind"] == kind)
print("before:", pol["status"], "last rotated", pol["last_rotated_at"])
r = admin.post(f"{LABAPE}/api/admin/secrets/{pol['id']}/rotate")
print("rotate now:", r.status_code, r.json())
s, deadline = admin, time.time() + 900
while time.time() < deadline:
    time.sleep(15)
    try:
        s = login("labape-admin")
        p = s.get(f"{LABAPE}/api/admin/secrets/{pol['id']}").json()
    except Exception as exc:  # noqa: BLE001 — the API restarts during R3 rotations
        print("  waiting:", type(exc).__name__)
        continue
    if p["status"] == "failed" or (p["status"] == "ok" and p["last_rotated_at"] != pol["last_rotated_at"]):
        print("after:", p["status"], p["last_rotated_at"], p["last_error"][:300])
        break
    print("  policy:", p["status"])
h = s.get(f"{LABAPE}/api/dashboard/admin").json()
print("secret store self-test:", [c["status"] for c in h["secrets"]])
print("directory search:", s.post(f"{LABAPE}/api/admin/settings/directory/test").json().get("ok"))
print("maintenance:", s.get(f"{LABAPE}/api/maintenance").json()["active"])
print("health:", httpx.get(f"{LABAPE}/api/health", verify=False).json())
