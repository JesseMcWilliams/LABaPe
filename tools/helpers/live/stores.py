"""Point LABaPe at the test OpenBao and Conjur and exercise both (design §25, P1).

Usage: stores.py
Reads LABaPe's logins for the test stores from /secrets-test.env (lab-live.sh
mounts ~/.config/labape/secrets-test.env there), saves them in Settings →
Secret stores, runs each Test connection through the API, then writes, reads,
lists and deletes a throwaway value in each store directly with the provider
classes, checking certificates against the platform root. STORE_HOST
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

r = admin.put(f"{LABAPE}/api/admin/settings/store_openbao",
              json={"enabled": True, "url": bao_url, "role_id": env["OPENBAO_ROLE_ID"], "secret_id": env["OPENBAO_SECRET_ID"]})
print("save openbao:", r.status_code, "has_secret_id:", r.json().get("has_secret_id"))
r = admin.put(f"{LABAPE}/api/admin/settings/store_conjur",
              json={"enabled": True, "url": conjur_url, "api_key": env["CONJUR_HOST_API_KEY"]})
print("save conjur:", r.status_code, "has_api_key:", r.json().get("has_api_key"))
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
    name, value = f"live-test/p1 check {secrets.token_hex(3)}", secrets.token_urlsafe(24)
    s.put(name, value)
    got = s.get(name) == value
    s.put(name, value + "2")
    replaced = s.get(name) == value + "2"
    listed = name in s.list("live-test/")
    s.delete(name)
    gone = s.get(name) is None and name not in s.list("live-test/")
    print(f"{s.name}: put/get {got}, replace {replaced}, list {listed}, delete {gone}")
