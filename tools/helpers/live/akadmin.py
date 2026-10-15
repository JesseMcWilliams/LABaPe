"""akadmin.py status|check|elevated NAME|rotate: the Authentik administrator's stored password (Secrets page), never printed.

status    the policy row (health, last rotated, elevated account)
check     run the health check now and wait for it
elevated  choose the elevated account that may reset akadmin's password
rotate    rotate it now (a real change in the lab's Authentik) and wait
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
data = c.get(f"{LABAPE}/api/admin/secrets").json()
row = next(p for p in data["policies"] if p["kind"] == "authentik_admin")
pid = row["id"]


def wait(job_id):
    for _ in range(60):
        j = c.get(f"{LABAPE}/api/jobs/{job_id}").json()
        if j["state"] in ("succeeded", "failed", "cancelled"):
            print("job", job_id, j["state"])
            print(job_log(c, job_id)[-600:])
            return
        time.sleep(2)
    print("job still running")


def show():
    r = c.get(f"{LABAPE}/api/admin/secrets/{pid}").json()
    print({k: r.get(k) for k in ("check_status", "check_error", "status", "last_rotated_at", "elevated_ref", "automatic", "store")})
    print("elevated accounts:", data["elevated"])


if cmd == "status":
    show()
elif cmd == "check":
    r = c.post(f"{LABAPE}/api/admin/secrets/{pid}/check")
    wait(r.json()["job_id"])
    show()
elif cmd == "elevated":
    body = {k: row[k] for k in ("enabled", "interval_hours", "managed_externally", "notes", "store", "params") if k in row}
    body["elevated_ref"] = sys.argv[2]
    r = c.put(f"{LABAPE}/api/admin/secrets/{pid}", json=body)
    print(r.status_code, r.text[:200] if r.status_code != 200 else "")
    show()
elif cmd == "rotate":
    r = c.post(f"{LABAPE}/api/admin/secrets/{pid}/rotate")
    print(r.status_code, r.text[:200])
    if r.status_code == 200 and "job_id" in r.json():
        wait(r.json()["job_id"])
    show()
