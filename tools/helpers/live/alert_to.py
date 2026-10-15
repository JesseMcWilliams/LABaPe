"""alert_to.py [ADDRESS,...]: show Settings -> Monitoring "Email alerts to"; with addresses, set it."""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
mon = c.get(f"{LABAPE}/api/admin/settings").json()["monitoring"]
if len(sys.argv) > 1:
    mon["email_to"] = [a for a in sys.argv[1].split(",") if a]
    r = c.put(f"{LABAPE}/api/admin/settings/monitoring", json=mon)
    print("save:", r.status_code, r.text[:200] if r.status_code != 200 else "")
    mon = c.get(f"{LABAPE}/api/admin/settings").json()["monitoring"]
print("alerts to:", mon["email_to"])
