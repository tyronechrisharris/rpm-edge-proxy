[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet"
)

$ErrorActionPreference = "Stop"
$ProxyIp = "192.168.2.2"
$UpstreamIp = "192.168.2.3"

$localAddress = Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 `
    -IPAddress $ProxyIp -ErrorAction SilentlyContinue
if (-not $localAddress) {
    throw "$ProxyIp is not assigned to '$InterfaceAlias'."
}

$containerState = & docker inspect --format '{{.State.Status}}' rpm-edge-proxy 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "The rpm-edge-proxy container is not running."
}
$containerState = $containerState.Trim()
if ($containerState -ne "running") {
    throw "The rpm-edge-proxy container state is '$containerState', not 'running'."
}

if (-not (Test-NetConnection -ComputerName $UpstreamIp -Port 1600 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "The RPM is not reachable at ${UpstreamIp}:1600."
}
if (-not (Test-NetConnection -ComputerName $ProxyIp -Port 1600 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "The proxy listener is not reachable at ${ProxyIp}:1600."
}

Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9090/readyz" -TimeoutSec 3 | Out-Null
$status = Invoke-RestMethod -Uri "http://127.0.0.1:9090/status" -TimeoutSec 3
$status | ConvertTo-Json -Depth 6
Write-Host "Verification passed."
