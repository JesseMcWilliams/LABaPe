"""VM snapshots (design §39): list, take, revert, delete, waiting for each job.

Usage: snapshots.py ENV_ID VM [list] | refresh | auto on|off | take NAME [DESCRIPTION] | revert NAME | delete NAME
Signs in as labape-admin.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
env, vm = sys.argv[1], sys.argv[2]
cmd = sys.argv[3] if len(sys.argv) > 3 else "list"
base = f"{LABAPE}/api/environments/{env}/vms/{vm}/snapshots"


def wait(r):
    if r.status_code != 200:
        sys.exit(f"{r.status_code}: {r.text[:300]}")
    jid = r.json()["job_id"]
    for _ in range(360):
        j = c.get(f"{LABAPE}/api/jobs/{jid}").json()
        if j["state"] in ("succeeded", "failed", "cancelled"):
            print(f"job {jid} {j['state']}")
            return
        time.sleep(5)


if cmd == "take":
    wait(c.post(base, json={"name": sys.argv[4], "description": " ".join(sys.argv[5:])}))
elif cmd == "revert":
    wait(c.post(f"{base}/{sys.argv[4]}/revert"))
elif cmd == "auto":                         # Settings → VM snapshots: on|off
    r = c.put(f"{LABAPE}/api/admin/settings/vm_snapshots", json={"auto_before_deploy": sys.argv[4] == "on", "keep_auto": 3})
    print("auto before deploy:", r.status_code, r.json().get("auto_before_deploy"))
elif cmd == "refresh":
    wait(c.post(f"{base}/refresh"))
elif cmd == "delete":
    wait(c.delete(f"{base}/{sys.argv[4]}"))
m = c.get(base).json()
print(f"supported={m['supported']} {m['reason']} can_operate={m['can_operate']}")
for s in m["snapshots"]:
    print(f"  {s['name']} {s['created']} {s['state']} current={s['current']} auto={s['auto']} by={s['created_by']} {s['description']}")
