"""Point LABaPe's audit syslog (Settings → Syslog) at a collector and test it (design §21.5, §27).

Usage: syslog_settings.py show
       syslog_settings.py <host> [--port N] [--protocol udp|tcp|tls] [--facility F] [--no-verify] [--off]
Saves the Syslog section as labape-admin (enabled unless --off), then runs
Send test message and prints the result. TLS verifies the collector's
certificate against the system roots and the platform root unless --no-verify.
"""
import argparse
import sys

from oidc_login import LABAPE, login

admin = login("labape-admin")
cur = admin.get(f"{LABAPE}/api/admin/settings").json()["syslog"]
if sys.argv[1:2] == ["show"]:
    print({k: v for k, v in cur.items()})
    sys.exit(0)

ap = argparse.ArgumentParser()
ap.add_argument("host")
ap.add_argument("--port", type=int, default=None)
ap.add_argument("--protocol", choices=("udp", "tcp", "tls"), default="tls")
ap.add_argument("--facility", default=cur.get("facility", "local0"))
ap.add_argument("--no-verify", action="store_true")
ap.add_argument("--off", action="store_true")
o = ap.parse_args()
port = o.port or (6514 if o.protocol == "tls" else 514)
body = {**cur, "enabled": not o.off, "host": o.host, "port": port, "protocol": o.protocol,
        "facility": o.facility, "verify_tls": not o.no_verify}
r = admin.put(f"{LABAPE}/api/admin/settings/syslog", json=body)
print("save:", r.status_code, "" if r.status_code == 200 else r.text[:300])
if r.status_code == 200 and not o.off:
    t = admin.post(f"{LABAPE}/api/admin/settings/syslog/test")
    print("test:", t.status_code, t.json() if t.headers.get("content-type", "").startswith("application/json") else t.text[:300])
