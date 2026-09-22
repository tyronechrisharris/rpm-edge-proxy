[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window (Run as Administrator)."
}

$dataDir = Join-Path $env:ProgramData "RPMEdgeProxy"
$statePath = Join-Path $dataDir "server-baseline.json"
$pausePath = Join-Path $dataDir "watchdog.pause"
$taskName = "RPM Edge Proxy Server Watchdog"
if (-not (Test-Path $statePath -PathType Leaf)) {
    throw "No saved server baseline exists at '$statePath'."
}

New-Item -Path $pausePath -ItemType File -Force | Out-Null
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
& shutdown.exe /a *> $null

$state = Get-Content $statePath -Raw | ConvertFrom-Json

if ($state.OriginalPowerScheme) {
    & powercfg.exe /setactive $state.OriginalPowerScheme | Out-Null
}
if ($state.ServerPowerScheme) {
    & powercfg.exe /delete $state.ServerPowerScheme | Out-Null
}

foreach ($entry in @($state.Registry)) {
    if ($entry.Exists) {
        New-Item -Path $entry.Path -Force | Out-Null
        New-ItemProperty -Path $entry.Path -Name $entry.Name -PropertyType $entry.Kind `
            -Value $entry.Value -Force | Out-Null
    } elseif (Test-Path $entry.Path) {
        Remove-ItemProperty -Path $entry.Path -Name $entry.Name -ErrorAction SilentlyContinue
    }
}

if ($state.AdapterPower) {
    $parameters = @{
        Name = $state.InterfaceAlias
        ErrorAction = "Stop"
    }
    if ($state.AdapterPower.SelectiveSuspend -in @("Enabled", "Disabled")) {
        $parameters.SelectiveSuspend = $state.AdapterPower.SelectiveSuspend
    }
    if ($state.AdapterPower.DeviceSleepOnDisconnect -in @("Enabled", "Disabled")) {
        $parameters.DeviceSleepOnDisconnect = $state.AdapterPower.DeviceSleepOnDisconnect
    }
    if ($parameters.Count -gt 2) {
        try { Set-NetAdapterPowerManagement @parameters | Out-Null } catch { Write-Warning $_.Exception.Message }
    }
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    & docker info *> $null
    if ($LASTEXITCODE -eq 0) {
        foreach ($container in @($state.ContainerRestartPolicies)) {
            & docker inspect $container.Name *> $null
            if ($LASTEXITCODE -eq 0) {
                $policy = if ($container.Policy) { $container.Policy } else { "no" }
                & docker update --restart $policy $container.Name | Out-Null
            }
        }
    }
}

$restoredStatePath = Join-Path $dataDir "server-baseline.restored.json"
Move-Item -Path $statePath -Destination $restoredStatePath -Force
Write-Host "Windows server-mode settings were restored from the saved baseline." -ForegroundColor Green
Write-Host "The recovery task was removed. Logs and the restored baseline remain in $dataDir."
Write-Host "Restart Windows to apply all restored browser, update, and power-throttling policies."
