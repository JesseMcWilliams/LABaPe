# LABaPe (V2, design section 49.3): one of this VM's secrets from OpenBao. Its own (owners stored it for this
# VM), else a shared one whose VM list names this VM. Signs in with the machine's "LABaPe host
# certificate", whose key only administrators can use: run it elevated.
function Get-LabapeSecret {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')]
        [string]$Name
    )
    $ErrorActionPreference = 'Stop'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $c = Get-Content 'C:\ProgramData\LABaPe\secret-client.json' -Raw | ConvertFrom-Json
    $cert = Get-ChildItem Cert:\LocalMachine\My |
        Where-Object { $_.FriendlyName -eq 'LABaPe host certificate' -and $_.HasPrivateKey } |
        Sort-Object NotAfter -Descending | Select-Object -First 1
    if (-not $cert) { throw "There's no LABaPe host certificate with its key on this VM." }
    $login = Invoke-RestMethod -Method Post -Uri "$($c.url)/v1/auth/cert/login" -Certificate $cert `
        -ContentType 'application/json' -Body (@{ name = $c.role } | ConvertTo-Json)
    $headers = @{ 'X-Vault-Token' = $login.auth.client_token }
    try {
        foreach ($path in "env/$($c.env)/vm/$($c.vm)/$Name", "env/$($c.env)/user/$Name") {
            try {
                $r = Invoke-RestMethod -Uri "$($c.url)/v1/$($c.mount)/data/$path" -Headers $headers
                return $r.data.data.value
            } catch { continue }
        }
        throw "$Name isn't there or this VM may not read it."
    } finally {
        try { Invoke-RestMethod -Method Post -Uri "$($c.url)/v1/auth/token/revoke-self" -Headers $headers | Out-Null } catch { }
    }
}
Export-ModuleMember -Function Get-LabapeSecret
