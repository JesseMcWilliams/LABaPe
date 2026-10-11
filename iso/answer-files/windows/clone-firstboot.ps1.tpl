# First-logon network setup for a VM cloned from a Windows template,
# rendered by tofu/modules/vm/libvirt/main.tf and put on the clone's CD
# next to unattend.xml, which runs it as its first FirstLogonCommand.
# A script (not an inline command) so it can wait, verify and log:
# C:\Windows\Temp\labape-firstboot.log.
#
# The NIC is found by interface index, never by name: on a template built
# on other virtual hardware (Packer's QEMU VM) the clone's NIC is a new
# device ("Ethernet 2") and the old name stays with a hidden device. On a
# fresh clone Windows may still be installing that new NIC when first
# logon starts, so this waits for it, then retries until the address
# actually sticks. DNS: the network's dns_servers (default: the gateway).
Start-Transcript -Path C:\Windows\Temp\labape-firstboot.log -Append
%{ if addressing.mode == "static" ~}
$ip = '${addressing.address}'
$prefix = ${addressing.prefix_length}
$gateway = '${addressing.gateway}'
$dns = @(${join(", ", [for d in addressing.dns : "'${d}'"])})

for ($try = 1; $try -le 10; $try++) {
  $adapter = $null
  for ($n = 0; $n -lt 150 -and -not $adapter; $n++) {
    $adapter = Get-NetAdapter -Physical | Where-Object Status -eq Up | Sort-Object ifIndex | Select-Object -First 1
    if (-not $adapter) { Start-Sleep -Seconds 2 }
  }
  if (-not $adapter) { Write-Output "no physical adapter came up"; break }
  $i = $adapter.ifIndex
  Write-Output "attempt $try on $($adapter.Name) (ifIndex $i)"
  try {
    Set-NetIPInterface -InterfaceIndex $i -Dhcp Disabled -ErrorAction Stop
    Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetRoute -InterfaceIndex $i -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
    New-NetIPAddress -InterfaceIndex $i -IPAddress $ip -PrefixLength $prefix -DefaultGateway $gateway -ErrorAction Stop | Out-Null
    Set-DnsClientServerAddress -InterfaceIndex $i -ServerAddresses $dns -ErrorAction Stop
  } catch {
    Write-Output "attempt $try failed: $_"
  }
  Start-Sleep -Seconds 5
  if (Get-NetIPAddress -InterfaceIndex $i -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object IPAddress -eq $ip) {
    Write-Output "address $ip/$prefix is set"
    break
  }
}
Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias, IPAddress, PrefixOrigin | Out-String | Write-Output
%{ else ~}
Write-Output "DHCP addressing: nothing to do"
%{ endif ~}
Stop-Transcript
