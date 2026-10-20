"""Preview a redeploy (design §56.1) and print it; nothing is applied.

Usage: env_plan.py <environment name>
Signs in as labape-admin, queues the environment's plan job, waits for it and prints each VM's action and
the reasons, the counts, and whether the preview is stale.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
env = next(e for e in c.get(f"{LABAPE}/api/environments").json() if e["name"] == sys.argv[1])
r = c.post(f"{LABAPE}/api/environments/{env['id']}/plan")
print("queue plan:", r.status_code, r.json() if r.status_code != 200 else "")
if r.status_code != 200:
    sys.exit(1)
job = r.json()["job_id"]
t0 = time.time()
while time.time() - t0 < 900:
    state = c.get(f"{LABAPE}/api/jobs/{job}").json()["state"]
    if state not in ("queued", "running"):
        break
    time.sleep(5)
print(f"job {job}: {state} after {int(time.time() - t0)}s")
latest = c.get(f"{LABAPE}/api/environments/{env['id']}/plan").json()
plan = latest.get("plan") or {}
print("stale:", latest.get("stale"), "| counts:", plan.get("counts"), "| other changes:", plan.get("other_changes"))
for vm in plan.get("vms", []):
    print(f"  {vm['name']:12} {vm['action']:8} {'; '.join(vm['reasons'])}")
