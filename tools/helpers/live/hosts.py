"""List KVM hosts, or enable/disable one, through the API.

Usage: hosts.py list
       hosts.py enable|disable HOST_ID
       hosts.py logfwd HOST_ID on|off           (send the host's logs to the stack's syslog collector)
Signs in as labape-admin. A disabled host takes no jobs and isn't collected.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
args = sys.argv[1:]
if args[0] == "list":
    for h in c.get(f"{LABAPE}/api/hosts").json():
        print(f"  #{h['id']} {h['name']} {h['libvirt_uri']} enabled={h['enabled']} access={h.get('access_status')}")
elif args[0] == "logfwd":
    r = c.post(f"{LABAPE}/api/hosts/{args[1]}/log-forwarding", json={"enabled": args[2] == "on"})
    j = r.json()
    print(r.status_code, j if r.status_code != 200 else
          f"{j['name']}: log_forward={j['log_forward']} until={j['log_forward_until']} error={j['log_forward_error'][:200]}")
else:
    h = c.get(f"{LABAPE}/api/hosts/{args[1]}").json()
    body = {k: v for k, v in h.items() if k not in ("id", "resources")}
    body["enabled"] = args[0] == "enable"
    r = c.put(f"{LABAPE}/api/hosts/{args[1]}", json=body)
    print(r.status_code, r.text[:300] if r.status_code != 200 else f"{h['name']} enabled={body['enabled']}")
