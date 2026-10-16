$ErrorActionPreference = 'Continue'
"whoami: $(whoami)   computer: $env:COMPUTERNAME   domain joined: $((Get-CimInstance Win32_ComputerSystem).PartOfDomain) ($((Get-CimInstance Win32_ComputerSystem).Domain))"
'--- local users'
Get-LocalUser | Select-Object Name, Enabled, PasswordExpires, PasswordLastSet, LastLogon | Format-Table -AutoSize | Out-String
'--- Remote Desktop Users / Administrators members'
(Get-LocalGroupMember 'Remote Desktop Users' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ', '
(Get-LocalGroupMember 'Administrators' | Select-Object -ExpandProperty Name) -join ', '
'--- RDP settings'
"fDenyTSConnections: $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server').fDenyTSConnections)"
"UserAuthentication (NLA): $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp').UserAuthentication)"
"SecurityLayer: $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp').SecurityLayer)"
(Get-Service TermService, SessionEnv, UmRdpService | Select-Object Name, Status | Format-Table -AutoSize | Out-String)
'--- recent logon failures (4625) and RDP events'
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625} -MaxEvents 6 -ErrorAction SilentlyContinue | ForEach-Object {
  $x = [xml]$_.ToXml(); $d = @{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  "$($_.TimeCreated.ToString('s')) user=$($d.TargetDomainName)\$($d.TargetUserName) type=$($d.LogonType) status=$($d.Status) sub=$($d.SubStatus) from=$($d.IpAddress) proc=$($d.ProcessName)" }
'--- recent successful RDP-type logons (4624 type 10/3)'
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624} -MaxEvents 40 -ErrorAction SilentlyContinue | ForEach-Object {
  $x = [xml]$_.ToXml(); $d = @{}; $x.Event.EventData.Data | ForEach-Object { $d[$_.Name] = $_.'#text' }
  if ($d.LogonType -in '10','7') { "$($_.TimeCreated.ToString('s')) user=$($d.TargetDomainName)\$($d.TargetUserName) type=$($d.LogonType) from=$($d.IpAddress)" } } | Select-Object -First 6
