[CmdletBinding()]
param(
    [string]$InterfaceAlias = "Ethernet",
    [switch]$ConfigureNetwork,
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"
$ProxyIp = "192.168.2.2"
$UpstreamIp = "192.168.2.3"
$Image = "rpm-edge-proxy:2.0.0"
$ProjectDir = Split-Path -Parent $PSScriptRoot

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window (Run as Administrator)."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is not installed or docker.exe is not on PATH."
}

$dockerOs = & docker info --format '{{.OSType}}'
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running."
}
$dockerOs = $dockerOs.Trim()
if ($dockerOs -ne "linux") {
    throw "Docker Desktop must be using Linux containers."
}

$adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction Stop
if ($adapter.Status -ne "Up") {
    throw "Network adapter '$InterfaceAlias' is not up."
}

$localAddress = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $ProxyIp -ErrorAction SilentlyContinue |
    Where-Object InterfaceAlias -eq $InterfaceAlias

if (-not $localAddress) {
    if (-not $ConfigureNetwork) {
        throw "$ProxyIp is not assigned to '$InterfaceAlias'. Re-run with -ConfigureNetwork after confirming that address is unused."
    }

    & ping.exe -n 1 -w 750 $ProxyIp | Out-Null
    $pingAnswered = $LASTEXITCODE -eq 0
    $neighbor = Get-NetNeighbor -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -IPAddress $ProxyIp `
        -ErrorAction SilentlyContinue |
        Where-Object State -NotIn @("Unreachable", "Incomplete")
    if ($pingAnswered -or $neighbor) {
        throw "Another device appears to own $ProxyIp. Resolve the address conflict before continuing."
    }

    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $ProxyIp -PrefixLength 24 | Out-Null
    Write-Host "Assigned $ProxyIp/24 to $InterfaceAlias."
}

$firewallName = "RPMEdgeProxy-TCP-1600"
$firewallRule = Get-NetFirewallRule -Name $firewallName -ErrorAction SilentlyContinue
if (-not $firewallRule) {
    New-NetFirewallRule `
        -Name $firewallName `
        -DisplayName "RPM Edge Proxy TCP 1600" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol TCP `
        -LocalAddress $ProxyIp `
        -LocalPort 1600 `
        -RemoteAddress LocalSubnet | Out-Null
} else {
    Set-NetFirewallRule -Name $firewallName -Enabled True -Action Allow | Out-Null
}

if (-not (Test-NetConnection -ComputerName $UpstreamIp -Port 1600 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Write-Warning "The RPM is not accepting TCP at ${UpstreamIp}:1600. The proxy will keep retrying after it starts."
}

Push-Location $ProjectDir
try {
    if (-not $NoBuild) {
        & docker compose build
        if ($LASTEXITCODE -ne 0) { throw "Docker image build failed." }
    } else {
        & docker image inspect $Image *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "$Image is not loaded. Build it or load the offline AMD64 archive before using -NoBuild."
        }
    }

    & docker compose up --detach --no-build --remove-orphans
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed to start the proxy." }
} finally {
    Pop-Location
}

Start-Sleep -Seconds 3
$health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9090/healthz" -TimeoutSec 3
if ($health.StatusCode -ne 200) {
    throw "The proxy process did not pass its health check."
}

try {
    Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:9090/readyz" -TimeoutSec 3 | Out-Null
    Write-Host "RPM Edge Proxy is ready at ${ProxyIp}:1600."
} catch {
    Write-Warning "The proxy is running but has not connected to the RPM yet."
    Invoke-RestMethod -Uri "http://127.0.0.1:9090/status" -TimeoutSec 3 |
        ConvertTo-Json -Depth 6
}
