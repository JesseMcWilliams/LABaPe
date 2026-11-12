"""B2 check: a Windows VM's WinRM listener answers with its platform certificate and LABaPe's commands validate it.

Usage: winrm_ca_check.py <environment name> <windows vm> [--renew]
Signs in as labape-admin, makes a 1-day console token limited to that environment, runs a command on the VM
(`COMPUTERNAME` and the listener's thumbprint against the machine certificate), and with --renew forces the
renewal task's script (`renew.ps1 -Force`), which must move the listener to the renewed certificate, then
runs the command again. Read-only apart from the renewal. The token is revoked at the end and never printed.
"""
import sys

import httpx

from oidc_login import LABAPE, login

env_name, vm = sys.argv[1], sys.argv[2]
renew = "--renew" in sys.argv
c = login("labape-admin")
env = next(e for e in c.get(f"{LABAPE}/api/environments").json() if e["name"] == env_name)
t = c.post(f"{LABAPE}/api/tokens", json={"name": "winrm-ca-check", "roles": ["console"], "days": 1,
                                         "environments": [env["id"]], "require_account": False}).json()
raw = httpx.Client(verify=False, timeout=200, headers={"Authorization": f"Bearer {t['secret']}"})
url = f"{LABAPE}/api/environments/{env['id']}/vms/{vm}/exec"
PROBE = ("$c = Get-ChildItem Cert:\\LocalMachine\\My | Where-Object { $_.FriendlyName -eq 'LABaPe host certificate' } | "
         "Sort-Object NotAfter -Descending | Select-Object -First 1; "
         "$l = Get-WSManInstance -ResourceURI winrm/config/Listener -SelectorSet @{Address='*';Transport='HTTPS'}; "
         "\"$env:COMPUTERNAME cert=$($c.Thumbprint) listener=$($l.CertificateThumbprint) match=$($c.Thumbprint -eq $l.CertificateThumbprint) until=$($c.NotAfter)\"")
try:
    def run(label, command, timeout=60):
        r = raw.post(url, json={"command": command, "timeout": timeout})
        j = r.json()
        print(f"{label}: {r.status_code}", (j.get("stdout") or j.get("detail") or j).strip() if isinstance(j, dict) else j)
        return j
    run("probe", PROBE)
    if renew:
        run("renew", "powershell -NoProfile -ExecutionPolicy Bypass -File C:\\ProgramData\\LABaPe\\tls\\renew.ps1 -Force", 120)
        run("probe after renewal", PROBE)
finally:
    c.delete(f"{LABAPE}/api/tokens/{t['id']}")
