"""List KVM hosts, or enable/disable one, through the API.

Usage: hosts.py list
       hosts.py enable|disable HOST_ID
       hosts.py rename HOST_ID NAME             (reuse a disabled test host's record)
       hosts.py attach HOST_ID NETWORK BRIDGE   (the host carries NETWORK on BRIDGE, as its default)
       hosts.py set HOST_ID key=value ...       (e.g. libvirt_uri=..., ssh_address=...)
       hosts.py network NAME CIDR GATEWAY POOL   (a static-only catalog network; POOL is first-last)
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
elif args[0] == "rename":
    h = c.get(f"{LABAPE}/api/hosts/{args[1]}").json()
    body = {k: v for k, v in h.items() if k not in ("id", "resources")}
    body["name"] = args[2]
    r = c.put(f"{LABAPE}/api/hosts/{args[1]}", json=body)
    print(r.status_code, r.text[:200] if r.status_code != 200 else f"host {args[1]} is now {args[2]}")
elif args[0] == "attach":
    r = c.put(f"{LABAPE}/api/hosts/{args[1]}/networks", json=[{"network": args[2], "bridge": args[3], "is_default": True}])
    print(r.status_code, r.text[:300])
elif args[0] == "set":
    h = c.get(f"{LABAPE}/api/hosts/{args[1]}").json()
    body = {k: v for k, v in h.items() if k not in ("id", "resources")}
    body.update(dict(a.split("=", 1) for a in args[2:]))
    r = c.put(f"{LABAPE}/api/hosts/{args[1]}", json=body)
    print(r.status_code, r.text[:200] if r.status_code != 200 else {k: r.json()[k] for k in ("name", "libvirt_uri", "ssh_address")})
elif args[0] == "network":
    body = {"name": args[1], "cidr": args[2], "gateway": args[3], "dns_servers": [args[3]],
            "addressing": ["static"], "static_pools": [args[4]], "dhcp_ranges": [], "reserved": []}
    r = c.post(f"{LABAPE}/api/networks", json=body)
    print(r.status_code, r.text[:300])
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
