"""Run a PowerShell script on an environment's domain controller (WinRM), for lab proofs.

Usage: ad_run.py <environment id> <DC address> <script file in /tools> [secret name [user]]
The domain admin password is revealed through the API as labape-admin and used for
Administrator over WinRM (5986, basic, certificate not checked: the lab's self-signed one).
Prints the script's output (@@UTCNOW@@ in the script becomes the real UTC time); never prints the password.
"""
import datetime
import sys

import winrm

from oidc_login import LABAPE, login

env_id, dc, script = sys.argv[1], sys.argv[2], sys.argv[3]
secret = sys.argv[4] if len(sys.argv) > 4 else "domain_admin"
user = sys.argv[5] if len(sys.argv) > 5 else "Administrator"
c = login("labape-admin")
r = c.post(f"{LABAPE}/api/environments/{env_id}/secrets/reveal", json={"name": secret})
r.raise_for_status()
value = r.json()["value"]
session = winrm.Session(f"https://{dc}:5986/wsman", auth=(user, value),
                        transport="basic", server_cert_validation="ignore")
with open(f"/tools/{script}", encoding="utf-8") as f:
    text = f.read().replace("@@UTCNOW@@", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    res = session.run_ps(text)
print(res.std_out.decode(errors="replace"))
if res.std_err and not res.std_err.startswith(b"#< CLIXML"):
    print("STDERR:", res.std_err.decode(errors="replace")[:2000])
raise SystemExit(res.status_code)
