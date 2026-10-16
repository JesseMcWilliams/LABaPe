$ErrorActionPreference = 'Continue'
(Get-ADDomain | Select-Object DNSRoot, NetBIOSName, DomainMode | Format-List | Out-String)
'--- DNS zones'
Get-DnsServerZone | Select-Object ZoneName, ZoneType | Format-Table -AutoSize | Out-String
'--- A records in labape.test (non-DC)'
Get-DnsServerResourceRecord -ZoneName labape.test -RRType A | Select-Object HostName, @{n='IP';e={$_.RecordData.IPv4Address.IPAddressToString}} | Format-Table -AutoSize | Out-String
'--- AD users'
Get-ADUser -Filter * -Properties ServicePrincipalNames | Select-Object SamAccountName, Enabled, @{n='SPNs';e={$_.ServicePrincipalNames -join ','}} | Format-Table -AutoSize | Out-String
'--- Forwarders'
Get-DnsServerForwarder | Select-Object -ExpandProperty IPAddress | Out-String
