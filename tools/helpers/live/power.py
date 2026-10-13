"""VM power through the API (design §28.3): show states, or start/shut down/restart/force off.

Usage: power.py <env id> vms
       power.py <env id> <start|shutdown|restart|force_off> [--vm NAME ...] [--force-after] [--timeout S]
       power.py <env id> order GROUP=ORDER[:agent|none[:DELAY]] ...
Runs the job as labape-admin, waits, and prints its labape: lines and the VM states after.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

env, what, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
c = login("labape-admin")


def states():
    r = c.get(f"{LABAPE}/api/environments/{env}/vms").json()
    for v in r["vms"]:
        print(f"  {v['name']:<12} order {v['start_order']}  {v['state']:<9} agent={v['responding']}")


if what == "vms":
    states()
    sys.exit(0)
if what == "order":
    groups = []
    for a in rest:
        name, spec = a.split("=", 1)
        parts = spec.split(":")
        groups.append({"name": name, "start_order": int(parts[0]), "start_wait": parts[1] if len(parts) > 1 else "agent",
                       "start_delay_seconds": int(parts[2]) if len(parts) > 2 else 0})
    print(c.put(f"{LABAPE}/api/environments/{env}/start-order", json={"groups": groups}).json())
    sys.exit(0)
vms = [rest[i + 1] for i, a in enumerate(rest) if a == "--vm"] or None
timeout = int(rest[rest.index("--timeout") + 1]) if "--timeout" in rest else 300
r = c.post(f"{LABAPE}/api/environments/{env}/power",
           json={"action": what, "vms": vms, "force_after_timeout": "--force-after" in rest, "timeout_seconds": timeout})
if r.status_code != 200:
    raise SystemExit(f"refused: {r.status_code} {r.text[:300]}")
job = r.json()["job_id"]
t0 = time.time()
while (j := c.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
    time.sleep(3)
print(f"job {job} {j['state']} in {time.time() - t0:.0f}s")
for line in job_log(c, job).splitlines():
    if line.startswith("labape:"):
        print("  ", line)
