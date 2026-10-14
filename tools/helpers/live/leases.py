"""Environment leases (design §40): show, set, and the settings.

Usage: leases.py ENV_ID [show] | days N | in-minutes N | clear
       leases.py settings on|off [grace=N] [after=destroy|keep] [remind=7,1]
Signs in as labape-admin (in-minutes uses the admin's exact end).
"""
import datetime as dt
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
if sys.argv[1] == "settings":
    body = {"enabled": sys.argv[2] == "on"}
    for kv in sys.argv[3:]:
        k, v = kv.split("=", 1)
        body[{"grace": "grace_days", "after": "after_grace", "remind": "remind_days"}[k]] = (
            [int(x) for x in v.split(",")] if k == "remind" else (int(v) if k == "grace" else v))
    r = c.put(f"{LABAPE}/api/admin/settings/leases", json=body)
    sys.exit(print(r.status_code, r.json()))
env = sys.argv[1]
cmd = sys.argv[2] if len(sys.argv) > 2 else "show"
if cmd == "days":
    print(c.post(f"{LABAPE}/api/environments/{env}/lease", json={"days": int(sys.argv[3])}).status_code)
elif cmd == "in-minutes":
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=int(sys.argv[3]))
    print(c.post(f"{LABAPE}/api/environments/{env}/lease", json={"until": until.isoformat()}).status_code)
elif cmd == "clear":
    print(c.post(f"{LABAPE}/api/environments/{env}/lease", json={"clear": True}).status_code)
e = c.get(f"{LABAPE}/api/environments/{env}").json()
print(f"{e['name']} {e['status']} lease={e['lease']}")
