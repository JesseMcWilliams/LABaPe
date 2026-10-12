"""Point LABaPe at the test OpenBao and Conjur and exercise both (design §25, P1).

Usage: stores.py
Reads LABaPe's logins for the test stores from /secrets-test.env (lab-live.sh
mounts ~/.config/labape/secrets-test.env there) and saves them in Settings →
Secret stores, unless LABaPe's saved ones already work (they're rotated, so
the file's copies go stale). Runs each Test connection through the API, then,
while the file's logins still work, writes, reads, lists and deletes a
throwaway value in each store directly with the provider classes, checking
certificates against the platform root. STORE_HOST
overrides the stores' address (default: the LABaPe host). Never prints a secret.
"""
import os
import secrets
from urllib.parse import urlparse

import httpx

from oidc_login import LABAPE, login
from labape.stores.conjur import ConjurSettings, ConjurStore
from labape.stores.openbao import OpenBaoSettings, OpenBaoStore

env = dict(line.strip().split("=", 1) for line in open("/secrets-test.env") if "=" in line and not line.startswith("#"))
host = os.environ.get("STORE_HOST", urlparse(LABAPE).hostname)
bao_url, conjur_url = f"https://{host}:8200", f"https://{host}:8443"
admin = login("labape-admin")

# LABaPe rotates these logins (design §25, P3), after which the copies in
# secrets-test.env are stale: only save them while LABaPe's own don't work.
creds = {"openbao": {"url": bao_url, "role_id": env["OPENBAO_ROLE_ID"], "secret_id": env["OPENBAO_SECRET_ID"]},
         "conjur": {"url": conjur_url, "api_key": env["CONJUR_HOST_API_KEY"]}}
for name, body in creds.items():
    if admin.post(f"{LABAPE}/api/admin/settings/stores/{name}/test").json()["ok"]:
        print(f"{name}: LABaPe's saved login works; not replacing it")
        continue
    cur = admin.get(f"{LABAPE}/api/admin/settings").json()[f"store_{name}"]
    r = admin.put(f"{LABAPE}/api/admin/settings/store_{name}", json={**cur, "enabled": True, **body})
    print(f"save {name}:", r.status_code)
for name in ("openbao", "conjur"):
    t = admin.post(f"{LABAPE}/api/admin/settings/stores/{name}/test").json()
    print(f"test {name}:", "ok" if t["ok"] else "FAILED", "-", t["message"])
checks = {c["name"]: c for c in admin.get(f"{LABAPE}/api/dashboard/admin").json()["secrets"]}
for name in ("store.openbao", "store.conjur"):
    print(f"dashboard {name}:", checks.get(name, {}).get("status", "missing"))

root = httpx.get(f"{LABAPE}/api/ca/root.crt", verify=False).text
stores = [OpenBaoStore(OpenBaoSettings(enabled=True, url=bao_url, role_id=env["OPENBAO_ROLE_ID"], ca_bundle=root),
                       env["OPENBAO_SECRET_ID"]),
          ConjurStore(ConjurSettings(enabled=True, url=conjur_url, ca_bundle=root), env["CONJUR_HOST_API_KEY"])]
for s in stores:
    if not s.check_login(s._credential):
        print(f"{s.name}: the login in secrets-test.env was rotated by LABaPe; direct checks skipped")
        continue
    name, value = f"live-test/p1 check {secrets.token_hex(3)}", secrets.token_urlsafe(24)
    s.put(name, value)
    got = s.get(name) == value
    s.put(name, value + "2")
    replaced = s.get(name) == value + "2"
    listed = name in s.list("live-test/")
    s.delete(name)
    gone = s.get(name) is None and name not in s.list("live-test/")
    print(f"{s.name}: put/get {got}, replace {replaced}, list {listed}, delete {gone}")
