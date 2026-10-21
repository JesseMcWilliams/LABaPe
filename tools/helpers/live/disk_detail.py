"""A drive's page data (design §64): its current numbers, how many readings of history there are and when they moved.

Usage: disk_detail.py <host name> <drive: serial or sdX> [read]
`read` reads the host's drives now first (SMART over SSH, read-only), which adds a point to the history.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
host = next(h for h in c.get(f"{LABAPE}/api/hosts").json() if h["name"] == sys.argv[1])
if sys.argv[3:4] == ["read"]:
    print("read now:", c.post(f"{LABAPE}/api/hosts/{host['id']}/admin/refresh").status_code)
r = c.get(f"{LABAPE}/api/hosts/{host['id']}/disks/{sys.argv[2]}", params={"days": 365})
print("status", r.status_code)
if r.status_code == 200:
    j = r.json()
    d = j["disk"]
    print(f"{j['host']['name']} {d['device']} {d.get('model')} serial={'yes' if d.get('serial') else 'no'} passed={d.get('passed')} "
          f"reallocated={d.get('reallocated')} pending={d.get('pending')} uncorrectable={d.get('uncorrectable')} temp={d.get('temperature')}")
    print("attributes:", len(d.get("attributes") or []), "| readings:", len(j["samples"]), "| changes:", len(j["changes"]),
          "| alerts:", [a["message"] for a in j["alerts"]], "| layout:", (j["block"] or {}).get("name"), "| retention", j["retention_days"])
else:
    print(r.text[:300])
