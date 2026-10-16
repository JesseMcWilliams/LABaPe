"zone: $((Get-TimeZone).Id)"
"utc now (guest): $((Get-Date).ToUniversalTime().ToString('s'))  local: $((Get-Date).ToString('s'))"
"RealTimeIsUniversal: $((Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation').RealTimeIsUniversal)"
