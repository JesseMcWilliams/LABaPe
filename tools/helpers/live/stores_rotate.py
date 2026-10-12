"""Rotate LABaPe's own logins to the test stores and check them (design §25, P3).

Usage: stores_rotate.py [openbao|conjur ...]
Needs both stores set up in LABaPe (stores.py) and, for OpenBao, the policy
grant from tools/secrets-test/deploy.sh (re-run it as root after updating).
For each store: Rotate now on its policy, wait for the job, then run Test
connection and check the login LABaPe had before no longer signs in (it
compares against /secrets-test.env only while that copy was still current).
Once no login in that file signs in any more, it prints a marker and
lab-live.sh deletes the file.
Never prints a secret.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login
from labape.stores.conjur import ConjurSettings, ConjurStore
from labape.stores.openbao import OpenBaoSettings, OpenBaoStore

KINDS = {"openbao": "openbao_secret_id", "conjur": "conjur_api_key"}
wanted = sys.argv[1:] or list(KINDS)
try:      # deleted by lab-live.sh once LABaPe has rotated every login in it
    env = dict(line.strip().split("=", 1) for line in open("/secrets-test.env") if "=" in line and not line.startswith("#"))
except FileNotFoundError:
    env = {}
admin = login("labape-admin")
settings = admin.get(f"{LABAPE}/api/admin/settings").json()
root = admin.get(f"{LABAPE}/api/ca/root.crt").text
old_login = {
    "openbao": OpenBaoStore(OpenBaoSettings(enabled=True, url=settings["store_openbao"]["url"],
                                            role_id=settings["store_openbao"]["role_id"], ca_bundle=root),
                            env.get("OPENBAO_SECRET_ID", "")),
    "conjur": ConjurStore(ConjurSettings(enabled=True, url=settings["store_conjur"]["url"], ca_bundle=root),
                          env.get("CONJUR_HOST_API_KEY", "")),
}
old_login = {name: s for name, s in old_login.items() if s._credential}
file_current = {name: s.check_login(s._credential) for name, s in old_login.items()}

pols = {p["kind"]: p for p in admin.get(f"{LABAPE}/api/admin/secrets").json()["policies"]}
for name in wanted:
    p = pols.get(KINDS[name])
    if p is None:
        print(f"{name}: no {KINDS[name]} policy (is the store set up?)")
        continue
    r = admin.post(f"{LABAPE}/api/admin/secrets/{p['id']}/rotate")
    if r.status_code != 200:
        print(f"{name}: Rotate now refused: {r.status_code} {r.text[:200]}")
        continue
    job = r.json()["job_id"]
    while (j := admin.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
        time.sleep(2)
    after = admin.get(f"{LABAPE}/api/admin/secrets/{p['id']}").json()
    print(f"{name}: job {job} {j['state']}; policy {after['status']}, next {after['next_due_at']}",
          after["last_error"] or "")
    for line in job_log(admin, job).splitlines():
        if line.startswith("labape:"):
            print("   ", line)
    t = admin.post(f"{LABAPE}/api/admin/settings/stores/{name}/test").json()
    print(f"  test connection after rotating:", "ok" if t["ok"] else "FAILED", "-", t["message"])
    if file_current.get(name):
        s = old_login[name]
        print(f"  the previous login (from secrets-test.env) still signs in:", s.check_login(s._credential))
    else:
        print("  the previous login wasn't the file's copy; old-login check skipped")

if old_login and not any(s.check_login(s._credential) for s in old_login.values()):
    print("labape-live: secrets-test.env is stale")    # lab-live.sh deletes the file
