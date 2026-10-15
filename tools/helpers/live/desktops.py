"""Desktop sessions on an environment's Linux host groups (E3, design §50.5), as labape-admin.

Usage: desktops.py ENV_ID [GROUP xrdp,vnc|none]
Without a group, prints each group's desktops. With one, sets them (the next Redeploy sets them up).
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
env_id = sys.argv[1]
if len(sys.argv) > 3:
    kinds = [] if sys.argv[3] == "none" else sys.argv[3].split(",")
    r = c.put(f"{LABAPE}/api/environments/{env_id}/desktops", json={"groups": [{"name": sys.argv[2], "remote_desktop": kinds}]})
    print("set:", r.status_code, r.text[:300] if r.status_code != 200 else "")
for g in c.get(f"{LABAPE}/api/environments/{env_id}").json()["spec"]["host_groups"]:
    print(f"  {g['name']:12} {g['os']:24} {g.get('remote_desktop') or []}")
