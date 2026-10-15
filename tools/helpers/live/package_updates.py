"""Catalog → Updates (U1, design §50.7), as labape-admin.

Usage: package_updates.py [list] | check [SOURCE_NAME]
`check` runs the check (all enabled sources, or one), waits for the job, and prints each source's
newest version, what's kept and any problem.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
base = f"{LABAPE}/api/package-updates"


def show():
    data = c.get(base).json()
    print("schedule:", data["schedule"])
    for s in data["sources"]:
        kept = ", ".join(f"{h['version']}={h['name']}" for h in s.get("history") or [])
        print(f"  {s['name']:24} {s['kind']:10} {'on ' if s['enabled'] else 'off'} newest={s['last_version'] or '-':14} "
              f"kept=[{kept}] {('PROBLEM: ' + s['last_error'][:160]) if s['last_error'] else ''}")
    return data


data = show()
if sys.argv[1:2] == ["check"]:
    body = {}
    if len(sys.argv) > 2:
        body["source_id"] = next(s["id"] for s in data["sources"] if s["name"] == sys.argv[2])
    r = c.post(f"{base}/check", json=body)
    print("check:", r.status_code, r.text[:200])
    job = r.json().get("job_id")
    while job and c.get(f"{LABAPE}/api/jobs/{job}").json()["state"] in ("queued", "running"):
        time.sleep(10)
    if job:
        print("job", job, c.get(f"{LABAPE}/api/jobs/{job}").json()["state"])
        print("\n".join(line for line in job_log(c, job).splitlines() if not line.startswith("labape: downloading")
                        and " at " not in line)[-3000:])
    show()
