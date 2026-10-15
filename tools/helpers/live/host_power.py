"""host_power.py info|update|reboot HOST_ID: the host's pending OS updates, or queue an update/reboot job.

`info` is read-only. update and reboot start real jobs on a lab host: run them only on purpose.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd, host = sys.argv[1], sys.argv[2]
if cmd == "info":
    r = c.get(f"{LABAPE}/api/hosts/{host}/os-updates")
    d = r.json()
    print(r.status_code, {k: (v if k != "packages" else f"{len(v)} packages, first: {v[:3]}") for k, v in d.items()})
else:
    path = {"update": "os-update", "reboot": "reboot"}[cmd]
    r = c.post(f"{LABAPE}/api/hosts/{host}/{path}")
    print(r.status_code, r.text[:200])
