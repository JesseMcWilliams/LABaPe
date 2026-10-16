# libvirt gives Windows guests a localtime clock; the guests' zone is UTC, so they run 7 h behind real time.
# Set the zone to the host's (Pacific), then set the real time so the underlying UTC clock is right too.
Set-TimeZone -Id 'Pacific Standard Time'
Set-Date -Date ([datetime]::Parse('@@UTCNOW@@')) | Out-Null
"now utc: $((Get-Date).ToUniversalTime().ToString('s'))  zone: $((Get-TimeZone).Id)"
if (Get-Command Set-ADUser -ErrorAction SilentlyContinue) {
    Set-ADUser svc-authentik -UserPrincipalName 'svc-authentik@labape.test'
    'upn reset'
}
