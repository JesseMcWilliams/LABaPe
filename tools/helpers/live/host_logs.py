"""host_logs.py [on|off HOST_ID]: which hosts forward their own logs to the stack syslog (and turn it on/off)."""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
if len(sys.argv) > 2:
    r = c.post(f"{LABAPE}/api/hosts/{sys.argv[2]}/log-forwarding", json={"enabled": sys.argv[1] == "on"})
    print(r.status_code, r.text[:200] if r.status_code != 200 else "")
for h in c.get(f"{LABAPE}/api/hosts").json():
    print(h["id"], h["name"], h.get("role"), "forwarding:", h.get("log_forward"), h.get("log_forward_error", "")[:80])
