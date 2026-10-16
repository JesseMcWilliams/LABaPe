$ErrorActionPreference = 'Continue'
"os: $((Get-CimInstance Win32_OperatingSystem).Caption)  core: $(-not (Test-Path C:\Windows\explorer.exe))"
'--- sessions'
(qwinsta 2>&1 | Out-String)
'--- processes per session'
Get-Process -IncludeUserName -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -in 'explorer','winlogon','LogonUI','userinit','rdpclip','dwm','csrss' } | Select-Object ProcessName, SessionId, UserName | Sort-Object SessionId | Format-Table -AutoSize | Out-String
'--- RDP session manager events (today)'
Get-WinEvent -LogName 'Microsoft-Windows-TerminalServices-LocalSessionManager/Operational' -MaxEvents 14 -ErrorAction SilentlyContinue | ForEach-Object { "$($_.TimeCreated.ToString('s')) id=$($_.Id) $($_.Message -replace '\s+',' ')".Substring(0, [Math]::Min(200, ("$($_.TimeCreated.ToString('s')) id=$($_.Id) $($_.Message -replace '\s+',' ')").Length)) }
'--- RemoteConnectionManager'
Get-WinEvent -LogName 'Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational' -MaxEvents 8 -ErrorAction SilentlyContinue | ForEach-Object { $m = "$($_.TimeCreated.ToString('s')) id=$($_.Id) $($_.Message -replace '\s+',' ')"; $m.Substring(0, [Math]::Min(200, $m.Length)) }
'--- time / zone'
"$(Get-TimeZone | Select-Object -ExpandProperty Id)  $(Get-Date -Format s)"
