# Kerberos proof, client side: what a domain-joined Windows machine's browser needs to sign in to LABaPe
# with Windows credentials (design §58): the platform root CA trusted, and the stack's name in the Local
# intranet zone (and allowed to receive Kerberos tickets in Edge and Chrome). Idempotent.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = 'auth.labape.test'
$domain = 'labape.test'; $host1 = 'auth'

# 1. The platform root CA, fetched from the stack itself (that fetch can't be verified yet: it is how trust starts).
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Net.ServicePointManager]::ServerCertificateValidationCallback = { $true }
$pem = (New-Object Net.WebClient).DownloadString("https://$name/api/ca/root.crt")
[Net.ServicePointManager]::ServerCertificateValidationCallback = $null
$tmp = Join-Path $env:TEMP 'labape-root.crt'
Set-Content -Path $tmp -Value $pem -Encoding ascii
$cert = Import-Certificate -FilePath $tmp -CertStoreLocation Cert:\LocalMachine\Root
Remove-Item $tmp
"root CA: $($cert.Subject) trusted (thumbprint ending $($cert.Thumbprint.Substring(34)))"

# 2. Local intranet zone for the name (1 = intranet), for every user of this machine.
$zone = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\ZoneMap\Domains\$domain\$host1"
New-Item -Path $zone -Force | Out-Null
New-ItemProperty -Path $zone -Name https -Value 1 -PropertyType DWord -Force | Out-Null
"intranet zone: https://$name"

# 3. Edge and Chrome may send Kerberos tickets to the name.
foreach ($k in 'HKLM:\SOFTWARE\Policies\Microsoft\Edge', 'HKLM:\SOFTWARE\Policies\Google\Chrome') {
    New-Item -Path $k -Force | Out-Null
    New-ItemProperty -Path $k -Name AuthServerAllowlist -Value $name -PropertyType String -Force | Out-Null
}
'browser policy: AuthServerAllowlist set for Edge and Chrome'

# 4. Check: the name resolves, port 9445 answers, and the certificate now verifies.
"dns: $((Resolve-DnsName $name -Type A).IPAddress -join ',')"
"port 9445: $((Test-NetConnection $name -Port 9445 -WarningAction SilentlyContinue).TcpTestSucceeded)"
$r = & curl.exe -sS --ssl-no-revoke -o NUL -w '%{http_code} verify=%{ssl_verify_result}' "https://${name}:9445/" 2>&1
"https://${name}:9445/ -> $r (verify=0 means the certificate is trusted; the platform CA publishes no revocation list, so the check is skipped)"
