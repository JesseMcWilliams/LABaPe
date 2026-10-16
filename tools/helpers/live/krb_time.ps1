"dc utc now: $((Get-Date).ToUniversalTime().ToString('s'))"
(Get-ADUser svc-authentik -Properties msDS-KeyVersionNumber,msDS-SupportedEncryptionTypes,UserPrincipalName | Select-Object UserPrincipalName,'msDS-KeyVersionNumber','msDS-SupportedEncryptionTypes' | Format-List | Out-String)
w32tm /query /status | Select-String 'Source|Stratum'
