"""secret_row.py KIND [rotate|check]: one platform secret's row (errors in full), or run rotate/check and wait."""
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
kind = sys.argv[1]
d = c.get(f"{LABAPE}/api/admin/secrets").json()
p = next(x for x in d["policies"] if x["kind"] == kind)
if len(sys.argv) > 2:
    r = c.post(f"{LABAPE}/api/admin/secrets/{p['id']}/{sys.argv[2]}")
    print(r.status_code, r.text[:300])
    if r.status_code == 200 and "job_id" in r.json():
        jid = r.json()["job_id"]
        for _ in range(60):
            j = c.get(f"{LABAPE}/api/jobs/{jid}").json()
            if j["state"] in ("succeeded", "failed", "cancelled"):
                print("job", jid, j["state"])
                print(job_log(c, jid)[-800:])
                break
            time.sleep(2)
    p = next(x for x in c.get(f"{LABAPE}/api/admin/secrets").json()["policies"] if x["kind"] == kind)
print({k: p[k] for k in ("kind", "status", "last_error", "check_status", "check_error", "last_rotated_at", "elevated_ref")})
