"""Secret health (S1): list each secret's check, or check them now.

Usage: secret_health.py [list] | check all|POLICY_ID ...
`check` queues Check now for each (all: every checkable one), waits for the jobs and prints the
results. Signs in as labape-admin.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")


def rows():
    return c.get(f"{LABAPE}/api/admin/secrets").json()["policies"]


def show(ps):
    for p in ps:
        every = f"every {p['check_every_hours']}h" if p["check_every_hours"] else "no check (rotates daily)"
        print(f"  #{p['id']} {p['label'][:50]:50} recoverable={'yes' if p['recoverable'] else 'no ':3} "
              f"{p['check_status']:10} {every:24} {p['check_error'][:150]}")


args = sys.argv[1:] or ["list"]
if args[0] == "check":
    ids = [p["id"] for p in rows() if p["checkable"]] if args[1:] == ["all"] else [int(x) for x in args[1:]]
    jobs = {}
    for i in ids:
        r = c.post(f"{LABAPE}/api/admin/secrets/{i}/check")
        if r.status_code == 200:
            jobs[i] = r.json()["job_id"]
        else:
            print(f"  #{i}: {r.status_code} {r.text[:150]}")
    t0 = time.time()
    while jobs and time.time() - t0 < 900:
        if all(c.get(f"{LABAPE}/api/jobs/{j}").json()["state"] not in ("queued", "running") for j in jobs.values()):
            break
        time.sleep(5)
    show([p for p in rows() if p["id"] in ids])
else:
    show(rows())
