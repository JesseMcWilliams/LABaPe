"""Show or rotate an environment's Windows passwords (phase R4).

Usage: env_password.py <environment id> list
       env_password.py <environment id> <windows_admin|domain_admin>
Rotating queues the job, waits, prints the job's labape: lines, the policies,
and the credentials sheet's section and line labels (never the values).
"""
import sys
import time

from oidc_login import LABAPE, login

admin = login("labape-admin")
env_id, kind = sys.argv[1], sys.argv[2]
for p in admin.get(f"{LABAPE}/api/environments/{env_id}/rotation").json()["policies"]:
    print(p["kind"], "on" if p["enabled"] else "off", f"{p['interval_hours']}h", p["status"],
          "last", p["last_rotated_at"], "next", p["next_due_at"])
if kind == "list":
    raise SystemExit
r = admin.post(f"{LABAPE}/api/environments/{env_id}/rotation/{kind}/rotate")
print("rotate now:", r.status_code, r.json())
job = r.json()["job_id"]
while (j := admin.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
    time.sleep(10)
print("job:", job, j["state"])
log = admin.get(f"{LABAPE}/api/jobs/{job}/log").text
print("\n".join(line for line in log.splitlines() if line.startswith("labape:"))[-1500:])
for p in admin.get(f"{LABAPE}/api/environments/{env_id}/rotation").json()["policies"]:
    print(p["kind"], p["status"], p["last_rotated_at"], p["last_error"][:200])
sheet = admin.get(f"{LABAPE}/api/environments/{env_id}/credentials").json()["sections"]
print("sheet:", [(s["title"], [i["label"] for i in s["items"]]) for s in sheet])
