"""Exercise maintenance mode.

Usage: maintenance.py on|check|off
  on     turn it on as labape-admin
  check  as labape-dev: a write is refused (503) and a read works; as admin:
         Back up now is refused too; prints the status and job counts
  off    turn it off
"""
import sys

from oidc_login import LABAPE, login

admin = login("labape-admin")
step = sys.argv[1]
if step == "on":
    r = admin.put(f"{LABAPE}/api/admin/maintenance", json={"active": True, "reason": "live test of maintenance mode"})
    print("on:", r.status_code, r.json()["active"])
elif step == "check":
    dev = login("labape-dev")
    r = dev.post(f"{LABAPE}/api/environments", json={"name": "mtest", "kvm_host_id": 1, "host_groups": []})
    print("dev write:", r.status_code, r.json().get("detail", "")[:90])
    print("dev read:", dev.get(f"{LABAPE}/api/environments").status_code, "health:", admin.get(f"{LABAPE}/api/health").json())
    print("backup now (admin):", admin.post(f"{LABAPE}/api/admin/backups").status_code)
    m = admin.get(f"{LABAPE}/api/maintenance").json()
    print("status:", m["active"], m["reason"], "queued:", m["queued_jobs"], "running:", m["running_jobs"])
elif step == "off":
    print("off:", admin.put(f"{LABAPE}/api/admin/maintenance", json={"active": False}).json()["active"])
else:
    raise SystemExit(__doc__)
