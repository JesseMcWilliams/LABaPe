"""Kerberos proof: from the DC, as an AD user, sign in to Authentik through its SPNEGO endpoint and finish the flow.

Usage: krb_negotiate.py <environment id> <DC address> <account secret name, e.g. account/jesse> <user, e.g. jesse> [url]
Runs `curl.exe --negotiate` in a scheduled task that runs as that user (a real logon, so Kerberos works), follows
the redirects, then runs the flow Authentik reached (its executor API) until it redirects or stops. Prints status
codes, final URLs and flow component names, never a password, cookie or token value.
"""
import sys

import winrm

from oidc_login import LABAPE, login

env_id, dc, secret, user = sys.argv[1:5]
url = sys.argv[5] if len(sys.argv) > 5 else "https://auth.labape.test:9443/source/kerberos/lab2-kerberos/"
admin = login("labape-admin")
base = f"{LABAPE}/api/environments/{env_id}/secrets/reveal"
dpw = admin.post(base, json={"name": "domain_admin"}).json()["value"]
upw = admin.post(base, json={"name": secret}).json()["value"]
s = winrm.Session(f"https://{dc}:5986/wsman", auth=("Administrator", dpw), transport="basic", server_cert_validation="ignore")
PS = r"""
$ErrorActionPreference = 'Stop'
$jar = 'C:\Users\Public\krb_jar.txt'; $out = 'C:\Users\Public\krb_out.txt'; $err = 'C:\Users\Public\krb_err.txt'
Remove-Item $jar, $out, $err -ErrorAction SilentlyContinue
function Run-AsUser($curlArgs) {
    Remove-Item $out, $err -ErrorAction SilentlyContinue
    $act = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument "/c curl.exe -k -sS $curlArgs > $out 2>$err"
    Register-ScheduledTask -TaskName krbtest -Action $act -User 'LABAPE\USERHERE' -Password 'PWHERE' -RunLevel Limited -Force | Out-Null
    Start-ScheduledTask krbtest
    $n = 0; while ((Get-ScheduledTask krbtest).State -ne 'Ready' -and $n++ -lt 30) { Start-Sleep 1 }
    Unregister-ScheduledTask krbtest -Confirm:$false
    $t = if (Test-Path $out) { Get-Content $out -Raw } else { '' }
    $e = if (Test-Path $err) { Get-Content $err -Raw } else { '' }
    if ($e) { "stderr: $e" }
    return $t
}
$w = '-w "codes=%{http_code} redirects=%{num_redirects} final=%{url_effective}"'
(Run-AsUser "-L --negotiate -u : --max-redirs 10 -c $jar -b $jar -o NUL $w URLHERE")
$final = ((Get-Content $out -Raw) -split 'final=')[-1].Trim()
if ($final -match '/if/flow/([^/]+)/\?(.*)$') {
    $slug = $Matches[1]; $q = [uri]::EscapeDataString($Matches[2])
    $exec = "https://auth.labape.test:9443/api/v3/flows/executor/$slug/?query=$q"
    for ($i = 1; $i -le 6; $i++) {
        $body = Run-AsUser "-L -c $jar -b $jar $exec"
        try { $j = $body | ConvertFrom-Json } catch { "step ${i}: not JSON: $($body.Substring(0, [Math]::Min(200, $body.Length)))"; break }
        "step ${i}: component=$($j.component) to=$($j.to) flow=$slug"
        if ($j.component -like '*redirect*' -or $j.component -eq 'ak-stage-access-denied') { break }
        if ($j.component -eq 'ak-stage-consent') { $null = Run-AsUser "-c $jar -b $jar -H 'Content-Type: application/json' -d '{`"component`":`"ak-stage-consent`"}' $exec" }
        elseif ($j.component -notlike 'ak-flow-*') { break }
    }
}
Remove-Item $jar, $out, $err -ErrorAction SilentlyContinue
"""
ps = PS.replace("URLHERE", url).replace("USERHERE", user).replace("PWHERE", upw.replace("'", "''"))
res = s.run_ps(ps)
print(res.std_out.decode(errors="replace"))
if res.std_err and not res.std_err.startswith(b"#< CLIXML"):
    print("STDERR:", res.std_err.decode(errors="replace")[:1500])
