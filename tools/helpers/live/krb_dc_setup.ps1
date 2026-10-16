# Kerberos proof, DC side: a DNS name for Authentik, a service account holding the HTTP/ SPN,
# and its keytab (random password, AES256 only) left at C:\Windows\Temp\authentik.keytab.
# Idempotent: re-running resets the account's password and rewrites the keytab.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$fqdn = 'auth.labape.test'
$ip = '172.21.49.100'
$realm = 'LABAPE.TEST'
$acct = 'svc-authentik'

if (-not (Get-DnsServerResourceRecord -ZoneName labape.test -Name auth -RRType A -ErrorAction SilentlyContinue)) {
    Add-DnsServerResourceRecordA -ZoneName labape.test -Name auth -IPv4Address $ip
    "dns: added $fqdn -> $ip"
} else { "dns: $fqdn exists" }

if (-not (Get-ADUser -Filter "SamAccountName -eq '$acct'")) {
    $pw = ConvertTo-SecureString ([guid]::NewGuid().ToString() + 'aA1!') -AsPlainText -Force
    New-ADUser -Name $acct -SamAccountName $acct -AccountPassword $pw -Enabled $true -PasswordNeverExpires $true `
        -Description 'LABaPe Kerberos proof: Authentik SPNEGO service account'
    "account: created $acct"
} else { "account: $acct exists" }
Set-ADUser $acct -Replace @{'msDS-SupportedEncryptionTypes' = 24}

$kt = 'C:\Windows\Temp\authentik.keytab'
Remove-Item $kt -ErrorAction SilentlyContinue
$ErrorActionPreference = 'Continue'
$out = & ktpass.exe -princ "HTTP/$fqdn@$realm" -mapuser "LABAPE\$acct" +rndPass -crypto AES256-SHA1 `
    -ptype KRB5_NT_PRINCIPAL -mapOp set -out $kt 2>&1 | Out-String
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $kt)) { throw "ktpass failed: $out" }
"keytab: $((Get-Item $kt).Length) bytes"
(setspn -L $acct) -join "`n"
