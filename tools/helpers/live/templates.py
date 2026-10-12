"""Template settings and deletes through the API (Design_Base-Images.md §8).

Usage: templates.py settings                  print Settings → Templates
       templates.py delete <name> [--host ID]  ask to delete a template; if a
                                              job starts, wait and print its log
Signs in as labape-admin.
"""
import argparse
import json
import sys
import time

from oidc_login import LABAPE, job_log, login

ap = argparse.ArgumentParser()
ap.add_argument("action", choices=("settings", "delete"))
ap.add_argument("name", nargs="?")
ap.add_argument("--host", type=int, default=None)
o = ap.parse_args()

c = login("labape-admin")
if o.action == "settings":
    print(json.dumps(c.get(f"{LABAPE}/api/admin/settings").json()["templates"]))
    sys.exit(0)

host = o.host or c.get(f"{LABAPE}/api/hosts").json()[0]["id"]
match = [t for t in c.get(f"{LABAPE}/api/templates?kvm_host_id={host}").json() if t["name"] == o.name]
if not match:
    raise SystemExit(f"no template {o.name!r} on host {host}")
r = c.delete(f"{LABAPE}/api/templates/{match[0]['id']}")
print("delete:", r.status_code, r.text[:400])
if r.status_code != 200:
    sys.exit(0)
job = r.json()["job_id"]
while True:
    time.sleep(5)
    j = c.get(f"{LABAPE}/api/jobs/{job}").json()
    if j["state"] not in ("queued", "running"):
        break
print("job:", job, j["state"])
print(job_log(c, job).strip())
