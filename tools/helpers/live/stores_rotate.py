"""Rotate LABaPe's own logins to its secret stores and check them (design §25, P3).

Usage: stores_rotate.py [openbao|conjur ...]
For each store: Rotate now on its login policy, wait for the job, print its
labape: lines, then run Test connection. Never prints a secret.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

KINDS = {"openbao": "openbao_secret_id", "conjur": "conjur_api_key"}
wanted = sys.argv[1:] or list(KINDS)
admin = login("labape-admin")

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
